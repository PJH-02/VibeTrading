# VN19 — local source review preparation

`build_local(root, expected_head=..., paths=[...])` in
`scripts/refactoring/build_release_manifest.py` checks the current committed HEAD
and exact bytes of an explicit bounded source/config/document inventory. It returns
`LOCAL_REVIEW_PACKAGE_ONLY`, layout `src` or `flat`, and per-file SHA256 values.
Ambiguous/missing roots, dirty selected files, stale HEAD, duplicate paths,
symlink ancestors, credentials/data paths and untracked files fail closed.
The existing snapshot-based release CLI is unchanged; this API creates no image,
archive, deployment, process or account connection.

1. Complete VN18 local fault acceptance and review its scoped receipt.
2. Pin the selected commit and exact file inventory, including role config,
   existing source and deployment definitions. Generate the readonly review map.
3. Review `local_paper_assembly.md`: `local_runtime.main(..., paper_runtime=host,
   paper_feed_dependencies=...)` borrows an explicitly constructed Paper owner.
   There is no new CLI flag that constructs arbitrary strategy/account owners.
4. Review current runtime mounts/devices, immutable candidate and rollback image
   digests, current account reconciliation and representative capacity externally.
   Every such gate remains NOT_RUN here; no current server identities are inferred
   from old snapshots. External Telegram/cloud delivery also remains unverified.

The public batch receipt records the accepted local source SHA and inventory.
server_deployed=false; live_active=false; build_image_verified=false.
VN20 deletion is not applied. Existing working venue/cloud/ETF paths stay intact.
