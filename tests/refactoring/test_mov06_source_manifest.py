"""Release review must include the moved source, not just the old namespace."""
import hashlib

import pytest

from scripts.refactoring.build_release_manifest import DOMAIN_PACKAGES, canonical_source_hashes


def tree(tmp_path):
    for name in DOMAIN_PACKAGES:
        package = tmp_path / "src" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"")
    return tmp_path


def test_all_domain_paths_are_hashed_and_a_changed_moved_source_is_observed(tmp_path):
    root = tree(tmp_path)
    source = root / "src" / "runtime" / "cli.py"
    source.write_bytes(b"value = 1\r\n")
    before = canonical_source_hashes(root)
    assert len(before) == len(DOMAIN_PACKAGES) + 1
    assert before["src/runtime/cli.py"] == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(b"value = 2\r\n")
    after = canonical_source_hashes(root)
    assert [key for key in before if before[key] != after[key]] == ["src/runtime/cli.py"]


@pytest.mark.parametrize("kind", ["file", "directory", "source_root"])
def test_symlink_sources_cannot_disappear_from_release_evidence(tmp_path, kind):
    root = tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.py").write_text("secret = 'fixture'\n")
    if kind == "file":
        (root / "src/runtime/link.py").symlink_to(outside / "source.py")
    elif kind == "directory":
        (root / "src/runtime/linked").symlink_to(outside, target_is_directory=True)
    else:
        (root / "src").rename(root / "actual-src")
        (root / "src").symlink_to(root / "actual-src", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        canonical_source_hashes(root)


def test_missing_domain_fails_instead_of_producing_partial_release_inventory(tmp_path):
    root = tree(tmp_path)
    (root / "src/timefolio/__init__.py").unlink()
    (root / "src/timefolio").rmdir()
    with pytest.raises(FileNotFoundError, match="timefolio"):
        canonical_source_hashes(root)
