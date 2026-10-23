I'll perform a thorough read-only audit of the provided source code. Let me analyze each function systematically for regressions, concurrency risks, secrets exposure, interface breaks, and missing acceptance evidence.

# O01A Final Source Delta Audit — Findings

After thorough read-only analysis of the 9 changed functions in `scripts/dev/agent_task.py`, I found **4 concrete security/reliability issues** requiring remediation. No critical bypasses of the stage/ready mechanism were found.

---

## CRITICAL FINDINGS

### FINDING-1: Missing symlink check on `read_paths` in `make_packet` (CWE-59)
**Location:** `make_packet` lines 510–515
**Repro:** Provide a `spec["read_paths"]` entry that is a symlink pointing outside the validated root (e.g., `../../etc/passwd`). The loop at 511–515 calls `_existing_regular_file` then `_sha256_file` but **never calls `_check_no_symlink`**. Contrast with `edit_paths` at line 523 which explicitly invokes `_check_no_symlink`.
**Impact:** An attacker who can place a symlink in the repo (or race-create one) causes the worker to read arbitrary host files into `source_hashes`, violating the sandbox boundary. The packet pins the *target's* hash, not the symlink's, so verification passes.

### FINDING-2: TOCTOU between validation and hashing in `make_packet` (CWE-367)
**Location:** `make_packet` lines 511–515 (read_paths) and 522–528 (edit_paths)
**Repro:**
1. Worker calls `make_packet` with a legitimate `read_paths` entry.
2. Between `_existing_regular_file(root, rel, ...)` (line 514) and `_sha256_file(target)` (line 515), attacker replaces the regular file with a symlink to a secret file.
3. `_sha256_file` follows the symlink (no `O_NOFOLLOW` in the hashing helper) and pins the secret's hash.
**Impact:** The packet cryptographically commits to attacker-chosen content. `verify_source` later re-hashes the *current* target (which may be the symlink) and the hashes match, so verification passes.

### FINDING-3: TOCTOU in `verify_source` between symlink check and file stat (CWE-367)
**Location:** `verify_source` lines 668–677
**Repro:**
1. `verify_source` calls `_check_no_symlink(root, rel, ...)` at line 668.
2. Attacker replaces `root/rel` with a symlink between line 668 and line 669 (`target = root / rel`).
3. Line 675 `target.is_file()` follows the symlink; line 677 `_sha256_file(target)` follows it.
**Impact:** Verification can be fooled into accepting a swapped file. The window is small but exploitable under contention.

### FINDING-4: `_open_private_state_leaf` mutates permissions on existing leaf directory (CWE-281)
**Location:** `_open_private_state_leaf` lines 908–910
**Repro:** Pre-create the leaf directory with mode `0o755` owned by the same UID. Call `write_record`. The `os.fchmod(dir_fd, 0o700)` at line 909 silently widens the threat model's "never chmod existing ancestors" guarantee to the leaf itself. If the leaf is shared with other processes (e.g., a parent directory used as state root), this changes their permissions.
**Impact:** Violates the documented invariant; could break co-operating tools or widen access unintentionally.

---

## HIGH-SEVERITY FINDINGS

### FINDING-5: `write_record` trusts packet fields without validation
**Location:** `write_record` lines 938–940
**Repro:** Call `write_record(state_dir, {"task_id": None}, [], decision=DECISION_PENDING)`. The receipt stores `task_id: null`, `base_sha: null`, `model: null`. Downstream consumers expecting strings may crash or misbehave.
**Mitigation:** Add `_require_nonempty_str(packet.get("task_id"), "packet.task_id")` (and similar for `base_sha`, `model`) before building the receipt.

### FINDING-6: `make_packet` computes `canonical_hashes` AFTER `source_hashes`
**Location:** `make_packet` lines 510–543
**Repro:** If canonical files change between line 535 (end of source hashing) and line 537 (start of canonical hashing), the packet contains inconsistent snapshots. `verify_source` will detect this (it re-reads canonicals), but the packet itself is internally inconsistent.
**Impact:** Non-atomic snapshot; complicates debugging and audit trails.

---

## MEDIUM/LOW FINDINGS

### FINDING-7: `_validated_root` TOCTOU on symlink check
**Location:** `_validated_root` lines 253–256
**Details:** The loop checks `current.is_symlink()` per component, but `absolute.exists()`/`is_dir()` at lines 257–260 occur after the loop. An attacker can replace a component with a symlink between the check and the existence test.
**Context:** Typically called once at startup; lower exploitability but still a TOCTOU.

### FINDING-8: Receipt filename collision resistance relies on 100-try loop
**Location:** `write_record` lines 948–984
**Details:** `uuid.uuid4().hex` provides 128 bits of entropy; 100 attempts is excessive but harmless. No practical collision risk.

### FINDING-9: `verify_source` does not re-validate `forbidden_paths` from packet
**Location:** `verify_source` lines 630–631
**Details:** Reads `read_paths`/`edit_paths` from packet but ignores `packet["forbidden_paths"]`. If the packet was tampered to remove forbidden patterns, verification wouldn't notice.
**Mitigation:** Verify `packet["forbidden_paths"] == spec["forbidden_paths"]` (or canonical equivalent) if forensic completeness is required.

---

## ACCEPTANCE EVIDENCE GAPS

| Requirement | Evidence in Source | Gap |
|-------------|-------------------|-----|
| "Source pins must exactly cover read+edit" | `verify_source` lines 648–664 enforce 1:1 match between `expected_paths` (read+edit) and `source_hashes` keys | ✅ Covered |
| "ModelRoute prefix must match canonical entry" | `_model_route` line 460 enforces `route == provider` | ✅ Covered |
| "Repo root/canonical symlinks reject before ANY read/git" | `_validated_root` called at line 471 (make_packet) and 603 (verify_source) before any I/O | ✅ Covered |
| "write_record ALWAYS PENDING_CODEX_REVIEW/UNTRUSTED_INPUT" | Line 920 enforces `decision == DECISION_PENDING`; lines 935–936 hardcode provenance/untrusted | ✅ Covered |
| "Never chmod existing ancestors" | `_open_private_state_leaf` only fchmods leaf (line 909), not ancestors | ⚠️ **FINDING-4** violates for leaf itself |
| "O_NOFOLLOW/fchmod bound object handles" | `_open_state_dir_component` line 872, `_open_private_state_leaf` line 896, `write_record` line 959 all use `O_NOFOLLOW` with `dir_fd` | ✅ Covered |

---

## SUMMARY

| Severity | Count |
|----------|-------|
| Critical | 4 |
| High | 2 |
| Medium/Low | 3 |

**Verdict:** **NOT READY FOR MERGE** — FINDING-1 and FINDING-2 are exploitable boundary bypasses. FINDING-3 and FINDING-4 violate documented invariants. All require fixes before this delta can be accepted.

---

## REPRODUCTION COMMANDS (for maintainers)

```bash
# FINDING-1: symlink in read_paths
mkdir -p /tmp/test_repo/secret
echo "SECRET" > /tmp/test_repo/secret/data.txt
cd /tmp/test_repo
ln -s /etc/passwd secret/link.txt
# Call make_packet with read_paths=["secret/link.txt"] → pins /etc/passwd hash

# FINDING-2: TOCTOU read_paths
# Requires concurrent attacker; simulate with LD_PRELOAD hook delaying _sha256_file

# FINDING-3: TOCTOU verify_source
# Similar concurrent replacement between _check_no_symlink and target.is_file()

# FINDING-4: fchmod leaf
mkdir -m 755 /tmp/state/leaf
python -c "from scripts.dev.agent_task import write_record; write_record('/tmp/state/leaf', {...})"
stat /tmp/state/leaf  # shows 700, not 755
```

No edits, no tool execution, no other agents invoked. Audit complete.
