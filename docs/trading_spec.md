# 🚀 Professional Trading System Specification

### v2.4 FINAL — *Market-Scoped, Strategy-Agnostic, Bias-Safe*

**Status:** Mentor-Approved Architecture
**Intended Use:** AI input for generating an implementation PLAN and executing full vibe coding
**Scope:** Infrastructure, system architecture, execution correctness, and backtesting integrity only

---

## 0. Role & Operating Principles (MANDATORY)

You are a **senior software architect specializing in event-driven trading systems**.

* You **do NOT design or modify trading strategies**
* You **do NOT propose alpha, heuristics, or trading ideas**
* Strategy selection and evaluation are handled externally by the user
* Your responsibility is **correctness, robustness, production readiness, and bias-safe execution**

**Tone Constraints**

* No theory, no hype
* Concrete, implementation-focused
* Assume real capital, real latency, real failure

---

## 1. Primary Objective

Build a **high-performance, resilient, modular trading system** that allows:

* Independent execution per **market** (KR / US / Crypto)
* Independent execution per **service** (Data / Backtest / Trade)
* Safe and deterministic transition from backtesting to live trading
* **Zero-tolerance divergence** between backtest assumptions and live execution mechanics

This document is a **production blueprint**, not a conceptual overview.

---

## 2. Explicit Non-Goals (STRICT)

The following are out of scope and must not be introduced:

* Trading strategy logic or alpha ideas
* Strategy discovery, optimization, or selection logic
* Parameter search, grid search, or ML training loops
* Dynamic strategy registries or factories
* Any architecture not explicitly defined here

---

## 3. Market-Scoped Architecture (MANDATORY)

KR Stock, US Stock, and Crypto are **first-class execution scopes**.

* No service may implicitly handle multiple markets
* Market context must always be explicit
* All execution paths are scoped by market

---

## 4. System Architecture Overview

### 4.1 Architectural Style

* Event-driven microservices
* Fully decoupled services
* Each service must be:

  * Independently startable
  * Independently testable
  * Independently deployable

All inter-service communication occurs **only via the messaging backbone**.

---

## 4.2 Messaging Backbone — NATS JetStream

**Requirements**

* JetStream enabled
* File-based storage
* Explicit acknowledgments
* Durable consumers
* Idempotent message handling

**Core Subjects**

```
MARKET.CANDLES.*
STRATEGY.SIGNALS.*
TRADE.ORDERS.*
TRADE.FILLS.*
RISK.ALERTS.*
SYSTEM.HEALTH.*
```

Message replay must be **safe, deterministic, and replay-order stable**.

---

## 5. Data Storage Layer (Hybrid Model)

### 5.1 QuestDB — Market Data

* OHLCV and tick-level storage
* Partitioned by DAY
* SYMBOL-typed symbols
* Used for **backtesting, replay, and validation only**
* Backtesting must run with **no external network access**

---

### 5.2 PostgreSQL — Transactional State

Stores:

* Orders
* Fills
* Account snapshots
* Risk state
* Configuration versions

Requirements:

* ACID compliance
* Explicit schema versioning
* Full auditability
* Clear separation between backtest, paper, and live schemas

---

## 6. Technical Stack (FIXED)

| Component        | Technology                |
| ---------------- | ------------------------- |
| Language         | Python 3.11+              |
| Messaging        | NATS JetStream            |
| Time-Series DB   | QuestDB                   |
| Relational DB    | PostgreSQL                |
| API / Monitoring | FastAPI                   |
| Deployment       | Docker Compose (Profiles) |

No alternatives allowed.

---

## 7. File & Service Structure

```
Tradingbot/
├── services/
│   ├── data_feed/        # Market-scoped collectors
│   ├── signal_gen/       # Strategy execution (logic external)
│   ├── execution/        # Broker-specific execution
│   ├── risk_engine/     # Global risk controls
│   └── monitoring/      # Alerts & dashboards (interface-only initially)
│
├── shared/
│   ├── models.py        # Pydantic schemas
│   ├── config.py        # Centralized configuration
│   ├── database.py     # DB connections
│   └── fill_logic.py   # Unified fill & cost logic
│
├── backtesting/
│   ├── engine.py       # Replay & walk-forward engine
│   └── bias_checker.py # Bias & integrity enforcement
│
├── docker-compose.yml
└── main.py              # CLI entrypoint
```

The `shared` directory is the **single source of truth**.

---

## 8. Strategy Integration Rule (STRICT)

Strategies are intentionally simple and external.

```python
from strategy import strategy_number_1
```

Rules:

* Direct import only
* No registries, factories, or discovery layers
* Strategy treated as a black box
* Strategy import occurs at process start
* Strategy change requires process restart (acceptable)

---

## 9. Market Data Pipeline (STRICT SEPARATION)

For **each market**, data must flow through the following stages:

1. **Raw Data Collection**

   * Market-native format
   * No assumptions or transformations

2. **Normalized Data**

   * Unified schema per market
   * Time alignment
   * Corporate actions applied
   * Stored immutably

3. **Strategy Consumption**

   * Strategy selects compatible datasets
   * Strategy must not mutate stored data

---

## 10. Execution Modes & CLI Interface

All executions must be explicitly scoped by market and mode.

### Required CLI Arguments

* `--market {kr, us, crypto}`
* `--mode {data, backtest, trade}`
* `--strategy <strategy_name>` (backtest / trade only)

---

## 11. Market × Service Execution Matrix

| Market | Data | Backtest | Trade |
| ------ | ---- | -------- | ----- |
| KR     | Yes  | Yes      | Yes   |
| US     | Yes  | Yes      | Yes   |
| Crypto | Yes  | Yes      | Yes   |

Each combination must run **independently**.

---

## 12. Backtesting & Replay Guarantees (MANDATORY)

Backtesting is a **first-class system**, not a convenience tool.

### 12.1 Deterministic Replay

* Fixed random seed
* Single authoritative clock
* Strict timestamp ordering
* Identical inputs → identical outputs

Backtesting must NEVER:

* Connect to live APIs
* Publish to NATS
* Modify live or paper PostgreSQL state

---

### 12.2 Bias & Data Integrity Enforcement (STRICT)

The backtesting engine MUST explicitly enforce the following.
Violations MUST raise **hard errors**, never warnings.

#### Look-Ahead Bias

* Indicators and signals may only use data strictly prior to the event timestamp
* Any future data access must trigger immediate failure

#### Timestamp Integrity

* Events processed in strictly increasing time order
* No duplicate timestamps unless explicitly supported by the market
* Clock drift or backward timestamps must halt execution

#### Survivorship Bias

* Delisted symbols must remain in historical universes
* Historical symbol membership must be time-aware

#### Corporate Actions

* Splits, dividends, symbol changes, delistings must be reflected
* Price series must be adjusted consistently
* Raw and adjusted prices must never be mixed silently

#### Transaction Cost Pessimism

* Fees, commissions, slippage applied conservatively
* Worst-case assumptions preferred over optimistic ones

#### Latency Modeling

* Signal → order → fill delays must be explicitly modeled
* Zero-latency execution is forbidden

#### In-Sample / Out-of-Sample Separation

* Engine must support walk-forward evaluation
* IS and OOS data boundaries must be explicit and enforced
* Leakage across boundaries must raise hard errors

---

## 13. Unified Fill Logic

* All slippage, fees, and fill logic lives in `shared/fill_logic.py`
* Used identically by backtest and live execution
* No duplicate implementations allowed

---

## 14. Risk & Safety Controls (System-Level)

### Level 1 — Local

* Strategy-level limits (external)

### Level 2 — Global

* Account drawdown monitoring
* `RISK.KILL_SWITCH` broadcast via NATS

### Level 3 — Manual

* External command trigger
* Immediate halt of new orders

Risk controls override all services.

---

## 15. Failure & Recovery Expectations

The system must explicitly handle:

* Message replay after downtime
* Duplicate or out-of-order messages
* Partial fills
* Clock drift
* Data feed gaps
* Broker API outages

Unclear behavior must be flagged before implementation.

---

## 16. Security & Operations

* `.env` allowed for development only
* Production secrets externally managed
* Centralized rate limiting
* Full logging and audit trails

---

## 17. Task for the AI (STRICT)

You must:

1. Validate implementability of this specification
2. Identify blocking ambiguities
3. Produce a **step-by-step implementation PLAN**
4. List assumptions requiring confirmation

You must NOT:

* Add strategies or alpha
* Modify scope
* Introduce new architectural patterns

---

## 18. Required Output Format (STRICT)

```
1. Architecture Validation (Pass / Conditional Pass / Fail)
2. Blocking Ambiguities & Required Clarifications
3. Implementation Plan
   - Phase 0: Infrastructure & Messaging
   - Phase 1: Shared Library
   - Phase 2: Market-Scoped Service Rollout
   - Phase 3: Backtesting Engine (Bias-Safe)
   - Phase 4: Live Execution Safety
4. Pre–Vibe Coding Checklist
```

---

## 19. Final Instruction

This is a **production trading system specification**.

Transform it into an **implementation-ready PLAN**
suitable for **full autonomous vibe coding execution**,
with **bias-safe backtesting guarantees**.

---

### ✅ End of Specification (v2.4)

---
