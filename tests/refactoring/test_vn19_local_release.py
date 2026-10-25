
import pytest
import subprocess
import hashlib
from pathlib import Path

from scripts.refactoring.build_release_manifest import build_local, PACKAGE

@pytest.fixture
def tmp_git_repo(tmp_path):
    """Fixture to create a temporary git repository."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)

    # Create some initial files
    (repo_path / "file1.txt").write_text("initial content 1")
    (repo_path / "src").mkdir()
    (repo_path / "src" / PACKAGE).mkdir()
    (repo_path / "src" / PACKAGE / "module1.py").write_text("print('hello')")

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    return repo_path, head_sha

@pytest.fixture
def tmp_git_repo_flat(tmp_path):
    """Fixture to create a temporary git repository with a flat layout."""
    repo_path = tmp_path / "test_repo_flat"
    repo_path.mkdir()

    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)

    (repo_path / PACKAGE).mkdir()
    (repo_path / PACKAGE / "module_flat.py").write_text("print('flat module')")

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial flat commit"], cwd=repo_path, check=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    return repo_path, head_sha

@pytest.fixture
def tmp_git_repo_src(tmp_path):
    """Fixture to create a temporary git repository with a src layout."""
    repo_path = tmp_path / "test_repo_src"
    repo_path.mkdir()

    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)

    (repo_path / "src").mkdir()
    (repo_path / "src" / PACKAGE).mkdir()
    (repo_path / "src" / PACKAGE / "module_src.py").write_text("print('src module')")

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial src commit"], cwd=repo_path, check=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    return repo_path, head_sha

@pytest.fixture
def tmp_git_repo_ambiguous(tmp_path):
    """Fixture to create a temporary git repository with both src and flat layouts."""
    repo_path = tmp_path / "test_repo_ambiguous"
    repo_path.mkdir()

    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)

    (repo_path / PACKAGE).mkdir()
    (repo_path / PACKAGE / "module_flat.py").write_text("print('flat module')")
    (repo_path / "src").mkdir()
    (repo_path / "src" / PACKAGE).mkdir()
    (repo_path / "src" / PACKAGE / "module_src.py").write_text("print('src module')")

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial ambiguous commit"], cwd=repo_path, check=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    return repo_path, head_sha

# --- Test cases ---

def test_build_local_src_layout_success(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    paths = [f"src/{PACKAGE}/module_src.py"]

    result = build_local(repo_path, expected_head=head_sha, paths=paths)

    assert result["state"] == "LOCAL_REVIEW_PACKAGE_ONLY"
    assert result["source_sha"] == head_sha
    assert result["source_layout"] == "src"
    assert f"src/{PACKAGE}/module_src.py" in result["files"]

def test_build_local_flat_layout_success(tmp_git_repo_flat):
    repo_path, head_sha = tmp_git_repo_flat
    paths = [f"{PACKAGE}/module_flat.py"]

    result = build_local(repo_path, expected_head=head_sha, paths=paths)

    assert result["state"] == "LOCAL_REVIEW_PACKAGE_ONLY"
    assert result["source_sha"] == head_sha
    assert result["source_layout"] == "flat"
    assert f"{PACKAGE}/module_flat.py" in result["files"]

def test_build_local_ambiguous_layout_rejection(tmp_git_repo_ambiguous):
    repo_path, head_sha = tmp_git_repo_ambiguous
    paths = [f"{PACKAGE}/module_flat.py"] # Even with valid paths, layout should be rejected

    with pytest.raises(ValueError, match="ambiguous package source roots"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_head_mismatch_rejection(tmp_git_repo_src):
    repo_path, _ = tmp_git_repo_src
    paths = [f"src/{PACKAGE}/module_src.py"]

    with pytest.raises(ValueError, match="HEAD mismatch"):
        build_local(repo_path, expected_head="a" * 40, paths=paths)

def test_build_local_invalid_head_format_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    paths = [f"src/{PACKAGE}/module_src.py"]

    with pytest.raises(ValueError, match="expected_head must be lowercase40hex"):
        build_local(repo_path, expected_head="invalid_head_sha", paths=paths)

def test_build_local_empty_paths_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src

    with pytest.raises(ValueError, match="paths must contain 1-1024 entries"):
        build_local(repo_path, expected_head=head_sha, paths=[])

def test_build_local_too_many_paths_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    many_paths = [f"src/{PACKAGE}/file_{i}.py" for i in range(1025)]

    # Create these dummy files to avoid FileNotFoundError
    for p_str in many_paths:
        (repo_path / p_str).parent.mkdir(parents=True, exist_ok=True)
        (repo_path / p_str).write_text("dummy content")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "add many files"], cwd=repo_path, check=True)

    updated_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    with pytest.raises(ValueError, match="paths must contain 1-1024 entries"):
        build_local(repo_path, expected_head=updated_head_sha, paths=many_paths)

def test_build_local_non_string_path_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    paths = [f"src/{PACKAGE}/module_src.py", 123]

    with pytest.raises(TypeError, match="all paths must be strings"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_absolute_path_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    abs_path = (repo_path / f"src/{PACKAGE}/module_src.py").resolve()
    paths = [str(abs_path)]

    with pytest.raises(ValueError, match="raw absolute path not allowed"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_path_traversal_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src

    paths_with_dot_dot = [f"src/{PACKAGE}/../module.py"]
    with pytest.raises(ValueError, match="unsafe path components or aliases|non-canonical path"):
        build_local(repo_path, expected_head=head_sha, paths=paths_with_dot_dot)

    paths_with_dot = [f"src/{PACKAGE}/./module.py"]
    with pytest.raises(ValueError, match="unsafe path components or aliases|non-canonical path"):
        build_local(repo_path, expected_head=head_sha, paths=paths_with_dot)

    paths_with_double_slash = [f"src//{PACKAGE}/module_src.py"]
    with pytest.raises(ValueError, match="non-canonical path"):
        build_local(repo_path, expected_head=head_sha, paths=paths_with_double_slash)

    paths_with_single_dot = ["."]
    with pytest.raises(ValueError, match="unsafe path components or aliases|non-canonical path"):
        build_local(repo_path, expected_head=head_sha, paths=paths_with_single_dot)

    paths_with_double_dot = [".."]
    with pytest.raises(ValueError, match="unsafe path components or aliases|non-canonical path"):
        build_local(repo_path, expected_head=head_sha, paths=paths_with_double_dot)

def test_build_local_symlink_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    symlink_path = repo_path / "link_to_module.py"
    target_path = repo_path / f"src/{PACKAGE}/module_src.py"
    symlink_path.symlink_to(target_path)

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "add symlink"], cwd=repo_path, check=True)
    updated_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    paths = [str(symlink_path.relative_to(repo_path))]

    with pytest.raises(ValueError, match="symlink not allowed"):
        build_local(repo_path, expected_head=updated_head_sha, paths=paths)

def test_build_local_untracked_file_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    untracked_file = repo_path / "untracked.py"
    untracked_file.write_text("untracked content")

    paths = [str(untracked_file.relative_to(repo_path))]

    with pytest.raises(ValueError, match="untracked file"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_missing_file_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    paths = ["non_existent.py"]

    with pytest.raises(ValueError, match="file not found"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_directory_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    paths = [f"src/{PACKAGE}"]

    with pytest.raises(ValueError, match="not a regular file"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_secret_component_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src

    # Reject the real repo's existing metadata path without corrupting its Git config.
    with pytest.raises(ValueError, match="secret path component in: .git/config"):
        build_local(repo_path, expected_head=head_sha, paths=[".git/config"])

    (repo_path / ".ssh").mkdir()
    (repo_path / ".ssh" / "id_rsa").write_text("secret key")
    subprocess.run(["git", "add", ".ssh/id_rsa"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "add ssh key"], cwd=repo_path, check=True)
    updated_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()
    with pytest.raises(ValueError, match="secret path component in: .ssh/id_rsa"):
        build_local(repo_path, expected_head=updated_head_sha, paths=[".ssh/id_rsa"])

def test_build_local_env_file_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    env_file = repo_path / ".env.local"
    env_file.write_text("DATABASE_URL=secret")

    subprocess.run(["git", "add", ".env.local"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "add env file"], cwd=repo_path, check=True)
    updated_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    paths = [str(env_file.relative_to(repo_path))]

    with pytest.raises(ValueError, match=r"forbidden .env\* file"):
        build_local(repo_path, expected_head=updated_head_sha, paths=paths)

def test_build_local_forbidden_suffix_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    key_file = repo_path / "my_key.key"
    key_file.write_text("private key")

    subprocess.run(["git", "add", "my_key.key"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "add key file"], cwd=repo_path, check=True)
    updated_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    paths = [str(key_file.relative_to(repo_path))]

    with pytest.raises(ValueError, match="disallowed file extension/suffix"):
        build_local(repo_path, expected_head=updated_head_sha, paths=paths)

def test_build_local_content_drift_rejection(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    module_path = repo_path / f"src/{PACKAGE}/module_src.py"

    module_path.write_text("modified content") # Change without committing

    paths = [f"src/{PACKAGE}/module_src.py"]

    with pytest.raises(ValueError, match="content drift"):
        build_local(repo_path, expected_head=head_sha, paths=paths)

def test_build_local_allowed_sqlite_filename(tmp_git_repo_src):
    repo_path, head_sha = tmp_git_repo_src
    sqlite_module_path = repo_path / f"src/{PACKAGE}/sqlite_store.py"
    sqlite_module_path.write_text("import sqlite3")

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "add sqlite_store"], cwd=repo_path, check=True)
    updated_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, check=True
    ).stdout.decode().strip()

    paths = [f"src/{PACKAGE}/sqlite_store.py"]

    result = build_local(repo_path, expected_head=updated_head_sha, paths=paths)

    assert result["state"] == "LOCAL_REVIEW_PACKAGE_ONLY"
    assert result["source_sha"] == updated_head_sha
    assert result["source_layout"] == "src"
    assert f"src/{PACKAGE}/sqlite_store.py" in result["files"]
    # Verify SHA256
    expected_sha = hashlib.sha256(b"import sqlite3").hexdigest()
    assert result["files"][f"src/{PACKAGE}/sqlite_store.py"] == expected_sha


# Codex critical counterexamples added after partial worker quota failure.
def test_owner_duplicate_inventory_rejected(tmp_git_repo_src):
    root, head = tmp_git_repo_src
    with pytest.raises(ValueError, match="duplicate"):
        build_local(root, expected_head=head, paths=["src/vibetrading/module_src.py"]*2)


def test_owner_raw_crlf_is_pinned_without_mutation(tmp_git_repo_src):
    root, _ = tmp_git_repo_src
    path=root/"src/vibetrading/module_src.py"
    raw=b"x = 1\r\ny = 2\r\n";path.write_bytes(raw)
    subprocess.run(["git","-c","core.autocrlf=false","add","."],cwd=root,check=True)
    subprocess.run(["git","commit","-m","owned CRLF fixture"],cwd=root,check=True,capture_output=True)
    head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    before=subprocess.check_output(["git","status","--porcelain=v1"],cwd=root)
    result=build_local(root,expected_head=head,paths=["src/vibetrading/module_src.py"])
    assert result["files"]["src/vibetrading/module_src.py"]==hashlib.sha256(raw).hexdigest()
    assert path.read_bytes()==raw and subprocess.check_output(["git","status","--porcelain=v1"],cwd=root)==before


def test_owner_symlink_ancestor_cannot_attest_identical_copy(tmp_git_repo_src):
    root,head=tmp_git_repo_src
    original=root/"src/vibetrading";owned=root/"owned-copy"
    original.rename(owned);original.symlink_to(owned,target_is_directory=True)
    with pytest.raises(ValueError,match="symlink"):
        build_local(root,expected_head=head,paths=["src/vibetrading/module_src.py"])
