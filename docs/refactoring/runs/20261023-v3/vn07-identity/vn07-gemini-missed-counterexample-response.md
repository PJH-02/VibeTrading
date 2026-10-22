I have carefully reviewed the provided source code excerpts from `vibetrading/local_runtime.py` and `vibetrading/market_spool.py` against the requirements outlined in the ticket VN07-identity-audit-inline.md.

Here are my concrete findings:

**NO_CRITICAL_FINDINGS**

The code adheres to all specified requirements. The mechanisms for identity pinning, state validation, and error handling are robustly implemented across the `_SegmentProof`, `_CheckpointSpoolReader`, and `SpoolReader` components.

**Detailed Observations and Rationale:**

1.  **Bounded metadata identity pin before C proof/restore:**
    *   **Mechanism:** In `_capture_segment_proofs` (vibetrading/local_runtime.py:114), `os.fstat(opened.fileno())` is used to capture `st_dev` and `st_ino` into `_SegmentProof` objects. These proofs are then passed to the `_CheckpointSpoolReader` constructor.
    *   **Verification:** `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:141) explicitly checks `if (stat.st_dev,stat.st_ino)!=(proof.dev,proof.ino): raise SpoolCorruption("checkpoint spool segment identity changed")`. Similar identity checks are performed in `SpoolReader._locate_start` (vibetrading/market_spool.py:387) and `SpoolReader.read_batch` (vibetrading/market_spool.py:440). This ensures that the physical file backing a segment does not change unexpectedly.

2.  **Actual fstat descriptor used by parser on all opens:**
    *   **Mechanism:** Across all critical segment opening and validation paths, `os.fstat(opened.fileno())` is consistently used. This includes `_capture_segment_proofs` (vibetrading/local_runtime.py:114), `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:140), `SpoolReader._locate_start` (vibetrading/market_spool.py:384), and `SpoolReader.read_batch` (vibetrading/market_spool.py:438).
    *   **Verification:** The direct use of the file descriptor's `fstat` ensures that the metadata observed belongs to the currently opened file, preventing race conditions or reliance on potentially stale path-based metadata.

3.  **Frozen H does not advance:**
    *   **Mechanism:** The `_SegmentProof` for sealed segments includes `mtime_ns`.
    *   **Verification:** `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:160) directly compares `(stat.st_size, stat.st_mtime_ns)` for sealed segments with the pinned proof, raising `SpoolCorruption` on mismatch. `SpoolReader.read_batch` (vibetrading/market_spool.py:445) performs a similar check with `sealed_signature`. Furthermore, `SpoolReader.read_batch` (vibetrading/market_spool.py:424) explicitly checks `if durable < self._durable_sequence or durable < sequence: raise SpoolCorruption("durable checkpoint regressed")`, preventing a regression of the durable high-water mark.

4.  **C0 uses prerestore authority:**
    *   **Mechanism:** `replay_checkpoint_startup` (vibetrading/local_runtime.py:293-302) handles the initial checkpoint (sequence 0). It calculates a SHA256 digest of an empty byte string (`hashlib.sha256(b'').hexdigest()`) and compares it against `point.digest`.
    *   **Verification:** This comparison (vibetrading/local_runtime.py:300) serves as the pre-restore authority for the C0 state.

5.  **Missing prefix fail closed:**
    *   **Mechanism:** Multiple points in the code enforce fail-closed behavior for missing or corrupted segments.
    *   **Verification:**
        *   `_open_checkpoint_segment` (vibetrading/local_runtime.py:105) raises `SpoolCorruption` if segments are missing during checkpoint opening.
        *   `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:137) checks if a segment is not in the frozen inventory.
        *   `SpoolReader._locate_start` (vibetrading/market_spool.py:394) raises `SpoolCorruption` if the durable start cursor is not found.
        *   `SpoolReader.read_batch` (vibetrading/market_spool.py:414) immediately raises `SpoolError` if `self._error` is set, and error handling blocks (e.g., vibetrading/market_spool.py:485-490) set `self._error` upon corruption.

6.  **Active grow/same inode seal allowed:**
    *   **Mechanism:** The code explicitly accommodates an active segment growing and then being sealed under the same inode.
    *   **Verification:**
        *   `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:143-156) allows `stat.st_size` to be greater than `proof.size` for active segments, while still detecting shrinking. It also handles the transition to a sealed state.
        *   `SpoolReader.read_batch` (vibetrading/market_spool.py:472-478) checks that an active file ending before the durable boundary must have been renamed to a sealed file with the *same inode* (`(renamed.stat().st_dev, renamed.stat().st_ino) != identity`).

7.  **Observed sealed state MUST never regress to active or change again:**
    *   **Mechanism:** The `sealed` status and metadata (size, mtime_ns) are rigorously checked.
    *   **Verification:**
        *   `_capture_segment_proofs` (vibetrading/local_runtime.py:116) checks `if not was_active and not sealed: raise SpoolCorruption("checkpoint spool sealed segment regressed")`.
        *   `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:158) checks `if not sealed: raise SpoolCorruption("checkpoint spool sealed segment regressed")` for previously sealed segments.
        *   `_CheckpointSpoolReader._open_segment` (vibetrading/local_runtime.py:160) validates the size and `mtime_ns` for sealed segments.
        *   `SpoolReader.read_batch` (vibetrading/market_spool.py:445) verifies `if sealed_signature is not None and signature != sealed_signature: raise SpoolCorruption("immutable sealed segment changed")`. These checks collectively prevent sealed segments from reverting to active or undergoing any metadata alteration.

8.  **Postrestorefault closes engine/never grants readiness/noreplayorders:**
    *   **Mechanism:** The `replay_checkpoint_startup` function includes a comprehensive `try...except BaseException` block.
    *   **Verification:** If any exception occurs after `engine.restore_virgin` (vibetrading/local_runtime.py:313), the `except BaseException` block (vibetrading/local_runtime.py:351-354) ensures `await engine.close()` is called. The function's docstring (vibetrading/local_runtime.py:265) explicitly states that it "never arms initial resume or grants execution readiness," confirming that it does not provide an unsafe "ready" state.
