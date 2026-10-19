"""Durable local gates; an epoch does not fence an external exchange owner."""
from __future__ import annotations

from .execution_ledger import ExecutionLedger


class GateBlocked(RuntimeError):
    pass


class RuntimeControl:
    def __init__(self, ledger: ExecutionLedger):
        self.ledger = ledger
        self._memory_closed: set[str] = set()
        self._storage_failed = False
        # Every new owner incarnation starts closed, including after clean stop.
        with ledger.transaction() as db:
            db.execute("UPDATE execution_controls SET closed=1,epoch=epoch+1")

    def _keys(self, strategy: str) -> tuple[str, str, str]:
        if not isinstance(strategy, str) or not strategy or len(strategy) > 128:
            raise GateBlocked("A bounded strategy identity is required")
        return "global", "account:" + self.ledger.scope.account, "strategy:" + strategy

    def snapshot(self, strategy: str) -> dict:
        keys = self._keys(strategy)
        try:
            with self.ledger.transaction() as db:
                for key in keys:
                    exists = db.execute("SELECT 1 FROM execution_controls WHERE scope=?", (key,)).fetchone()
                    if not exists:
                        if db.execute("SELECT COUNT(*) FROM execution_controls").fetchone()[0] >= self.ledger.max_intents + 2:
                            raise GateBlocked("Control scope budget exhausted")
                        db.execute("INSERT INTO execution_controls VALUES(?,0,1)", (key,))
                rows = [db.execute("SELECT scope,epoch,closed FROM execution_controls WHERE scope=?", (key,)).fetchone() for key in keys]
        except BaseException:
            self.fail_closed()
            raise
        return {"epochs": {r[0]: r[1] for r in rows},
                "closed": {r[0]: bool(r[2]) or r[0] in self._memory_closed or self._storage_failed for r in rows}}

    def validate(self, strategy: str, expected_epochs: dict[str, int]) -> None:
        state = self.snapshot(strategy)
        if self._storage_failed or any(state["closed"].values()):
            raise GateBlocked("Execution gate is closed")
        if state["epochs"] != expected_epochs:
            raise GateBlocked("Execution epoch vector is stale")

    def fail_closed(self) -> None:
        self._storage_failed = True

    def halt(self, strategy: str, *, scope: str = "global") -> dict:
        keys = self._keys(strategy)
        selected = {"global": keys[0], "account": keys[1], "strategy": keys[2]}.get(scope)
        if selected is None:
            raise ValueError("Unknown control scope")
        # Linearization precedes any storage operation, including a failed one.
        self._memory_closed.add(selected)
        try:
            self.snapshot(strategy)
            with self.ledger.transaction() as db:
                db.execute("UPDATE execution_controls SET closed=1,epoch=epoch+1 WHERE scope=?", (selected,))
        except BaseException:
            self.fail_closed()
            raise
        return {"status": "ACCEPTED", "completed": not self.ledger.unresolved(), **self.snapshot(strategy)}

    def resume_if_ready(self, strategy: str, *, expected_epochs: dict[str, int], readiness_verified: bool) -> dict:
        if self._storage_failed or readiness_verified is not True:
            raise GateBlocked("Resume requires explicit verified readiness and healthy storage")
        state = self.snapshot(strategy)
        if state["epochs"] != expected_epochs:
            raise GateBlocked("Resume epoch vector is stale")
        if self.ledger.unresolved():
            raise GateBlocked("Unresolved orders prevent resume")
        keys = self._keys(strategy)
        try:
            with self.ledger.transaction() as db:
                for key in keys:
                    db.execute("UPDATE execution_controls SET closed=0,epoch=epoch+1 WHERE scope=?", (key,))
        except BaseException:
            self.fail_closed()
            raise
        self._memory_closed.difference_update(keys)
        return self.snapshot(strategy)
