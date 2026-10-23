# R03C3 — deterministic physical path replacements

objective: Update canonical candidate Compose source/target binds, physical Docker COPY sources and active source/report consumers for src layout.
read/edit paths: docker-compose.yml; deploy/crypto-profile.yml; deploy/Dockerfile.crypto-ledger; scripts/prepare_etf_reference.py; scripts/report_mm_state_revision.py; scripts/seal_fv_indicator_report.py; scripts/build_all_strategy_report.py; README.md; docs/architecture.md; docs/status.md.
method: Explicit literal prefix substitutions. Crypto-ledger Docker COPY source tokens only; retain /app/vibetrading targets for pinned legacy flat base. Preserve ARCHIVE_PREFIX and all historical server/frozen-export paths. No product code or evidence rewriting.
constraints: protected deploy_p02_schema.py immutable; strategy/order/math/model/data untouched; no server/account/Compose up/push/merge/deploy.
acceptance: inspect diff and Python AST parse; actual docker compose config with synthetic paths/no credentials, current package COPY source existence, fresh Docker imports, protected P02 help and synthetic frozen-export preparation, final full Linux regressions.
review: Codex mechanical path review. Parent R03 accepts only after aggregate verification.
