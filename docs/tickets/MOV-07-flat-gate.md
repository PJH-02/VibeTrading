# MOV-07 — independent flat-source / entrypoint / image gate

Prerequisites MOV-01–06 accepted locally; MOV-06 complete receipt is recorded in
05. Base acceptance HEAD 7dfbbc9. Scope: final/02 MOV-07 and final/03 ARCH-MOD.
Codex owns the deterministic gate, source ownership, safety, review and final
PASS/FAIL/BLOCKED. This gate writes evidence/status only. Product fixes discovered
here return to MOV-01/05/06 ownership in a separate bounded follow-up packet.
No scope waiver, new strategy, new signer or blanket MM move into runtime/apps.

Initial worker: freellmapi/glm-4.5-flash, role audit (plan), read-only. Snapshot
origin SHA is pinned by launcher origin.json. Workers cannot change provider,
edit any source, authority/status/receipts, approve acceptance or delegate.

Read /work/worker-context.txt and exactly these relevant source groups:
/work/pyproject.toml, /work/src/vibetrading/{__init__,models,dedup,strategy_contract}.py,
/work/src/vibetrading/fv_mm/types.py, /work/src/vibetrading/mm_state/{features,policy}.py,
/work/src/strategies/{legacy/fair_value,research/state}/*.py,
/work/docs/final/02_DEVELOPER_TICKETS.md MOV-05/06/07 sections and
/work/docs/final/03_ARCHITECTURE_REQUIREMENTS.md ARCH-MOD only.
Do not read home, .env, credentials, runtime data or external sources. All tool
paths must start /work/ (not /worker-context.txt). No Bash/setup probes needed.

Report bounded static findings (<=1200 words): exact Gate-A/B/C violations and
direct callers. In particular review whether the 3 pure dependency modules
fv_mm/types.py, mm_state/features.py and mm_state/policy.py can move unchanged
to strategy-owned legacy/research helpers while preserving function/class ASTs,
types, rounding and module/monkeypatch identity. These feature windows are the
legacy research strategy's private state, not the canonical LocalMarketEngine
or native L2 writer. Check any contrary data/owner evidence rather than assuming.
Do not classify all remaining MM bundles as strategy or execution. They already
have 286 per-function DEFER records and must remain recoverable for CAP/STR/CLN.
Assess preserving inactive originals as explicitly historical test/rollback
oracles outside shipped src/wheel/images, with narrow test-only identity shims.
Do not delete source, weaken Golden assertions, or claim an archival plan passed.

Codex independent gate includes immutable MOV-00 map + supplemental resolutions,
all source AST/imports, active commands/mounts/services/cron, root/outside source
and wheel origins, old artifact readback/forward conversion, actual immutable
images/imports/UID/CLI/blank browser, selected Compose7 and no-network startup,
rollback snapshot hash drill, full Linux regression including separate strict
lease guard. Exact commands: python3 -m compileall -q src; offline pip wheel
--no-deps --no-build-isolation --no-index in private copy; python3 -m pytest -q
-p no:cacheprovider -p pytest_asyncio.plugin tests (live_data excluded by existing
pytest.ini, strict inherited-fork suite separately guarded); docker compose config
--no-env-resolution for selected combinations; credential-free docker run
--network=none --read-only --user10001 for actual image probes.
LOCAL/PAPER/FAKE/static only. No live orders/cancels/accounts, host package
installation, production/server changes, push, merge, deploy or paid routes.
