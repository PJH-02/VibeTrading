# SKILLS.md

**Autonomous Coding AI - Environmental Interaction Specification**

This document defines how the autonomous coding AI may safely interact with the trading system environment during the implementation build loop. These skills are strictly constrained to enable autonomous operation without human supervision while preventing destructive or strategy-altering actions.

---

## CORE PRINCIPLES

### Safety First
All skills must be safe for unattended execution. No destructive system-level actions are permitted.

### Implementation Only
Skills must NEVER be used to design trading strategies, derive alpha, or optimize trading parameters. The AI implements specifications—it does not create them.

### Idempotency
Every action should be repeatable without corrupting system state. Running the same operation twice should produce the same result.

### Atomic Changes
Only one logical change (e.g., one class, one function, one test suite) per iteration. Avoid cascading modifications.

### Fail-Fast Protocol
If an error persists after **3 consecutive self-correction attempts**, the AI must:
1. STOP all execution
2. Generate a detailed error log with context
3. Report to human supervisor
4. Await manual intervention

### Minimal Change Principle
Do not refactor unrelated code. Only modify what is strictly necessary for the current Phase task.

---

## SKILL 1: FILE OPERATIONS

**Purpose:** Implement system logic, schemas, configurations, and tests according to the active Implementation Plan.

### Allowed Operations

#### READ
- Explore existing codebase structure
- Review `shared/models.py`, `shared/schemas.py`, and other shared modules
- Examine service implementations in `services/`
- Read configuration files, logs, and test files

#### CREATE
- New service modules in `services/`
- New test files in `tests/`
- New utility scripts
- Configuration files (`.env.example`, YAML configs)
- Documentation updates

#### MODIFY
- Existing service implementations
- Test files to add coverage
- Shared models and schemas (with extreme caution)
- Requirements files for dependencies

### Operational Constraints

#### Pre-Write Validation
Before saving any file, the AI must:
1. Check for Python syntax errors
2. Verify compatibility with `shared/models.py` and `shared/schemas.py`
3. Ensure imports are valid and modules exist
4. Confirm type hints are consistent with existing patterns

#### Atomic Modification Rule
**Maximum 2 files per iteration** unless directly coupled.

**Directly coupled** means:
- A class and its direct unit test (e.g., `risk_manager.py` + `test_risk_manager.py`)
- A model and its immediate schema or migration
- A service and its configuration file

**Not considered coupled:**
- Multiple unrelated services
- A service and shared utilities
- Multiple test files for different modules

#### Auditability
Every file write must include:
- A docstring or header comment explaining the change
- Inline comments for complex logic
- A commit-style message in the build loop log: `"Modified [file]: [reason]"`

Example:
```python
# Added position size calculation method to RiskManager
# Implements Phase 2.3: Position Sizing Logic
def calculate_position_size(self, signal: Signal, account_balance: float) -> float:
    ...
```

#### Forbidden Operations
- Mass refactoring of entire directories
- Deleting or renaming files in `shared/` without explicit human approval
- Modifying the core event loop in `main.py` without Phase specification
- Creating files outside the project directory structure
- Overwriting configuration files that contain production credentials

### Error Handling
If a file operation fails:
1. Log the error with full stack trace
2. Check if the file is locked or permissions are incorrect
3. Retry once after a 1-second delay
4. If retry fails, escalate to Fail-Fast Protocol

---

## SKILL 2: SHELL EXECUTION

**Purpose:** Build infrastructure, run validation tools, manage dependencies, and execute the trading system.

### Allowed Command Categories

#### Python Execution
- `python main.py --mode backtest`
- `python main.py --mode paper`
- `python scripts/validate_config.py`
- Running individual service modules for testing

#### Code Quality Validation
- `pytest tests/` (all tests or specific test files)
- `pytest --cov=services tests/` (with coverage reports)
- `pylint services/ shared/`
- `mypy services/ shared/ --strict`
- `black --check .` (formatting validation)
- `isort --check-only .` (import ordering)

#### Infrastructure Management
**Allowed `docker-compose` commands:**
- `docker-compose up -d` (start services in detached mode)
- `docker-compose down` (stop services, **without** `-v` flag)
- `docker-compose ps` (check service status)
- `docker-compose logs [service]` (view logs)
- `docker-compose restart [service]` (restart specific service)

**Forbidden `docker-compose` commands:**
- `docker-compose down -v` (volume removal)
- `docker-compose rm` (container removal)
- Any command that modifies volumes or networks directly

#### Dependency Management
- `pip install -r requirements.txt`
- `pip install [package]` (for adding new dependencies)
- `pip list` (check installed packages)
- Updating `requirements.txt` with pinned versions

### Operational Constraints

#### Execution Timeouts
- **Default timeout:** 60 seconds for any single command
- **Exception:** Long-running data feeds during paper trading (configurable up to 5 minutes)
- **Enforcement:** Use Python's `subprocess.run(timeout=60)` or equivalent
- **On timeout:** Terminate process, log timeout error, attempt cleanup

#### Non-Destructive Command Filtering
**Strictly forbidden commands:**
- `rm -rf /` or any recursive deletion of system directories
- `mkfs` (filesystem creation)
- `dd` (disk operations)
- `chmod` or `chown` on system files
- `sudo` or privilege escalation
- `kill -9` on system processes
- Network configuration changes (`iptables`, `ifconfig`)
- Package manager operations outside Python (`apt`, `yum`)

**Command Whitelist Pattern:**
The AI should match commands against a whitelist regex:
```
^(python|pytest|pylint|mypy|black|isort|docker-compose|pip)
```

Any command not matching this pattern requires human approval.

#### Failure Detection and Self-Correction

Every shell command must:
1. Capture return code (`returncode`)
2. Capture stdout and stderr
3. Log all three to the build loop audit log

**On non-zero return code:**
1. Parse stderr for error type (syntax, import, runtime)
2. Attempt self-correction based on error type:
   - **Syntax error:** Fix in source file and retry
   - **Import error:** Check dependencies, install if missing, retry
   - **Test failure:** Analyze test output, fix implementation, retry
3. Track correction attempts (max 3)
4. If 3 attempts exhausted, trigger Fail-Fast Protocol

#### Logging
Every command execution must log:
```
[TIMESTAMP] SHELL_EXEC: <command>
[TIMESTAMP] RETURN_CODE: <code>
[TIMESTAMP] STDOUT: <output>
[TIMESTAMP] STDERR: <errors>
```

---

## SKILL 3: DATABASE ACCESS

**Purpose:** Validate data integrity, debug execution logic, and verify system state without compromising data or discovering alpha.

### Allowed Database Categories

#### QuestDB (Time-Series Market Data)
**Purpose:** Verify candle data collection and ingestion pipeline

**Allowed queries:**
- `SELECT COUNT(*) FROM candles WHERE symbol = 'BTC-USD'`
- `SELECT * FROM candles WHERE timestamp > now() - 1h LIMIT 100`
- `SELECT symbol, COUNT(*) FROM candles GROUP BY symbol`
- Schema inspection: `SHOW TABLES`, `SHOW COLUMNS FROM candles`

**Use cases:**
- Verify data ingestion is working
- Check for gaps in candle data
- Validate timestamp alignment
- Confirm symbol coverage

#### PostgreSQL (Trade State & Audit)
**Purpose:** Debug order execution, verify fills, audit trade history

**Allowed queries:**
- `SELECT * FROM orders WHERE status = 'pending' LIMIT 50`
- `SELECT * FROM fills WHERE timestamp > NOW() - INTERVAL '1 hour'`
- `SELECT COUNT(*) FROM signals WHERE processed = false`
- `SELECT * FROM positions WHERE closed_at IS NULL`

**Use cases:**
- Verify orders are being placed correctly
- Debug fill matching logic
- Check position tracking accuracy
- Audit signal processing pipeline

### Operational Constraints

#### Read-Only Default
**All queries are SELECT by default.**

`INSERT`, `UPDATE`, `DELETE` are **forbidden** except:
- In test-only environments (explicitly marked as `DATABASE_URL_TEST`)
- With human approval in build loop logs
- For test fixture setup/teardown in isolated test databases

#### Anti-Alpha-Mining Enforcement
The AI is **strictly forbidden** from:
- Running queries to find profitable patterns
- Optimizing strategy parameters based on historical data
- Analyzing correlations for trading insights
- Backtesting variations not specified in the Implementation Plan

**Behavioral markers to prevent:**
- Queries with complex aggregations over large time windows for pattern discovery
- Joins between signals and fills to calculate hypothetical PnL not in spec
- Statistical analysis queries (STDDEV, CORR, etc.) for parameter tuning

**Allowed analysis:**
- System performance metrics (latency, throughput)
- Data quality checks (null counts, duplicates)
- Implementation verification (does the code do what the spec says?)

#### Sandbox Principle
During `--mode backtest` or `--mode test`:
- Use isolated test databases only
- Never write to production PostgreSQL tables
- Clearly log database environment in use

During `--mode paper`:
- Read-only access to production data sources
- Write only to designated paper trading tables (prefixed `paper_`)

#### Schema Safety
The AI can:
- **Propose** schema migrations in SQL files
- **Review** existing Alembic migrations
- **Generate** migration scripts using Alembic

The AI **cannot**:
- Execute `DROP TABLE` without human confirmation
- Execute destructive `ALTER TABLE` (dropping columns, changing types) without approval
- Modify `shared/models.py` in ways that require breaking migrations

**Migration approval workflow:**
1. AI generates migration script
2. AI logs: `"Migration proposed: [description]"`
3. AI waits for human approval before executing
4. Human reviews and runs `alembic upgrade head`

#### Query Logging
All database queries executed by the AI must be logged:
```
[TIMESTAMP] DB_QUERY [QuestDB|PostgreSQL]: <query>
[TIMESTAMP] DB_RESULT: <row_count> rows returned
[TIMESTAMP] DB_ERROR: <error_message> (if applicable)
```

#### Connection Management
- Use connection pooling (SQLAlchemy engine)
- Close connections after queries complete
- Maximum query timeout: 30 seconds
- On timeout: Log error, close connection, retry once

#### Error Handling
On database errors:
1. Log full error message with query context
2. Check for common issues:
   - Connection failures (service down)
   - Syntax errors (wrong SQL dialect)
   - Permission errors (wrong credentials)
3. Retry once for transient errors (connection reset)
4. Escalate persistent errors to Fail-Fast Protocol

---

## AUTONOMOUS BUILD LOOP WORKFLOW

The AI operates in this loop for each Phase task:

### 1. Read Phase Specification
- Load current Phase from Implementation Plan
- Understand acceptance criteria
- Identify files to modify

### 2. Implement Changes
- Use FILE SKILL to write/modify code
- Follow Atomic Change principle (max 2 files)
- Add comprehensive docstrings and comments

### 3. Validate Implementation
- Use SHELL SKILL to run linters: `pylint`, `mypy`, `black --check`
- Fix any linting errors (auto-correct attempt #1)
- If errors persist after 3 attempts → Fail-Fast

### 4. Run Tests
- Use SHELL SKILL: `pytest tests/test_[module].py`
- Analyze test failures
- Fix implementation (auto-correct attempts #2-3)
- If tests fail after 3 fix attempts → Fail-Fast

### 5. Validate Data (if applicable)
- Use DATABASE SKILL to verify data integrity
- Check QuestDB for candle ingestion
- Check PostgreSQL for order/fill consistency
- Report any anomalies to log

### 6. Dry Run (if applicable)
- Use SHELL SKILL: `python main.py --mode paper --duration 5min`
- Monitor logs for runtime errors
- Use DATABASE SKILL to spot-check results
- Validate system behavior matches Phase spec

### 7. Report Success or Failure
**On Success:**
```
[PHASE_COMPLETE] Phase X.Y: [Description]
- Files modified: [list]
- Tests passed: [count]
- Validation: PASS
```

**On Failure:**
```
[PHASE_FAILED] Phase X.Y: [Description]
- Error: [category]
- Attempts: 3/3
- Last error: [message]
- Awaiting human intervention
```

---

## ERROR CATEGORIES & SELF-CORRECTION STRATEGIES

### Syntax Errors
- **Detection:** Linter fails, Python import fails
- **Correction:** Parse error message, fix syntax, retry
- **Max attempts:** 3

### Import Errors
- **Detection:** `ModuleNotFoundError`, `ImportError`
- **Correction:** Check `requirements.txt`, install missing package, retry
- **Max attempts:** 2

### Type Errors (mypy)
- **Detection:** `mypy` reports type incompatibility
- **Correction:** Add type hints, fix type mismatches, retry
- **Max attempts:** 3

### Test Failures
- **Detection:** `pytest` non-zero exit code
- **Correction:** Read test output, fix logic error, retry
- **Max attempts:** 3

### Runtime Errors (Dry Run)
- **Detection:** Exception in logs during `main.py` execution
- **Correction:** Add error handling, fix logic, retry with shorter duration
- **Max attempts:** 2

### Database Errors
- **Detection:** Connection failures, query syntax errors
- **Correction:** Check service status, fix query, retry once
- **Max attempts:** 1 (escalate quickly for DB issues)

---

## AUDIT & LOGGING REQUIREMENTS

### Build Loop Log Format
Every action must generate a structured log entry:

```
[TIMESTAMP] [SKILL] [ACTION] [TARGET] [RESULT]

Examples:
[2025-01-28 14:32:01] [FILE] [MODIFY] [services/risk_manager.py] [SUCCESS]
[2025-01-28 14:32:15] [SHELL] [EXEC] [pytest tests/test_risk.py] [PASS]
[2025-01-28 14:32:45] [DB] [QUERY] [PostgreSQL: SELECT * FROM orders] [50 rows]
[2025-01-28 14:33:10] [FILE] [MODIFY] [services/risk_manager.py] [RETRY 1/3]
```

### Human Escalation Log
When Fail-Fast is triggered:

```
[ESCALATION] Phase X.Y failed after 3 attempts
---
Phase: X.Y - [Description]
Error Category: [Syntax|Import|Test|Runtime|Database]
Files Modified: [list]
Last Error:
  <full stack trace or error message>
Self-Correction Attempts:
  1. [action taken] → [result]
  2. [action taken] → [result]
  3. [action taken] → [result]
Recommendation: [AI's analysis of the issue]
---
```

### Audit Trail
Maintain a persistent `build_audit.log` with:
- All file modifications with timestamps
- All shell commands executed
- All database queries run
- All error/retry events
- Phase completion status

This log is append-only and must never be deleted by the AI.

---

## SECURITY & SAFETY GUARDRAILS

### Filesystem Boundaries
- **Allowed:** Project directory and subdirectories
- **Forbidden:** `/etc/`, `/usr/`, `/home/`, `/root/`, system paths

### Network Boundaries
- **Allowed:** Connections to `localhost` services (QuestDB, PostgreSQL, Redis)
- **Forbidden:** Outbound connections to external APIs (except those in trading spec)
- **Exception:** Package downloads via `pip` from PyPI only

### Resource Limits
- **CPU:** Single Python process, no parallel execution without approval
- **Memory:** Monitor for memory leaks during dry runs (>1GB sustained → escalate)
- **Disk:** No files larger than 10MB created without approval
- **Time:** Max 60-second execution per command (except long-running feeds)

### Credential Handling
- **Read:** Environment variables from `.env` file
- **Never log:** API keys, database passwords, secret tokens
- **Forbidden:** Modifying `.env` in production mode
- **Required:** Use `.env.example` as template, never commit secrets

---

## CONCLUSION

This SKILLS specification defines a **conservative, safe, and auditable** environment for autonomous AI operation. The AI has sufficient capability to implement the trading system according to specification while being structurally prevented from:

- Destroying data or infrastructure
- Discovering or optimizing trading strategies
- Making uncontrolled changes to critical systems
- Operating without human oversight beyond defined boundaries

**When in doubt, the AI must STOP and ASK.**

The Fail-Fast Protocol ensures that the AI recognizes the limits of autonomous correction and escalates appropriately. The comprehensive logging ensures full auditability of all actions taken during the build loop.

---

**Document Version:** 1.0  
**Last Updated:** 2025-01-28  
**Approval Required:** Yes (Human Supervisor)  
**Review Frequency:** After each major Phase milestone