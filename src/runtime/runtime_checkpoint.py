"""Bounded coupled market/strategy/journal checkpoint with staged recovery.

The SQLite account and ETF journals remain the only economic authorities.  A
runtime checkpoint copies their exact row digests for cut verification; it never
copies, rewinds or deletes those databases.  Restore installs market state into
a callback-free engine, stages new strategy instances, verifies every selected
journal and only then publishes the selected Paper callback/readiness.  Any
ahead/behind/unknown/missing/corrupt handoff quarantines all selected consumers
with zero new intent transmission.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import time

from storage.market_checkpoint import CheckpointError, _canonical, _read_checkpoint_file, _write_checkpoint_file
from core.models import OrderIntent


FORMAT = "vibetrading-runtime-checkpoint"
VERSION = 1
STATES = ("IDLE", "RECOVERING", "READY", "QUARANTINED")


class RuntimeCheckpointError(RuntimeError):
    pass


def _require(condition, reason):
    if not condition:
        raise RuntimeCheckpointError(reason)


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(name + " must be an explicit positive integer")


def _text(value, name="identity"):
    _require(type(value) is str and bool(value), name + " is invalid")


def _load(payload, limit):
    _require(type(payload) is bytes and 0 < len(payload) <= limit,
             "runtime checkpoint byte bound exceeded")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeCheckpointError("duplicate runtime checkpoint JSON key")
            result[key] = value
        return result

    def finite(value):
        raise RuntimeCheckpointError("nonfinite runtime checkpoint JSON")

    try:
        document = json.loads(payload, object_pairs_hook=unique, parse_constant=finite)
        _require(type(document) is dict and set(document) == {"format", "version", "sha256", "body"},
                 "runtime checkpoint envelope schema mismatch")
        _require(document["format"] == FORMAT and type(document["version"]) is int
                 and document["version"] == VERSION, "unsupported runtime checkpoint format/version")
        body = document["body"]
        _require(type(body) is dict and set(body) == {
            "market", "market_identity", "paper", "etf", "journals"},
            "runtime checkpoint body schema mismatch")
        _require(type(document["sha256"]) is str and len(document["sha256"]) == 64
                 and all(c in "0123456789abcdef" for c in document["sha256"]),
                 "runtime checkpoint checksum is invalid")
        _require(document["sha256"] == hashlib.sha256(
            _canonical(body, limit)).hexdigest(), "runtime checkpoint checksum mismatch")
        _require(_canonical(document, limit) == payload,
                 "runtime checkpoint JSON is not canonical")
        return body
    except RuntimeCheckpointError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        raise RuntimeCheckpointError("invalid runtime checkpoint document") from exc


def _json_value(value, limit, depth=0):
    """Return a detached finite JSON-only value; reject executable/extra types."""
    _require(depth <= 64, "plugin state nesting bound exceeded")
    if value is None or type(value) in {bool, int, str}:
        if type(value) is str:
            _require(len(value.encode("utf-8")) <= limit, "plugin state string exceeds byte bound")
        return value
    if type(value) is float:
        _require(math.isfinite(value), "plugin state contains a nonfinite number")
        return value
    if type(value) is list:
        _require(len(value) <= limit, "plugin state list exceeds capacity")
        return [_json_value(item, limit, depth + 1) for item in value]
    if type(value) is dict:
        _require(len(value) <= limit, "plugin state mapping exceeds capacity")
        result = {}
        for key, item in value.items():
            _require(type(key) is str and bool(key), "plugin state keys must be explicit strings")
            result[key] = _json_value(item, limit, depth + 1)
        return result
    raise RuntimeCheckpointError("plugin state must contain only dict/list/scalars")


def _canonical_state(value, limit):
    detached = _json_value(value, limit)
    encoded = _canonical(detached, limit)
    return detached, encoded


def _database_path(connection):
    rows = connection.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if row[1] == "main" and row[2]:
            return str(Path(row[2]).resolve())
    raise RuntimeCheckpointError("journal database path is unavailable")


def _row_document(table, columns, row, *, paper):
    document = {}
    for column, value in zip(columns, row):
        if isinstance(value, (bytes, bytearray, memoryview)):
            value = {"bytes_hex": bytes(value).hex()}
        elif isinstance(value, float):
            _require(math.isfinite(value), "journal contains a nonfinite number")
        elif value is not None and type(value) not in {int, str, float}:
            value = str(value)
        document[column] = value
    if paper and table == "portfolio_orders":
        # Startup preserves OPEN/CANCEL_PENDING reservations as UNKNOWN.  Treat
        # those three labels as the same unresolved active state only while
        # fingerprinting; never rewrite the actual ledger.
        if document.get("state") in {"OPEN", "CANCEL_PENDING", "UNKNOWN"}:
            document["state"] = "@UNRESOLVED_ACTIVE"
    return document


def journal_evidence(connection, *, kind, scope, mode, path, max_rows, max_bytes,
                     paper=False):
    """Hash every user table/schema through deterministic, bounded streaming."""
    if not hasattr(connection, "execute"):
        raise RuntimeCheckpointError("journal connection is unavailable")
    if getattr(connection, "in_transaction", False):
        raise RuntimeCheckpointError("journal must not have an open transaction")
    check = connection.execute("PRAGMA quick_check").fetchall()
    if [row[0] for row in check] != ["ok"]:
        raise RuntimeCheckpointError("journal integrity check failed")
    tables = connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    total_rows = total_bytes = 0
    table_digests = []
    for table, schema in tables:
        _text(table, "journal table")
        _text(schema, "journal schema")
        columns = tuple(row[1] for row in connection.execute(
            'PRAGMA table_info("' + table.replace('"', '""') + '")'))
        _require(bool(columns), "journal table has no columns")
        rows = []
        for row in connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"'):
            total_rows += 1
            _require(total_rows <= max_rows, "journal row budget exhausted")
            try:
                encoded = _canonical(_row_document(table, columns, row, paper=paper), max_bytes)
            except CheckpointError as exc:
                raise RuntimeCheckpointError("journal byte budget exhausted") from exc
            total_bytes += len(encoded)
            _require(total_bytes <= max_bytes, "journal byte budget exhausted")
            rows.append(encoded)
        rows.sort()
        digest = hashlib.sha256()
        try:
            digest.update(_canonical([table, schema, list(columns)], max_bytes))
        except CheckpointError as exc:
            raise RuntimeCheckpointError("journal byte budget exhausted") from exc
        for encoded in rows:
            digest.update(encoded)
        table_digests.append({"table": table, "rows": len(rows), "sha256": digest.hexdigest()})
    try:
        combined = hashlib.sha256(_canonical({
            "kind": kind, "scope": scope, "mode": mode, "path": path,
            "tables": table_digests}, max_bytes)).hexdigest()
    except CheckpointError as exc:
        raise RuntimeCheckpointError("journal byte budget exhausted") from exc
    return {"kind": kind, "scope": scope, "mode": mode, "path": path,
            "rows": total_rows, "sha256": combined, "tables": table_digests}


def _verify_evidence(expected, actual):
    if type(expected) is list and type(actual) is list:
        _require(len(expected) == len(actual), "journal evidence inventory changed")
        for left, right in zip(expected, actual):
            _verify_evidence(left, right)
        return True
    _require(type(expected) is dict and type(actual) is dict, "journal evidence schema mismatch")
    _require(set(expected) == set(actual), "journal evidence schema changed")
    for key in ("kind", "scope", "mode", "path", "rows", "sha256", "tables"):
        _require(expected.get(key) == actual.get(key),
                 "selected journal changed from the checkpoint cut")
    _require(expected == actual,
             "selected consumer/matcher evidence changed from the checkpoint cut")
    return True


def _matcher_state_evidence(matcher):
    ledger = matcher.ledger
    state = ledger.paper_state()
    if state is not None:
        _require(deepcopy(getattr(matcher, "_state", None)) == state,
                 "matcher memory differs from its durable paper state")
    else:
        empty = {"version": 2, "config": dict(matcher.config), "clock": 0, "orders": {},
                 "books": {}, "receipts": {}, "fills": 0, "blocked": {},
                 "gap_generations": {}}
        _require(not ledger.active_orders(), "unpersisted matcher has durable active orders")
        _require(deepcopy(getattr(matcher, "_state", None)) == empty,
                 "unpersisted matcher memory is not a validated empty state")
    for order in ledger.active_orders():
        try:
            payload = json.loads(order["payload"])
            rules = json.loads(order["rules"])
            evidence = json.loads(order["evidence"])
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeCheckpointError("malformed Paper pending order payload") from exc
        _require(type(payload) is dict and type(rules) is dict and type(evidence) is dict,
                 "malformed Paper pending order payload")
        try:
            intent = OrderIntent.model_validate_json(order["payload"])
        except Exception as exc:
            raise RuntimeCheckpointError("malformed Paper pending order intent") from exc
        _require(intent.intent_id == order["intent_id"],
                 "Paper pending order identity differs from its intent")
    return {"config": dict(matcher.config), "account_version": ledger.account_version(),
            "readiness": ledger.readiness(), "has_persisted_matcher": state is not None}


def _paper_evidence(paper, max_rows, max_bytes):
    evidence = []
    for key, matcher in sorted(paper.accounts.items()):
        ledger = matcher.ledger
        _require(ledger.mode.value == "paper", "selected paper account is not PAPER")
        evidence.append({"account": key, "scope": ledger.scope.key,
                         "mode": ledger.mode.value, "path": str(ledger.path),
                         "matcher": _matcher_state_evidence(matcher),
                         "journal": journal_evidence(
                             ledger._db, kind="paper", scope=ledger.scope.key,
                             mode=ledger.mode.value, path=str(ledger.path),
                             max_rows=max_rows, max_bytes=max_bytes, paper=True)})
    return evidence


def _etf_evidence(etf, max_rows, max_bytes, *, require_day=True):
    journal = getattr(etf, "journal", None)
    _require(journal is not None and hasattr(journal, "db"), "ETF journal is missing")
    if getattr(journal.db, "in_transaction", False):
        raise RuntimeCheckpointError("ETF journal has an open transaction")
    # Reuse the existing physical-account ownership proof. No new account
    # registry or credential lookup is introduced here.
    ownership = getattr(etf, "_check_ownership", None)
    if callable(ownership):
        ownership()
    states = []
    for row in journal.db.execute("SELECT intent_id,state FROM dispatch_attempts ORDER BY intent_id"):
        states.append({"intent_id": row[0], "state": row[1]})
    for row in states:
        _require(row["state"] not in {"PREPARED", "HANDOFF", "UNKNOWN"},
                 "ETF dispatch has an unresolved handoff")
    pending = journal.pending()
    for row in pending:
        _require(row["status"] not in {"submitting", "unknown"},
                 "ETF order has an unknown/submitting handoff")
        try:
            intent = OrderIntent.model_validate_json(row["payload"])
        except Exception as exc:
            raise RuntimeCheckpointError("malformed ETF pending order intent") from exc
        _require(intent.intent_id == row["intent_id"]
                 and intent.job_id == row["strategy"]
                 and intent.instrument.raw_symbol == row["symbol"]
                 and intent.side.value == row["side"]
                 and intent.quantity == row["quantity"],
                 "ETF pending order differs from its intent")
    day = getattr(etf, "_day", "")
    _require(type(day) is str and (bool(day) or not require_day),
             "ETF day identity is unavailable")
    # Real EtfTradingLoop persists the two hashed account identities in the
    # journal. A fresh pre-initialize loop has no _day yet but the same journal
    # metadata is available for the preflight bind.
    identities = {row[0]: row[1] for row in journal.db.execute(
        "SELECT key,value FROM metadata WHERE key IN ('account_identity','mock2_account_identity')"
        " ORDER BY key")}
    _require(set(identities) == {"account_identity", "mock2_account_identity"}
             and all(type(value) is str and bool(value) for value in identities.values()),
             "ETF account identities are unavailable")
    accounts = {"mock": identities["account_identity"],
                "mock2": identities["mock2_account_identity"]}
    path = _database_path(journal.db)
    return {"day": day, "account_sha256": hashlib.sha256(
                _canonical({key: str(value) for key, value in sorted(accounts.items())}, max_bytes)
            ).hexdigest(),
            "routes": sorted(accounts),
            "journal": journal_evidence(journal.db, kind="etf", scope="etf-mock",
                mode="mock", path=path, max_rows=max_rows, max_bytes=max_bytes),
            "pending": [{"intent_id": row["intent_id"], "status": row["status"],
                         "dispatch": journal.dispatch_state(row["intent_id"])}
                        for row in pending]}


class RuntimeCheckpoint:
    """One coupled bundle owner for a selected market/Paper/ETF runtime."""

    def __init__(self, path, codec, *, max_bytes, max_journal_rows, max_journal_bytes):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("runtime checkpoint path must be absolute")
        _positive(max_bytes, "max_bytes")
        _positive(max_journal_rows, "max_journal_rows")
        _positive(max_journal_bytes, "max_journal_bytes")
        if not hasattr(codec, "max_bytes") or not hasattr(codec, "capacity"):
            raise ValueError("runtime checkpoint requires an explicit CheckpointCodec")
        self.codec, self.max_bytes = codec, max_bytes
        self.max_journal_rows, self.max_journal_bytes = max_journal_rows, max_journal_bytes
        self._state, self._reason = "IDLE", None
        self._save_task = self._loop = None
        self._bundle = None
        self._restore_evidence = None

    @property
    def state(self):
        return self._state

    @property
    def reason(self):
        return self._reason

    def _set(self, state, reason=None):
        _require(state in STATES, "unknown runtime checkpoint state")
        self._state, self._reason = state, reason

    def _capture_market(self, engine):
        _require(engine is not None and callable(getattr(engine, "capture_checkpoint", None)),
                 "runtime checkpoint requires a market engine owner")
        hot = getattr(engine, "hot_path", None)
        _require(hot is not None, "runtime checkpoint requires the shared hot path")
        return engine.capture_checkpoint(self.codec)

    def _capture_sync(self, engine, paper, etf):
        if self._state in {"RECOVERING", "QUARANTINED"}:
            raise RuntimeCheckpointError("runtime checkpoint source is not recoverable")
        protected = ([Path(matcher.ledger.path) for matcher in paper.accounts.values()]
                     if paper is not None else [])
        if etf is not None:
            protected.append(Path(_database_path(etf.journal.db)))
        output = self.path.resolve()
        for database in protected:
            for suffix in ("", "-wal", "-shm", "-journal"):
                source_path = Path(str(database) + suffix)
                collision = output == source_path.resolve()
                if not collision and self.path.exists() and source_path.exists():
                    collision = self.path.samefile(source_path)
                _require(not collision, "checkpoint output overlaps selected journal path")
        hot = getattr(engine, "hot_path", None)
        _require(not tuple(getattr(engine, "state_callbacks", ())),
                 "source engine has unsupported state callbacks")
        paper_state = None
        etf_state = None
        journals = {"paper": [], "etf": None}
        if paper is not None:
            _require(paper.hot_path is hot, "Paper runtime and market engine hot paths differ")
            _require(paper.state == "IDLE" if hasattr(paper, "state") else True,
                     "Paper runtime is already recovering")
            _require(getattr(paper, "_recovery_state", "IDLE") not in
                     {"RECOVERING", "QUARANTINED"},
                     "Paper runtime recovery gate is not capture-safe")
            expected = (paper.process_applied,)
            callbacks = tuple(getattr(engine, "post_apply_callbacks", ()))
            _require(callbacks in ((), expected),
                     "source engine has unsupported post-apply callbacks")
            if hot.apply_seq:
                _require(callbacks == expected,
                         "source Paper callback is not attached at the selected market cursor")
            paper._checkpoint_capture_guard(engine)
            journals["paper"] = _paper_evidence(paper, self.max_journal_rows,
                                                self.max_journal_bytes)
            paper_state = paper.capture_runtime_state(max_bytes=self.max_bytes)
            # Plugin checkpoint hooks run above. Recheck their actual plugin
            # output, runtime identity and journals before publication.
            identity_after, identity_sha = paper._runtime_identity()
            _require(paper_state["identity"] == identity_after
                     and paper_state["identity_sha256"] == identity_sha,
                     "Paper runtime identity changed during plugin checkpoint capture")
            after_hooks = _paper_evidence(paper, self.max_journal_rows,
                                          self.max_journal_bytes)
            _verify_evidence(journals["paper"], after_hooks)
            journals["paper"] = after_hooks
        else:
            _require(not tuple(getattr(engine, "post_apply_callbacks", ())),
                     "unselected post-apply callback cannot be checkpointed")
        if etf is not None:
            _require(getattr(etf, "_runner_task", None) is None, "ETF runner is active")
            lock = getattr(etf, "_lock", None)
            _require(lock is None or not lock.locked(), "ETF owner lock is held")
            _require(not getattr(etf, "_closing", False), "ETF owner is closing")
            phase = getattr(etf, "phase", None)
            _require(phase not in {"RECOVERING", "QUARANTINED"}, "ETF is already recovering")
            journals["etf"] = _etf_evidence(etf, self.max_journal_rows, self.max_journal_bytes)
            etf_state = dict(journals["etf"])
            etf_state["journal_sha256"] = journals["etf"]["journal"]["sha256"]
        # Capture plugins only after the market bytes and recheck the live market
        # cursor afterwards.  All of this is synchronous on the owner thread.
        if paper is not None:
            cursor = (hot.engine_incarnation, hot.apply_seq, hot.clock_version)
            _require(paper._checkpoint_captured_cursor == cursor,
                     "Paper runtime was not causally aligned with the market cursor")
        market = self._capture_market(engine)
        identity = {"engine_incarnation": hot.engine_incarnation,
                    "apply_seq": hot.apply_seq, "clock_version": hot.clock_version}
        if paper is not None:
            _require(paper._checkpoint_captured_cursor ==
                     (hot.engine_incarnation, hot.apply_seq, hot.clock_version),
                     "market cursor changed during Paper state capture")
            final_identity, final_sha = paper._runtime_identity()
            _require(final_identity == identity_after and final_sha == identity_sha,
                     "Paper runtime identity changed during market capture")
        if etf is not None:
            final_etf = _etf_evidence(etf, self.max_journal_rows, self.max_journal_bytes)
            _require(final_etf["day"] == etf_state["day"]
                     and final_etf["account_sha256"] == etf_state["account_sha256"]
                     and final_etf["pending"] == etf_state["pending"],
                     "ETF identity/pending changed during checkpoint capture")
            _verify_evidence(etf_state["journal"], final_etf["journal"])
            journals["etf"] = final_etf
            etf_state = dict(final_etf)
            etf_state["journal_sha256"] = final_etf["journal"]["sha256"]
        body = {"market": json.loads(market), "market_identity": identity,
                "paper": paper_state, "etf": etf_state, "journals": journals}
        return _canonical({"format": FORMAT, "version": VERSION,
            "sha256": hashlib.sha256(_canonical(body, self.max_bytes)).hexdigest(),
            "body": body}, self.max_bytes)

    async def save(self, engine, *, paper_runtime=None, etf=None):
        """Capture synchronously, then persist one shielded atomic bundle."""
        pending = self._save_task
        loop = asyncio.get_running_loop()
        if pending is not None and not pending.done():
            raise RuntimeCheckpointError("runtime checkpoint save already pending")
        if self._loop is not None and self._loop is not loop:
            raise RuntimeCheckpointError("runtime checkpoint is bound to another loop")
        self._loop = loop
        payload = self._capture_sync(engine, paper_runtime, etf)
        # A cancelled caller may no longer retrieve a failed write's exception.
        operation = asyncio.create_task(asyncio.to_thread(
            _write_checkpoint_file, self.path, payload, self.max_bytes),
            name="runtime-checkpoint-write")
        self._save_task = operation
        operation.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        await asyncio.shield(operation)
        return hashlib.sha256(payload).hexdigest()

    def _quarantine(self, paper, etf, reason):
        if paper is not None:
            try:
                paper.quarantine_checkpoint_recovery(reason)
            except Exception:
                pass
        if etf is not None:
            try:
                etf.quarantine_checkpoint_recovery(reason)
            except Exception:
                pass

    async def restore(self, engine, producers, *, paper_runtime=None, etf=None, readiness):
        """Restore one exact cut or quarantine every selected consumer."""
        if not callable(readiness) or inspect.iscoroutinefunction(readiness):
            raise ValueError("runtime checkpoint requires synchronous readiness")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeCheckpointError("runtime checkpoint is bound to another loop")
        if self._state == "QUARANTINED":
            raise RuntimeCheckpointError("runtime checkpoint is permanently quarantined")
        if self._state != "IDLE":
            raise RuntimeCheckpointError("runtime checkpoint is already restoring or restored")
        if self._save_task is not None and not self._save_task.done():
            raise RuntimeCheckpointError("runtime checkpoint save is still pending")
        self._loop = loop
        # Claim single-flight and close every selected consumer BEFORE the first
        # await/file read. A missing/corrupt/partial bundle can then never leave
        # an un-gated Paper/ETF target able to process input.
        self._set("RECOVERING", "restore started")
        try:
            if paper_runtime is not None:
                paper_runtime.begin_checkpoint_recovery()
            if etf is not None:
                _require(getattr(etf, "_runner_task", None) is None,
                         "ETF runner is active during restore")
                lock = getattr(etf, "_lock", None)
                _require(lock is None or not lock.locked(),
                         "ETF owner lock is held during restore")
                _require(not getattr(etf, "_closing", False),
                         "ETF owner is closing during restore")
                etf.begin_checkpoint_recovery()
            payload = await asyncio.to_thread(_read_checkpoint_file, self.path, self.max_bytes)
            body = _load(payload, self.max_bytes)
        except BaseException as exc:
            # Missing/corrupt newest state is a terminal fail-closed handoff for
            # every selected consumer; never fall back to an older file.
            self._quarantine(paper_runtime, etf, type(exc).__name__)
            self._set("QUARANTINED", type(exc).__name__)
            try:
                await engine.close()
            except BaseException:
                pass
            raise
        self._bundle = body
        handoffs = None
        try:
            paper_state, etf_state = body["paper"], body["etf"]
            _require((paper_state is None) == (paper_runtime is None),
                     "Paper consumer inventory differs from the checkpoint")
            _require((etf_state is None) == (etf is None),
                     "ETF consumer inventory differs from the checkpoint")
            if etf is not None:
                current = _etf_evidence(etf, self.max_journal_rows, self.max_journal_bytes,
                                        require_day=False)
                if getattr(etf, "_day", ""):
                    _require(current["day"] == etf_state["day"], "ETF day identity changed")
                _require(current["account_sha256"] == etf_state["account_sha256"],
                         "ETF account identity changed")
                _require(current["routes"] == etf_state.get("routes"),
                         "ETF account routes changed")
                _require(current["pending"] == etf_state.get("pending"),
                         "ETF pending order evidence changed")
                _verify_evidence(etf_state.get("journal"), current["journal"])
            # Market install is exact-C and state-only.  A nonempty retained
            # suffix has no coupled consumer-state replay in VN07B.
            from .local_runtime import replay_checkpoint_startup
            market_payload = _canonical(body["market"], self.codec.max_bytes)
            handoffs = await replay_checkpoint_startup(
                engine, producers, codec=self.codec, payload=market_payload,
                expected_engine_incarnation=body["market_identity"]["engine_incarnation"])
            if any(high != checkpoint for checkpoint, high in handoffs):
                _require(paper_runtime is None and etf is None,
                         "nonempty market suffix lacks matching consumer state")
            hot = engine.hot_path
            _require((hot.engine_incarnation, hot.apply_seq, hot.clock_version) == (
                body["market_identity"]["engine_incarnation"],
                body["market_identity"]["apply_seq"],
                body["market_identity"]["clock_version"]),
                "restored market cursor differs from the selected cut")
            # Stage all plugin instances and re-verify journals after hooks but
            # before publishing any callback/readiness.
            paper_proof = None
            if paper_runtime is not None:
                _verify_evidence(body["journals"]["paper"],
                    _paper_evidence(paper_runtime, self.max_journal_rows,
                                    self.max_journal_bytes))
                paper_proof = paper_runtime.restore_runtime_state(
                    paper_state, verify=lambda: _verify_evidence(
                        body["journals"]["paper"], _paper_evidence(
                            paper_runtime, self.max_journal_rows,
                            self.max_journal_bytes)),
                    max_bytes=self.max_bytes)
            if etf is not None:
                # Existing ETF initialization/reconciliation runs under the
                # unconditional recovery gate, then only today's state bars.
                initialize = getattr(etf, "initialize", None)
                if callable(initialize) and not getattr(etf, "_checkpoint_initialized", True):
                    await initialize()
                    etf._checkpoint_initialized = True
                current = _etf_evidence(etf, self.max_journal_rows, self.max_journal_bytes)
                _require(current["day"] == etf_state["day"], "ETF day identity changed")
                _require(current["account_sha256"] == etf_state["account_sha256"],
                         "ETF account identity changed")
                _require(current["routes"] == etf_state.get("routes"),
                         "ETF account routes changed")
                _require(current["pending"] == etf_state.get("pending"),
                         "ETF pending order evidence changed")
                _verify_evidence(etf_state.get("journal"), current["journal"])
                from .local_runtime import restore_etf_startup
                await restore_etf_startup(etf, producers, handoffs)
                current = _etf_evidence(etf, self.max_journal_rows, self.max_journal_bytes)
                _require(current["day"] == etf_state["day"], "ETF day identity changed")
                _require(current["account_sha256"] == etf_state["account_sha256"],
                         "ETF account identity changed")
                _require(current["routes"] == etf_state.get("routes"),
                         "ETF account routes changed")
                _require(current["pending"] == etf_state.get("pending"),
                         "ETF pending order evidence changed")
                _verify_evidence(etf_state.get("journal"), current["journal"])
            def combined(market_engine, snapshot, receipts):
                result = readiness(market_engine, snapshot, receipts)
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise RuntimeCheckpointError("runtime readiness must be synchronous")
                if result is not True:
                    return False
                _require((market_engine.hot_path.engine_incarnation,
                          market_engine.hot_path.apply_seq,
                          market_engine.hot_path.clock_version) == (
                              body["market_identity"]["engine_incarnation"],
                              body["market_identity"]["apply_seq"],
                              body["market_identity"]["clock_version"]),
                         "market cursor changed before readiness")
                if paper_runtime is not None:
                    _require(paper_runtime.checkpoint_restore_proof(paper_state) == paper_proof,
                             "Paper staged state changed before readiness")
                    _verify_evidence(body["journals"]["paper"], _paper_evidence(
                        paper_runtime, self.max_journal_rows, self.max_journal_bytes))
                if etf is not None:
                    current = _etf_evidence(etf, self.max_journal_rows, self.max_journal_bytes)
                    _require(current["day"] == etf_state["day"], "ETF day identity changed")
                    _require(current["account_sha256"] == etf_state["account_sha256"],
                             "ETF account identity changed")
                    _require(current["routes"] == etf_state.get("routes"),
                             "ETF account routes changed")
                    _require(current["pending"] == etf_state.get("pending"),
                             "ETF pending order evidence changed")
                    _verify_evidence(etf_state.get("journal"), current["journal"])
                return True
            if engine.complete_checkpoint_recovery(combined) is not True:
                raise RuntimeCheckpointError("runtime readiness did not permit startup")
            if paper_runtime is not None:
                # Only after every market/plugin/journal proof is verified do we
                # publish the exact selected callback and paper readiness gate.
                _require(paper_runtime.publish_checkpoint_restore(engine.hot_path, paper_proof),
                         "Paper recovery publication failed")
            if etf is not None:
                _require(etf.mark_checkpoint_recovery_ready(etf_state["day"]) is True,
                         "ETF recovery publication failed")
            if paper_runtime is not None:
                # The real API, not an isolated test, binds the exact selected
                # callback. This is the only point after every market, plugin,
                # journal and readiness proof and both publications succeeded.
                selected = (paper_runtime.process_applied,)
                existing = tuple(getattr(engine, "post_apply_callbacks", ()))
                _require(existing in ((), selected),
                         "engine post-apply callback inventory changed during restore")
                guard = getattr(engine, "_owner_operation", None)
                if callable(guard):
                    with guard(exclusive=True):
                        engine.post_apply_callbacks = selected
                else:
                    engine.post_apply_callbacks = selected
            self._restore_evidence = {"market": dict(body["market_identity"]),
                "paper": body["journals"]["paper"], "etf": body["journals"]["etf"]}
            self._set("READY", "exact cut verified")
            return handoffs
        except BaseException as exc:
            self._quarantine(paper_runtime, etf, type(exc).__name__)
            self._set("QUARANTINED", type(exc).__name__)
            try:
                await engine.close()
            except BaseException:
                pass
            raise

    def evidence(self):
        return deepcopy(self._restore_evidence)
