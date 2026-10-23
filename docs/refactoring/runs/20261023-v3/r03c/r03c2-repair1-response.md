Implemented R03C2 repair 1 within the listed edit paths.

Changes:
- `scripts/build_crypto_release.py`
  - `source_package` now rejects missing package roots instead of returning a nonexistent flat path.
  - Rejects symlinked flat package roots, symlinked `src/vibetrading`, and a symlinked `src` ancestor before reading package content.
- `scripts/build_etf_scope_release.py`, `scripts/build_telegram_restore_release.py`, `scripts/refactoring/build_release_manifest.py`
  - Added equivalent package-root symlink rejection alongside existing missing-root errors.
- `scripts/refactoring/build_server_candidate.py`
  - `package_layout` rejects missing package roots and package-root/`src`-ancestor symlinks.
  - Added delta normalization for both `src/vibetrading/...` and logical `vibetrading/...` forms.
  - Normalization applies to validation, selection, returned `selected_delta_paths`, and matching; traversal is still rejected.
  - Existing `copy_file` leaf symlink guard retained.
- `tests/refactoring/test_src_release_paths.py`
  - Added regressions for missing packages in all readers, symlinked flat/src package roots across all five readers, src-form delta paths, mixed-form deduplication, and traversal rejection.

Command run:
```text
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_release_paths.py tests/test_crypto_release.py
```

Result:
```text
31 passed in 0.14s
```

No metadata, product behavior, frozen layouts, or out-of-scope files changed.
