Implemented R03B2 within the five allowed product anchors plus the new focused test.

Changed:
- `src/vibetrading/settings.py` — layout-aware `PROJECT_ROOT` / `DEFAULT_DATA_ROOT`, preserving flat fallback when `src` has no project marker.
- `src/vibetrading/health.py` — same layout anchor for project-config fallback while preserving `/app/config` precedence.
- `src/vibetrading/liquidity_mm/cli.py` — layout-aware `PROJECT_ROOT` / `DEFAULT_CONFIG`.
- `src/vibetrading/fv_mm/runner.py` — `source_hash()` now hashes `root/strategy` plus the actual physical `fv_mm` and `mm_state` package directories; algorithm, filename ordering, and selected file set unchanged.
- `src/vibetrading/fv_pilot/runner.py` — same physical-package source-hash anchoring, retaining the fixed `fv_mm` selected-file list and order.
- `tests/refactoring/test_src_resource_paths.py` — added AST-extracted tests for src, flat, and old flat `src`-named-without-marker layouts, defaults, health config provenance including `/app/config` precedence, and real file-source hash coverage/mutations.

Command run:
```
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_resource_paths.py
```

Result:
```
14 passed in 0.21s
```

No metadata, credentials, trading logic, or out-of-scope files were changed. Parent R03 acceptance is not claimed.
