# VN17: existing local role assembly

This is a local programmatic assembly seam. It does not enable Paper automatically from the CLI, create an account owner, import arbitrary factories, or activate an exchange client.

A trusted startup module creates existing ProductSpec/PortfolioLedger/PaperExecution/PaperStrategyRuntime objects, registers individual actual strategy files with `register_configured`, and then calls:

```python
try:
    main(['engine', '--config', absolute_plan_path],
         paper_runtime=host, paper_feed_dependencies=explicit_producer_feed_map)
finally:
    # The caller owns the ledgers. The role borrows them and fences on shutdown.
    ledger.close()
```

Use explicit existing capacity, same protected owner root, PAPER account mode, and paper phase. Producer keys must match the plan and dependency tuples must contain distinct known reference feed keys. Kiwoom market-data identity remains separate from the KIS execution account; this uses the existing reference mapping and never treats Kiwoom as an execution venue. CLI defaults without injected objects remain unchanged.

`telegram` and `dashboard` roles receive the same plan-derived `LOCAL_MARKET_SNAPSHOT_FILE` and `LOCAL_SNAPSHOT_BUDGETS`. Paper account views require explicit `PAPER_PROJECTION_DATABASES` paths. Commands still require their separately configured authoritative handler; this assembly does not invent a Paper command owner.

The independent fixture executes real spool append/flush, UDS connect and resume, actual file loader, shared engine/hot path, matcher/ledger and cold readonly projection. It asserts one KRW buy at99, maker fee0.099, cash9900.901 from10000 and one physical/virtual unit. Borrowed ledger remains open after role shutdown. CLI wiring spies are used only for argument and observer-settings tests. Existing mount/capacity tests remain intact; synthetic tests are not current production volume/load proof.

Graceful role shutdown invalidates inputs and can request cancels, changing durable facts. An older coupled checkpoint is not automatically a proof for the newer journal. Exact-cut recovery must separately validate the current market/plugin/ledger evidence. No old account DB is copied over current fills.

Model-provider failures and rejected mock-only tests are retained in `docs/refactoring/runs/20261025-v3/core5/vn17/`. Codex's small main guard correction and independently executed critical tests, rather than those failed worker statements, establish this local acceptance. No source/data deletion, image build, operating deployment, or native venue verification was performed.
