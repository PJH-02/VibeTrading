"""Tests for the read-only documentation hygiene verifier."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.refactoring import docs_hygiene

REPO_ROOT = Path(__file__).resolve().parents[2]

AUTHORITY_PATHS = [
    "docs/final/01_FINAL_PLAN.md",
    "docs/final/02_DEVELOPER_TICKETS.md",
    "docs/final/03_ARCHITECTURE_REQUIREMENTS.md",
    "docs/final/04_COMPLETED_MANIFEST.json",
    "docs/final/05_TASK_STATUS.json",
]
SCRIPT = REPO_ROOT / "scripts" / "refactoring" / "docs_hygiene.py"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_file(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "worker@example.invalid")
    git(repo, "config", "user.name", "Worker")
    write_file(repo, "seed.txt", b"seed\n")
    git(repo, "add", "seed.txt")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def commit_file(repo: Path, relative: str, data: bytes, message: str = "base") -> str:
    write_file(repo, relative, data)
    git(repo, "add", relative)
    git(repo, "commit", "-q", "-m", message)
    return head(repo)


def archive_repo(
    tmp_path: Path, data: bytes = b"old raw\n", *, remove_working: bool = True
) -> tuple[Path, str, bytes]:
    repo = init_repo(tmp_path)
    base = commit_file(repo, "raw/old.md", data)
    if remove_working:
        git(repo, "rm", "-q", "--", "raw/old.md")
    else:
        git(repo, "rm", "-q", "--cached", "--", "raw/old.md")
    return repo, base, data


def make_entry(path: str, action: str, data: bytes, **overrides) -> dict:
    entry = {
        "path": path,
        "action": action,
        "sha256": digest(data),
        "bytes": len(data),
        "reason": "documented retention decision",
        "callers": ["no active code caller; receipt and path inventory checked"],
        "expiry": "2099-12-31",
    }
    entry.update(overrides)
    return entry


def make_manifest(
    base_sha: str,
    entries: list[dict],
    receipts: list[dict] | None = None,
    authorities: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "base_sha": base_sha,
        "entries": entries,
        "protected_receipts": receipts or [],
        "authority_paths": list(
            AUTHORITY_PATHS if authorities is None else authorities
        ),
    }


def make_backup(tmp_path: Path, relative: str, data: bytes) -> Path:
    backup = tmp_path / "backup"
    backup.mkdir(exist_ok=True)
    write_file(backup, relative, data)
    return backup


def snapshot(root: Path) -> dict:
    state = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            state[relative] = digest(path.read_bytes())
        elif path.is_dir():
            state[relative] = "dir"
    return state


def reasons(result: dict) -> str:
    return " | ".join(error["reason"] for error in result["errors"])


def test_valid_keep_and_protected_receipt(tmp_path):
    repo = init_repo(tmp_path)
    data = b"current authority\n"
    write_file(repo, "docs/keep.md", data)
    receipt = b'{"status":"accepted"}\n'
    write_file(repo, "receipts/receipt-1.json", receipt)
    manifest = make_manifest(
        head(repo),
        [make_entry("docs/keep.md", "KEEP", data)],
        receipts=[
            {
                "task_id": "TASK-1",
                "path": "receipts/receipt-1.json",
                "sha256": digest(receipt),
            }
        ],
    )
    original = copy.deepcopy(manifest)
    before = snapshot(repo)

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"] == []
    assert result["ok"] is True
    assert result["valid"] is True
    assert result["counts"]["keep"] == 1
    assert result["counts"]["passed"] == 1
    assert result["counts"]["protected_receipts"] == 1
    assert result["counts"]["authority_paths"] == 5
    assert manifest == original
    assert snapshot(repo) == before


def test_archive_git_untracked_with_local_copy(tmp_path):
    repo, base, data = archive_repo(tmp_path, remove_working=False)
    backup = make_backup(tmp_path, "raw/old.md", data)
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", data, backup_path="raw/old.md"
            )
        ],
    )
    before = snapshot(repo)

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"] == []
    assert result["counts"]["archive_git"] == 1
    assert (repo / "raw/old.md").is_file()
    assert snapshot(repo) == before


def test_archive_git_fresh_copy_absent(tmp_path):
    repo, base, data = archive_repo(tmp_path, remove_working=True)
    backup = make_backup(tmp_path, "raw/old.md", data)
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", data, backup_path="raw/old.md"
            )
        ],
    )

    assert not (repo / "raw/old.md").exists()
    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"] == []


def test_archive_requires_missing_and_mismatched_backup(tmp_path):
    repo, base, data = archive_repo(tmp_path)
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", data, backup_path="raw/old.md"
            )
        ],
    )

    missing_root = docs_hygiene.verify(repo, manifest)
    assert missing_root["errors"]
    assert "backup root required" in reasons(missing_root)

    empty_backup = tmp_path / "backup"
    empty_backup.mkdir()
    missing_file = docs_hygiene.verify(repo, manifest, empty_backup)
    assert "backup file missing" in reasons(missing_file)

    wrong_backup = make_backup(tmp_path, "raw/old.md", b"wrong bytes\n")
    wrong = docs_hygiene.verify(repo, manifest, wrong_backup)
    assert "backup" in reasons(wrong)
    assert "mismatch" in reasons(wrong)


def test_unsafe_backup_path_rejected(tmp_path):
    repo, base, data = archive_repo(tmp_path)
    write_file(tmp_path, "outside.bin", data)
    backup = tmp_path / "backup"
    backup.mkdir()
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md",
                "ARCHIVE_GIT",
                data,
                backup_path="../outside.bin",
            )
        ],
    )

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"]
    assert "backup_path" in reasons(result)


def test_archive_git_base_blob_mismatch(tmp_path):
    repo, base, _base_data = archive_repo(tmp_path, data=b"base bytes\n")
    expected = b"different bytes\n"
    backup = make_backup(tmp_path, "raw/old.md", expected)
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", expected, backup_path="raw/old.md"
            )
        ],
    )

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"]
    assert "base blob" in reasons(result)
    assert "mismatch" in reasons(result)


def test_archive_git_rejected_when_still_tracked(tmp_path):
    repo = init_repo(tmp_path)
    data = b"still tracked\n"
    base = commit_file(repo, "raw/old.md", data)
    backup = make_backup(tmp_path, "raw/old.md", data)
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", data, backup_path="raw/old.md"
            )
        ],
    )

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"]
    assert "still tracked" in reasons(result)


def test_protected_receipt_symlink_rejected(tmp_path):
    repo = init_repo(tmp_path)
    data = b"receipt target\n"
    write_file(repo, "receipts/real.json", data)
    (repo / "receipts" / "link.json").symlink_to("real.json")
    manifest = make_manifest(
        head(repo),
        [],
        receipts=[
            {
                "task_id": "TASK-LINK",
                "path": "receipts/link.json",
                "sha256": digest(data),
            }
        ],
    )

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "protected receipt symlink" in reasons(result)


def test_missing_protected_receipt(tmp_path):
    repo = init_repo(tmp_path)
    expected = b"not present\n"
    manifest = make_manifest(
        head(repo),
        [],
        receipts=[
            {
                "task_id": "TASK-4",
                "path": "receipts/missing.json",
                "sha256": digest(expected),
            }
        ],
    )

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "protected receipt file missing" in reasons(result)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/absolute.md",
        "../escape.md",
        "a/../b.md",
        "a/./b.md",
        "a//b.md",
        "a\\b.md",
        ".",
        "..",
        "",
    ],
)
def test_unsafe_paths_rejected(tmp_path, bad_path):
    repo = init_repo(tmp_path)
    manifest = make_manifest(
        head(repo),
        [make_entry(bad_path, "KEEP", b"x\n")],
    )

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "path" in reasons(result)


def test_duplicate_manifest_paths_rejected(tmp_path):
    repo = init_repo(tmp_path)
    data = b"duplicate\n"
    write_file(repo, "docs/keep.md", data)
    manifest = make_manifest(
        head(repo),
        [
            make_entry("docs/keep.md", "KEEP", data),
            make_entry("docs/keep.md", "KEEP", data),
        ],
    )

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "duplicate manifest path" in reasons(result)


def test_symlink_file_rejected(tmp_path):
    repo = init_repo(tmp_path)
    data = b"real target\n"
    write_file(repo, "docs/real.md", data)
    (repo / "docs" / "link.md").symlink_to("real.md")
    manifest = make_manifest(
        head(repo),
        [make_entry("docs/link.md", "KEEP", data)],
    )

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "symlink" in reasons(result)


def test_symlink_ancestor_rejected(tmp_path):
    repo = init_repo(tmp_path)
    data = b"real target\n"
    write_file(repo, "actual/file.md", data)
    (repo / "linked").symlink_to("actual", target_is_directory=True)
    manifest = make_manifest(
        head(repo),
        [make_entry("linked/file.md", "KEEP", data)],
    )

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "symlink" in reasons(result)


def test_symlink_root_rejected(tmp_path):
    repo = init_repo(tmp_path)
    manifest = make_manifest(head(repo), [])
    linked_root = tmp_path / "root-link"
    linked_root.symlink_to(repo, target_is_directory=True)

    result = docs_hygiene.verify(linked_root, manifest)

    assert result["errors"]
    assert "root symlink" in reasons(result)


def test_symlink_backup_root_rejected(tmp_path):
    repo, base, data = archive_repo(tmp_path)
    backup = make_backup(tmp_path, "raw/old.md", data)
    linked_backup = tmp_path / "backup-link"
    linked_backup.symlink_to(backup, target_is_directory=True)
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", data, backup_path="raw/old.md"
            )
        ],
    )

    result = docs_hygiene.verify(repo, manifest, linked_backup)

    assert result["errors"]
    assert "backup root symlink" in reasons(result)


def test_symlink_backup_file_rejected(tmp_path):
    repo, base, data = archive_repo(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    write_file(backup, "other.bin", data)
    link = backup / "raw" / "old.md"
    link.parent.mkdir(parents=True)
    link.symlink_to("../../other.bin")
    manifest = make_manifest(
        base,
        [
            make_entry(
                "raw/old.md", "ARCHIVE_GIT", data, backup_path="raw/old.md"
            )
        ],
    )

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"]
    assert "backup symlink" in reasons(result)


def test_malformed_manifest_fields_do_not_raise(tmp_path):
    repo = init_repo(tmp_path)
    data = b"x\n"
    write_file(repo, "docs/keep.md", data)
    valid = make_manifest(
        head(repo),
        [make_entry("docs/keep.md", "KEEP", data)],
    )

    cases = []
    bad = copy.deepcopy(valid)
    bad["schema_version"] = 2
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["base_sha"] = "A" * 40
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["entries"][0]["sha256"] = "ABC"
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["entries"][0]["action"] = "DELETE"
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["entries"][0]["bytes"] = "1"
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["entries"][0]["reason"] = " "
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["entries"][0]["callers"] = []
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["entries"][0]["expiry"] = ""
    cases.append(bad)
    bad = copy.deepcopy(valid)
    bad["authority_paths"] = AUTHORITY_PATHS[:4]
    cases.append(bad)
    cases.append([])

    for manifest in cases:
        result = docs_hygiene.verify(repo, manifest)
        assert result["errors"]
        assert result["ok"] is False


def test_index_update_current_hash_and_original_backup(tmp_path):
    repo = init_repo(tmp_path)
    original = b"old index\n"
    base = commit_file(repo, "docs/index.md", original)
    current = b"new current index\n"
    write_file(repo, "docs/index.md", current)
    backup = make_backup(tmp_path, "docs/index.md", original)
    entry = make_entry(
        "docs/index.md",
        "INDEX_UPDATE",
        original,
        current_sha256=digest(current),
        backup_path="docs/index.md",
    )
    manifest = make_manifest(base, [entry])

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"] == []
    assert result["counts"]["index_update"] == 1


def test_index_update_current_hash_mismatch(tmp_path):
    repo = init_repo(tmp_path)
    original = b"old index\n"
    base = commit_file(repo, "docs/index.md", original)
    write_file(repo, "docs/index.md", b"new current index\n")
    entry = make_entry(
        "docs/index.md",
        "INDEX_UPDATE",
        original,
        current_sha256=digest(b"not the current file\n"),
    )
    manifest = make_manifest(base, [entry])

    result = docs_hygiene.verify(repo, manifest)

    assert result["errors"]
    assert "current sha256 mismatch" in reasons(result)


def test_index_update_original_backup_mismatch(tmp_path):
    repo = init_repo(tmp_path)
    original = b"old index\n"
    base = commit_file(repo, "docs/index.md", original)
    current = b"new current index\n"
    write_file(repo, "docs/index.md", current)
    backup = make_backup(tmp_path, "docs/index.md", b"wrong original\n")
    entry = make_entry(
        "docs/index.md",
        "INDEX_UPDATE",
        original,
        current_sha256=digest(current),
        backup_path="docs/index.md",
    )
    manifest = make_manifest(base, [entry])

    result = docs_hygiene.verify(repo, manifest, backup)

    assert result["errors"]
    assert "backup" in reasons(result)
    assert "mismatch" in reasons(result)


def run_cli(repo: Path, manifest_path: Path, *extra: str):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(repo),
            "--manifest",
            str(manifest_path),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def test_cli_exit_zero_on_valid_manifest(tmp_path):
    repo = init_repo(tmp_path)
    data = b"cli keep\n"
    write_file(repo, "docs/keep.md", data)
    manifest = make_manifest(
        head(repo),
        [make_entry("docs/keep.md", "KEEP", data)],
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    proc = run_cli(repo, manifest_path)

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["errors"] == []
    assert "Traceback" not in proc.stderr


def test_cli_exit_one_on_invalid_manifest(tmp_path):
    repo = init_repo(tmp_path)
    data = b"cli keep\n"
    write_file(repo, "docs/keep.md", data)
    manifest = make_manifest(
        head(repo),
        [make_entry("docs/keep.md", "KEEP", data, sha256="0" * 64)],
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    proc = run_cli(repo, manifest_path)

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["errors"]
    assert "Traceback" not in proc.stderr


def test_cli_malformed_json_exits_one_without_traceback(tmp_path):
    repo = init_repo(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")

    proc = run_cli(repo, manifest_path)

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["errors"]
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize('kind', ['root', 'backup'])
def test_symlink_parent_of_root_or_backup_is_rejected(tmp_path, kind):
    repo, base, data = archive_repo(tmp_path)
    backup = make_backup(tmp_path, 'raw/old.md', data)
    parent_link = tmp_path / 'parent-link'
    parent_link.symlink_to(tmp_path, target_is_directory=True)
    manifest = make_manifest(base, [make_entry('raw/old.md', 'ARCHIVE_GIT', data, backup_path='raw/old.md')])
    selected_root = parent_link / repo.name if kind == 'root' else repo
    selected_backup = parent_link / backup.name if kind == 'backup' else backup
    result = docs_hygiene.verify(selected_root, manifest, selected_backup)
    assert result['errors'] and 'symlink' in reasons(result)
