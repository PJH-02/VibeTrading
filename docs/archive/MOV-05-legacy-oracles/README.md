# Historical MOV-05 test and rollback oracles

This directory preserves the inactive mixed-owner legacy bundles from b096288.
It is explicitly historical, excluded from wheels/images and normal source imports.
Only pytest adds this path to reproduce existing Golden/oracle tests. Three narrow
whole-module aliases refer to the sole canonical strategy helper implementations.
Original 45 paths and SHA/blob provenance are recorded in
`scripts/refactoring/MOV-05-flat-namespace-resolution.json`. No economic implementation
was removed or activated. The immutable 286-function disposition still governs
future CAP/STR/CLN semantic consolidation. Do not use this directory as an entrypoint,
production package, signer, data directory or active development plan. Rollback drills
are read-only hash checks; activation and operational data restore are unauthorized.
