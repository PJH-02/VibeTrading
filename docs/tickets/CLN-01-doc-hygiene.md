# CLN-01 — verified historical documentation and raw-log Git hygiene

Base 271a72f9d8544e9a3ca7432e08194b4dad167d55. Codex owns retention decisions,
source/caller evidence, physical backup, doc index routing, Git untracking,
independent verification and acceptance. Product code/economics unchanged.
Objective: exactly final/01..05 current authorities; remove obsolete plans and
unreferenced raw attempts from default Git tree after checksum-proven backup;
keep every current acceptance receipt, Golden/oracle, provenance, runbook and
rollback input readable. No operational data deletion. Existing raw evidence
bytes remain locally; Git history and private backup are both recoverable.

Worker scope: implement a bounded stdlib read-only verifier, not an archiver.
Exact model freellmapi/glm-4.5-flash; role implement. Worker cannot change model,
provider, authority/status, manifests, existing evidence, git or files outside
allowlist, recursively delegate, install dependencies or approve itself.
Allowed edits ONLY /work/scripts/refactoring/docs_hygiene.py and
/work/tests/refactoring/test_docs_hygiene.py. Read this ticket, /work/hygiene-context.json,
existing scripts/refactoring/build_release_manifest.py for style if needed.
Use /work/ absolute tool paths. No credentials/runtime_data/home/network access.

Implement API verify(root:Path, manifest:dict, backup_root:Path|None=None)->dict,
CLI --root PATH --manifest PATH [--backup-root PATH]. Read-only; CLI JSON summary,
exit0 only errors empty; exit1 validation failure. Manifest schema:
{schema_version:1,base_sha:40hex,entries:[{path:repo-relative POSIX,
action:'KEEP'|'ARCHIVE_GIT'|'INDEX_UPDATE',sha256:64hex,bytes:int,
reason:nonempty,callers:[nonempty strings],expiry:nonempty,
backup_path:relative POSIX for ARCHIVE_GIT, optional current_sha256 for INDEX_UPDATE}],
protected_receipts:[{task_id, path,sha256}],authority_paths:[five exact final files]}.
KEEP: original file exists, regular non-symlink ancestors/file, hash/size match.
INDEX_UPDATE: current file hash matches current_sha256; original SHA preserved
in backup_path when supplied. ARCHIVE_GIT: old path absent from tracked set
(git ls-files --error-unmatch); original may remain as ignored local file, but
never require that for a fresh clone. Required backup_root + backup_path must
contain exact original SHA/size (symlink/traversal rejected). `git cat-file blob
<base_sha>:<path>` must also match SHA/size; reject unreachable base or mismatch.
Never infer no caller from an empty list; all entries require explicit caller
proof strings, e.g. no active code caller plus current receipt/path inventory.
Protected receipts always exist in current root with exact SHA, no symlinks;
null/unknown historical 04 receipt hashes are measured by Codex, never invented.
Validate 40/64 lowercase hex, unique manifest paths, safe relative paths (reject
absolute,..,.,backslash,empty/noncanonical segments), action, size and all fields.
Root/backup symlinks fail closed. Malformed manifest/JSON does not traceback
secrets or mutate. Public summary returns counts/errors (path + reason only).
Allow base .git worktree file; never follow arbitrary symlink artifact paths.
No recursive product scans/registry/runtime imports or hardcoded repo addresses.

Tests use temporary git repositories and synthetic docs/backup fixtures only:
valid KEEP/protected; valid untrack-with-local-copy and fresh-copy-absent state;
wrong/missing backup, Git blob mismatch, still-tracked archive, missing protected
receipt, unsafe/duplicate paths, symlink file/ancestor/root/backup, malformed
schema/hash/action, INDEX_UPDATE current hash, CLI real exits0/1. Verify no writes.
Exact command: python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_docs_hygiene.py.
Codex independently runs verifier against real inventory + all04 receipt hashes,
active index links, source byte conservation, fresh-clone full Linux regression.
At most two scoped repairs. User authorizes final commit/push of completed batch;
worker itself must not commit/push. No live actions, deployment or new services.
