# 00) Repo Overview (Observed Current State)

## Top-level shape (v1 legacy vs v2 target)
- Root currently contains two parallel code trees plus CLI wrappers:
  - `vibetrading_V1/` (legacy runtime with microservice-style services)
  - `vibetrading_V2/` (target modular runtime scaffold)
  - `cli/` (current composition roots for V2 backtest/paper/live)
  - `docs/context/` (context-pack outputs)
- Observed top-level layout and split are visible in `tree -L 2` output and package trees (`tree -L 3 vibetrading_V1`, `tree -L 3 vibetrading_V2`).
- On this filesystem, both upper/lower-case aliases resolve to the same directories (case-insensitive mount), e.g. `vibetrading_V2` and `vibetrading_v2` show identical content/inodes in `ls -li`.

## Discovered run paths (CLIs/scripts/entry modules)
### V2 runtime entrypoints (current)
- `python cli/backtest.py ...` → constructs `RunnerPorts` with `ParquetDataSource + SimulatedExecution` and runs `run_backtest(...)` (`cli/backtest.py:30-46`, `cli/backtest.py:53-54`).
- `python cli/paper.py ...` → `ParquetDataSource + PaperExecutionAdapter` and `run_paper(...)` (`cli/paper.py:30-46`, `cli/paper.py:50-51`).
- `python cli/live.py ...` → `ParquetDataSource + LiveExecutionAdapter` and `run_live(...)` (`cli/live.py:30-46`, `cli/live.py:50-51`).
- Additional V2 validation CLI: `python vibetrading_V2/strategy/registry.py --validate-all ...` (`vibetrading_V2/strategy/registry.py:140-158`).

### V1 runtime/backtest entrypoints
- Main strategy runner: `python vibetrading_V1/run_strategy.py` (`vibetrading_V1/run_strategy.py:254-303`).
- Backtest CLI: `python vibetrading_V1/scripts/run_backtest.py backtest|walkforward ...` (`vibetrading_V1/scripts/run_backtest.py:139-197`).
- Infra scripts with direct entry blocks:
  - `scripts/init_db.py` (`vibetrading_V1/scripts/init_db.py:166-190`)
  - `scripts/init_nats.py` (`vibetrading_V1/scripts/init_nats.py:135-187`)
  - `scripts/validate_system.py` (`vibetrading_V1/scripts/validate_system.py:231-269`)

### `if __main__` inventory (observed)
- `cli/backtest.py`, `cli/paper.py`, `cli/live.py`
- `vibetrading_V1/run_strategy.py`
- `vibetrading_V1/scripts/{run_backtest.py, init_db.py, init_nats.py, validate_system.py, validate_crypto_data_saving.py}`
- `vibetrading_V2/strategy/registry.py`
(From `rg -n "def main\(|if __name__ == ..." ...`.)

## Config files and env-var usage
- Primary configuration loader exists in V1 only (`vibetrading_V1/shared/config.py:195-257`), using `.env` via `pydantic-settings` (`env_file=".env"`) (`vibetrading_V1/shared/config.py:197-201`).
- V1 provides `.env.example` with trading/db/broker/risk keys (`vibetrading_V1/.env.example:6-101`).
- V1 runner mutates env at startup (`STANDALONE_MODE=true`) and via strategy config overlay (`vibetrading_V1/run_strategy.py:28`; `vibetrading_V1/shared/config.py:265-337`).
- No `LIVE_API` / `CONFIRM_LIVE` gate usage found in current tree (`rg -n "LIVE_API|CONFIRM_LIVE" vibetrading_V1 vibetrading_V2 cli` returned no matches).
- V2 currently has no dedicated `.env.example`, `pyproject.toml`, or runtime settings module in-tree (`find` checks showed none under `vibetrading_V2`).

## Dependency/tooling snapshot
- Only explicit dependency manifest found: `vibetrading_V1/requirements.txt` (`find . ... requirements*.txt`).
- V1 requirements include runtime + tooling dependencies: `pydantic`, `nats-py`, `sqlalchemy`, `python-binance`, `websockets`, plus `pytest`, `mypy`, `black`, `isort` (`vibetrading_V1/requirements.txt:6-55`).
- Infrastructure assumptions are codified in `vibetrading_V1/docker-compose.yml` (NATS, QuestDB, PostgreSQL, Redis) (`vibetrading_V1/docker-compose.yml:8-104`).

## CI and test invocation status
- No CI workflow files were found in `.github/workflows/` (and no `.gitlab-ci.yml`) via `find` (empty output).
- Test inventory exists only under V2 (`vibetrading_V2/tests/test_*.py`) (`find vibetrading_V1 vibetrading_V2 -maxdepth 3 -type f -name 'test_*.py'`).
- `pytest --collect-only vibetrading_V2/tests -q` currently fails in this environment due missing plugin dependency (`ModuleNotFoundError: No module named 'pytest_asyncio'`).

## Evidence
### Commands run
```bash
ls -la
tree -L 2
tree -L 3 vibetrading_V1
tree -L 3 vibetrading_V2
find . -maxdepth 4 \( -name 'pyproject.toml' -o -name 'requirements.txt' -o -name 'setup.cfg' -o -name 'setup.py' -o -name 'Pipfile' -o -name 'poetry.lock' \) -print
rg -n "def main\(|if __name__ == ['\"]__main__['\"]" cli vibetrading_V1/run_strategy.py vibetrading_V1/scripts vibetrading_V2/strategy/registry.py
rg -n "env_file=|alias=\"TRADING_|alias=\"CRYPTO_|alias=\"KIS_|alias=\"BINANCE_|os\.environ\[|LIVE_API|CONFIRM_LIVE" vibetrading_V1/shared/config.py vibetrading_V1/run_strategy.py cli vibetrading_V2
find . -maxdepth 4 -type f -path './.github/workflows/*' -print
find vibetrading_V1 vibetrading_V2 -maxdepth 3 -type f -name 'test_*.py' -print | sort
pytest --collect-only vibetrading_V2/tests -q
```

### Key outputs/findings (short excerpts)
- `find . ... requirements ...` returned only `./vibetrading_V1/requirements.txt`.
- CI search command returned no files.
- `pytest --collect-only ...` failed with `ModuleNotFoundError: No module named 'pytest_asyncio'`.

### Concrete file/symbol references
- `cli/backtest.py:30-54 (main)`
- `cli/paper.py:30-51 (main)`
- `cli/live.py:30-51 (main)`
- `vibetrading_V2/strategy/registry.py:140-158 (main)`
- `vibetrading_V1/run_strategy.py:254-303 (main)`
- `vibetrading_V1/scripts/run_backtest.py:139-197 (main)`
- `vibetrading_V1/scripts/init_db.py:166-190 (main)`
- `vibetrading_V1/scripts/init_nats.py:135-187 (init_nats/__main__)`
- `vibetrading_V1/shared/config.py:195-257 (TradingSettings/get_settings)`
- `vibetrading_V1/shared/config.py:265-337 (apply_strategy_config)`
- `vibetrading_V1/.env.example:6-101`
- `vibetrading_V1/requirements.txt:6-55`
- `vibetrading_V1/docker-compose.yml:8-104`
- `vibetrading_V2/tests/test_runner_flow.py:63-110`
