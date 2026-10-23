Implemented ticket R03C2 scope.

Changed:
- `scripts/build_crypto_release.py` — canonical `src/vibetrading`/legacy flat resolution, ambiguity rejection, legacy `vibetrading/...` bundle names.
- `scripts/build_etf_scope_release.py` — src-aware source resolution, unchanged logical tar names/Dockerfile, guarded `main()`.
- `scripts/build_telegram_restore_release.py` — src-aware package mapping, preserved flat payload names, guarded `main()`.
- `scripts/refactoring/build_release_manifest.py` — scans canonical src while hashing/comparing with logical `vibetrading/...` keys.
- `scripts/refactoring/build_server_candidate.py` — per-tree flat/src detection, normalized logical delta matching, flat legacy output, server-only preservation, ambiguity/traversal rejection.
- `tests/refactoring/test_src_release_paths.py` — added synthetic flat/src fixtures for all five builders and rejection cases.

Verification:
```
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_release_paths.py tests/test_crypto_release.py
17 passed in 0.25s
```

No product/economics logic, production data, server access, installs, commits, or metadata changes.
