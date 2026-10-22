**Concrete Findings from VN07-identity-independent-audit.md:**

**Critical Finding: Violation of Monotone Sealed Invariant**

The system exhibits a critical flaw where a segment, once observed as sealed, can revert to an active state, violating the "monotone sealed invariant" which dictates that a sealed segment must not become active or change again.

**Concrete Counterexample:**

The ticket describes a real counterexample where this invariant is violated:
1.  A segment is initially `activeatcapture/Cproof`.
2.  During a `restorehook`, `writer.seal` is invoked on this segment.
3.  `postrestorevalidate` correctly records `sealed_observations` for this segment.
4.  However, immediately before the first suffix read, the system performs a `renameSAMEinodebackto.active`, effectively reverting the segment to an active state.
5.  The `_open_segment` function, in its "not sealed" branch, incorrectly relies on `proof.mtime_ns` for its state determination rather than the observed sealed state.
6.  Consequently, the expected rejection for opening a closed (sealed) segment does not occur, allowing the segment to become active again after being observed as sealed.

**Precise Fix:**

The `_open_segment` function must be modified to prioritize and strictly enforce the *observed sealed state* of a segment. Specifically, on the "not sealed" branch, if a segment has been previously recorded or observed as sealed (`sealed_observations`), it must be treated as permanently sealed, regardless of `proof.mtime_ns` or any subsequent attempts to rename it back to an active state. Any operation attempting to transition an observed-sealed segment back to active must be explicitly rejected, triggering a closed engine state as described in the contract.

**Other Potential Material Failures Identified for Future Audit/Investigation:**

The ticket also highlights additional areas that warrant further scrutiny to identify other material failures:

*   **descriptorpinpath-vs-fstat:** A deeper evaluation is needed to understand the implications of relying on `descriptorpinpath` versus `fstat` for determining segment properties and state, particularly concerning consistency and race conditions.
*   **Initialization Timing:** The timing and sequence of initialization routines, especially those involving segment state transitions and observations, require review to prevent incorrect assumptions or state corruption.
*   **Cursor/BatchH Bounds:** The management of cursor and batch handle bounds should be audited to ensure correct handling of segment data and prevent out-of-bounds access or data loss.
*   **Resource Bounds/FD Closures:** A review of resource management, including file descriptor (FD) handling and closure mechanisms, is necessary to prevent resource leaks or unintended access after closure.
*   **Unnecessary New Authority:** An assessment should be conducted to identify and eliminate any instances where new or elevated "authority" (permissions/capabilities) is acquired unnecessarily, as this could introduce security vulnerabilities.
