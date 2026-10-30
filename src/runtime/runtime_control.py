"""Durable local gates; an epoch does not fence an external exchange owner."""
from __future__ import annotations

import json
import inspect
import time

from execution.execution_ledger import ExecutionLedger


class GateBlocked(RuntimeError):
    pass


class RuntimeControl:
    def __init__(self, ledger: ExecutionLedger):
        self.ledger = ledger
        self._memory_closed: set[str] = set()
        self._storage_failed = False
        # Every new owner incarnation starts closed, including after clean stop.
        with ledger.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS owner_commands("
                       "command_id TEXT PRIMARY KEY,payload TEXT NOT NULL,receipt TEXT NOT NULL)")
            db.execute("UPDATE execution_controls SET closed=1,epoch=epoch+1")

    def _keys(self, strategy: str) -> tuple[str, str, str]:
        if not isinstance(strategy, str) or not strategy or len(strategy) > 128:
            raise GateBlocked("A bounded strategy identity is required")
        return "global", "account:" + self.ledger.scope.account, "strategy:" + strategy

    def _select(self, strategy, scope):
        keys = self._keys(strategy)
        selected = dict(zip(('global','account','strategy'),keys)).get(scope)
        if selected is None: raise ValueError('Unknown control scope')
        return keys,selected

    def _rows(self, db, keys):
        for key in keys:
            if not db.execute("SELECT 1 FROM execution_controls WHERE scope=?", (key,)).fetchone():
                if db.execute("SELECT COUNT(*) FROM execution_controls").fetchone()[0] >= self.ledger.max_intents + 2:
                    self.fail_closed()
                    raise GateBlocked("Control scope budget exhausted")
                db.execute("INSERT INTO execution_controls VALUES(?,0,1)", (key,))
        return [db.execute("SELECT scope,epoch,closed FROM execution_controls WHERE scope=?", (key,)).fetchone() for key in keys]

    def _state(self, rows):
        return {"epochs": {r[0]: r[1] for r in rows},
                "closed": {r[0]: bool(r[2]) or r[0] in self._memory_closed or self._storage_failed for r in rows}}

    def snapshot(self, strategy: str) -> dict:
        try:
            with self.ledger.transaction() as db:
                rows = self._rows(db, self._keys(strategy))
        except BaseException:
            self.fail_closed()
            raise
        return self._state(rows)

    def validate(self, strategy: str, expected_epochs: dict[str, int]) -> None:
        state = self.snapshot(strategy)
        if self._storage_failed or any(state["closed"].values()):
            raise GateBlocked("Execution gate is closed")
        if state["epochs"] != expected_epochs:
            raise GateBlocked("Execution epoch vector is stale")

    def fail_closed(self) -> None:
        self._storage_failed = True

    def halt(self, strategy: str, *, scope: str = "global") -> dict:
        _, selected = self._select(strategy,scope)
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

    def _check_resume(self, keys, selected, state, expected_epochs, readiness_verified):
        if self._storage_failed or readiness_verified is not True:
            raise GateBlocked("Resume requires explicit verified readiness and healthy storage")
        if state["epochs"] != expected_epochs:
            raise GateBlocked("Resume epoch vector is stale")
        if self.ledger.unresolved():
            raise GateBlocked("Unresolved orders prevent resume")
        if any(state['closed'][key] for key in keys[:keys.index(selected)]):
            raise GateBlocked('A broader execution gate remains closed')

    def resume_if_ready(self, strategy: str, *, expected_epochs: dict[str, int], readiness_verified: bool, scope: str = 'strategy') -> dict:
        keys, selected = self._select(strategy,scope)
        self._check_resume(keys, selected, self.snapshot(strategy), expected_epochs, readiness_verified)
        try:
            with self.ledger.transaction() as db:
                db.execute("UPDATE execution_controls SET closed=0,epoch=epoch+1 WHERE scope=?", (selected,))
        except BaseException:
            self.fail_closed()
            raise
        self._memory_closed.discard(selected)
        return self.snapshot(strategy)

    def apply_command(self, command_id: str, action: str, strategy: str, *, scope: str,
                      expires_ns: int, expected_epochs: dict | None = None,
                      readiness_verified: bool = False, verify_readiness=None) -> dict:
        """Commit one local gate effect and its receipt; replay is historical, not permission."""
        keys, selected = self._select(strategy, scope)
        if (not isinstance(command_id, str) or not command_id or len(command_id) > 256
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in command_id)):
            raise ValueError('A bounded command identity is required')
        if action not in ('pause', 'halt', 'resume') or type(expires_ns) is not int or expires_ns <= 0:
            raise ValueError('Invalid command action or expiry')
        if type(readiness_verified) is not bool:
            raise ValueError('Readiness must be boolean')
        if action == 'resume':
            expected_epochs = expected_epochs.copy() if type(expected_epochs) is dict else expected_epochs
            if (type(expected_epochs) is not dict or set(expected_epochs) != set(keys)
                    or any(type(v) is not int or v < 0 for v in expected_epochs.values())):
                raise ValueError('Resume requires the full epoch vector')
        elif expected_epochs is not None:
            raise ValueError('Only resume accepts an epoch vector')
        payload = self.ledger._bounded_json([command_id, action, strategy, scope, expires_ns,
                                             expected_epochs, readiness_verified])
        try:
            with self.ledger.transaction() as db:
                old = db.execute('SELECT payload,receipt FROM owner_commands WHERE command_id=?', (command_id,)).fetchone()
                if old:
                    if old[0] != payload:
                        raise GateBlocked('Command identity conflicts with immutable payload')
                    return {**json.loads(old[1]), 'duplicate': True}
                if time.time_ns() >= expires_ns:
                    raise GateBlocked('Command expired')
                if db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0] >= self.ledger.max_intents:
                    self.fail_closed()
                    raise GateBlocked('Command receipt budget exhausted')
                if action != 'resume':
                    self._memory_closed.add(selected)
                state = self._state(self._rows(db, keys))
                if action == 'resume':
                    if verify_readiness is not None:
                        ready = verify_readiness()
                        if inspect.iscoroutine(ready): ready.close()
                        if ready is not True:
                            raise GateBlocked('Owner readiness checker must synchronously verify readiness')
                    self._check_resume(keys, selected, state, expected_epochs, readiness_verified)
                if time.time_ns() >= expires_ns:
                    raise GateBlocked('Command expired')
                db.execute('UPDATE execution_controls SET closed=?,epoch=epoch+1 WHERE scope=?',
                           (int(action != 'resume'), selected))
                receipt = {'command_id': command_id, 'status': 'ACCEPTED', 'action': action,
                           'scope': selected, 'epoch': state['epochs'][selected] + 1}
                db.execute('INSERT INTO owner_commands VALUES(?,?,?)',
                           (command_id, payload, self.ledger._bounded_json(receipt)))
        except GateBlocked:
            raise
        except BaseException:
            self.fail_closed()
            raise
        if action == 'resume':
            self._memory_closed.discard(selected)
        return {**receipt, 'duplicate': False}
