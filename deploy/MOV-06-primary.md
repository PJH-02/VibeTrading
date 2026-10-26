# Explicit local primary and preserved rollback

`primary-compose.yml` is the NATS-free primary candidate. It is separate from
the historical NATS root Compose and its crypto/LS/release overlays. The exact
pre-change files and restore paths/hashes are in `rollback/MOV-06/manifest.json`.
Do not combine those historical NATS overlays with the local primary.

The local primary requires a measured `LocalRuntimePlan`, its referenced capacity
artifacts and the existing verified mount contract. They are external reviewed
inputs; synthetic unit-test measurements are never production capacity evidence.
No measured production plan or Docker daemon was available during this ticket.
No production startup/build/import, permission or deployment is implied.

All roles require explicit profile selection. Collector selects exactly one
reviewed producer; run separate projects/instances for other producers only with
the existing plan's producer/endpoint/storage ownership rules. Strategy and
execution run inside the reviewed engine, rather than starting duplicate owners.
Default configs/runtime.yaml and crypto_runtime.yaml remain historical collector
inputs; they are not valid LocalRuntimePlan documents and are not substituted.

Disabled until separately configured: Paper strategies (explicit borrowed runtime
only), Timefolio order runner (explicit timefolio-order-compose.yml override of the single
engine, ETF/Paper disabled, observation spool and exact producer dependencies required;
--no-submit is fixed in the opt-in command), Timefolio monitor (separate verified browser image and
credentials), maintenance (no automatic deletion/backup or remote activity),
external/public dashboard publishing (not authorized). No nats-init dependency.
Timefolio submit remains false even in the primary environment. A browser image's
default command is the monitor; it never starts an order runner by default.

Root run_strategy.py remains a historical source-only standalone tool using root
services/shared config. Canonical candle model types live in strategies.legacy;
root legacy packages are excluded from the wheel. Remaining research/compatibility
vibetrading sources await MOV-07/CLN-02; neither the flat gate nor release is PASS.
