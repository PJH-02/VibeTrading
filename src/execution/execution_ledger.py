"""Small account-bound execution journal; never a market catalog replacement."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from core.models import OrderIntent, Side
from .process_lease import ProcessLease


def positive_decimal(value, label: str, *, zero: bool = False) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or (not zero and number == 0):
        raise ValueError(label + " must be finite and " + ("nonnegative" if zero else "positive"))
    if len(number.as_tuple().digits) > 38 or number.adjusted() > 37 or number.as_tuple().exponent < -38:
        raise ValueError(label + " exceeds the execution decimal envelope")
    return number


def exact_sum(*values: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 128
        return sum(values, Decimal(0))


def exact_product(first: Decimal, second: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 128
        return first * second


def exact_difference(first: Decimal, second: Decimal) -> Decimal:
    return exact_sum(first, second.copy_negate())


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str, allow_nan=False)


@dataclass(frozen=True)
class AccountScope:
    venue: str
    account: str
    product: str
    environment: str

    def __post_init__(self):
        for value in (self.venue, self.account, self.product, self.environment):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError("Execution scope requires four bounded explicit identities")

    @property
    def key(self) -> str:
        return canonical([self.venue, self.account, self.product, self.environment])


class ExecutionLedger:
    """One owning thread/process, FULL commits, exact decimal arithmetic.

    Row/receipt limits are supplied explicitly. No production budget is inferred.
    The lease fences cooperating local writers, not exchange-side processes.
    """

    UNRESOLVED = ("AUTHORIZED", "HANDOFF", "UNKNOWN", "LEGACY_UNKNOWN", "SUBMITTED")

    SCHEMA_VERSION = 3

    def __init__(self, path: Path, scope: AccountScope, *, max_intents: int,
                 max_evidence_bytes: int, max_fills: int, authority_root: Path,
                 max_feedback: int = 1024, max_feedback_bytes: int = 65536,
                 native_account_id: str | None = None):
        for value in (max_intents, max_evidence_bytes, max_fills, max_feedback, max_feedback_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Execution ledger budgets must be explicit positive integers")
        self.path = Path(path).resolve()
        self.scope = scope
        if native_account_id is not None and (not isinstance(native_account_id, str)
                or not native_account_id.strip() or len(native_account_id.encode("utf-8")) > 256):
            raise ValueError("Native physical account identity must be bounded")
        self.native_account_id = native_account_id or scope.account
        self.max_intents, self.max_evidence_bytes, self.max_fills = max_intents, max_evidence_bytes, max_fills
        self.max_feedback, self.max_feedback_bytes = max_feedback, max_feedback_bytes
        self._thread = threading.get_ident()
        self._closed = False
        self._authority = ProcessLease.account_for_route(
            authority_root, scope.venue, self.native_account_id, scope.environment)
        self._lease = ProcessLease(self.path.with_suffix(self.path.suffix + ".owner.lock"))
        self._authority.acquire()
        try:
            self._lease.acquire()
            self._db = sqlite3.connect(self.path, isolation_level=None, timeout=2)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS owner_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_intents(
                    intent_id TEXT PRIMARY KEY,client_order_id TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL,payload TEXT NOT NULL,evidence TEXT NOT NULL,
                    expiry_ns INTEGER NOT NULL,epochs TEXT NOT NULL,state TEXT NOT NULL,
                    reservation TEXT NOT NULL,reserve_unit TEXT NOT NULL,
                    filled TEXT NOT NULL DEFAULT '0',notional TEXT NOT NULL DEFAULT '0',
                    fees TEXT NOT NULL DEFAULT '0',response TEXT,updated_ns INTEGER NOT NULL,
                    route_id TEXT NOT NULL DEFAULT '',strategy_id TEXT NOT NULL DEFAULT '',
                    config_version TEXT NOT NULL DEFAULT '',attempt_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT 'NEW',target_order_id TEXT NOT NULL DEFAULT '',
                    cancel_state TEXT NOT NULL DEFAULT '',contract_multiplier TEXT NOT NULL DEFAULT '1');
                CREATE TABLE IF NOT EXISTS execution_attempts(
                    intent_id TEXT PRIMARY KEY,state TEXT NOT NULL,updated_ns INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_fills(
                    fill_id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_positions(instrument TEXT PRIMARY KEY,quantity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_controls(
                    scope TEXT PRIMARY KEY,epoch INTEGER NOT NULL,closed INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_feedback(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,strategy_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,event TEXT NOT NULL,claimed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(strategy_id,event_id));
                CREATE INDEX IF NOT EXISTS execution_feedback_pending
                    ON execution_feedback(strategy_id,claimed,seq);
                CREATE TABLE IF NOT EXISTS execution_source_events(
                    strategy_id TEXT NOT NULL,event_key TEXT NOT NULL,fingerprint TEXT NOT NULL,
                    runtime_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(strategy_id,event_key));
                CREATE TABLE IF NOT EXISTS execution_source_checkpoints(
                    strategy_id TEXT PRIMARY KEY,document TEXT NOT NULL,sha256 TEXT NOT NULL,
                    updated_ns INTEGER NOT NULL);
            """)
            self._migrate()
            with self.transaction():
                row = self._db.execute("SELECT value FROM owner_metadata WHERE key='scope'").fetchone()
                if row and row[0] != scope.key:
                    raise ValueError("Execution ledger belongs to a different account/environment scope")
                self._db.execute("INSERT OR IGNORE INTO owner_metadata VALUES('scope',?)", (scope.key,))
                # The immutable fill receipts provide a migration baseline.
                # This version changes atomically with each newly known fill,
                # so an older external account snapshot cannot release risk.
                self._db.execute("""INSERT OR IGNORE INTO owner_metadata(key,value)
                    SELECT 'account_version',CAST(COUNT(*) AS TEXT) FROM execution_fills""")
                # A committed authorization is never proof of absence of a send.
                self._db.execute("UPDATE execution_intents SET state='UNKNOWN' WHERE state IN ('AUTHORIZED','HANDOFF')")
                self._db.execute("UPDATE execution_attempts SET state='UNKNOWN' WHERE state IN ('AUTHORIZED','HANDOFF')")
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._lease.close()
            self._authority.close()
            raise

    def _migrate(self):
        """Fail-closed in-place schema migration on the same ledger file."""
        required = {
            "route_id": "TEXT NOT NULL DEFAULT ''",
            "strategy_id": "TEXT NOT NULL DEFAULT ''",
            "config_version": "TEXT NOT NULL DEFAULT ''",
            "attempt_id": "TEXT NOT NULL DEFAULT ''",
            "action": "TEXT NOT NULL DEFAULT 'NEW'",
            "target_order_id": "TEXT NOT NULL DEFAULT ''",
            "cancel_state": "TEXT NOT NULL DEFAULT ''",
            "contract_multiplier": "TEXT NOT NULL DEFAULT '1'",
        }
        with self.transaction():
            existing = {row[1] for row in self._db.execute("PRAGMA table_info(execution_intents)")}
            for name, declaration in required.items():
                if name not in existing:
                    self._db.execute("ALTER TABLE execution_intents ADD COLUMN " + name + " " + declaration)
            source_columns = {row[1] for row in self._db.execute(
                "PRAGMA table_info(execution_source_events)")}
            if "runtime_id" not in source_columns:
                self._db.execute("ALTER TABLE execution_source_events ADD COLUMN runtime_id "
                                 "TEXT NOT NULL DEFAULT ''")
            row = self._db.execute("SELECT value FROM owner_metadata WHERE key='schema_version'").fetchone()
            if row is not None:
                try:
                    version = int(row[0])
                except (TypeError, ValueError) as error:
                    raise ValueError("Execution ledger schema version is malformed") from error
                if version > self.SCHEMA_VERSION:
                    raise ValueError("Execution ledger schema is newer than this implementation")
            self._db.execute("INSERT OR REPLACE INTO owner_metadata VALUES('schema_version',?)",
                             (str(self.SCHEMA_VERSION),))

    def _check_thread(self):
        if self._closed or threading.get_ident() != self._thread:
            raise RuntimeError("Execution ledger must be used by its open owning thread")
        self._authority.check()
        self._lease.check()

    def _commit(self):
        self._db.execute("COMMIT")

    @contextmanager
    def transaction(self):
        self._check_thread()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
            self._commit()
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def pragmas(self) -> dict:
        self._check_thread()
        return {"journal_mode": self._db.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": self._db.execute("PRAGMA synchronous").fetchone()[0]}

    def _bounded_json(self, value) -> str:
        text = canonical(value)
        if len(text.encode("utf-8")) > self.max_evidence_bytes:
            raise ValueError("Execution document exceeds explicit byte budget")
        return text

    def fingerprint(self, intent: OrderIntent) -> str:
        body = intent.model_dump(mode="json")
        body.pop("created_ts_ns", None)
        body.pop("status", None)
        return hashlib.sha256(canonical([self.scope.key, body]).encode()).hexdigest()

    def get(self, identity: str) -> dict | None:
        self._check_thread()
        row = self._db.execute("SELECT * FROM execution_intents WHERE intent_id=?", (identity,)).fetchone()
        return dict(row) if row else None

    def existing(self, intent: OrderIntent) -> dict | None:
        row = self.get(intent.intent_id)
        if row and row["fingerprint"] != self.fingerprint(intent):
            raise ValueError("Intent identity conflicts with immutable scoped payload")
        return row

    def reserved(self) -> Decimal:
        self._check_thread()
        return exact_sum(*(Decimal(r[0]) for r in self._db.execute("SELECT reservation FROM execution_intents")))

    def account_version(self) -> int:
        self._check_thread()
        return int(self._db.execute("SELECT value FROM owner_metadata WHERE key='account_version'").fetchone()[0])

    def unresolved(self) -> list[dict]:
        self._check_thread()
        marks = ",".join("?" for _ in self.UNRESOLVED)
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM execution_intents WHERE state IN (" + marks + ")", self.UNRESOLVED)]

    def authorize(self, intent: OrderIntent, *, evidence: dict, expiry_ns: int,
                  epochs: dict[str, int], reserve_unit: Decimal, legacy: bool = False,
                  route_id: str = "", strategy_id: str = "", config_version: str = "",
                  attempt_id: str = "", action: str = "NEW",
                  target_order_id: str = "", contract_multiplier=Decimal(1)) -> tuple[bool, dict]:
        quantity = positive_decimal(intent.quantity, "quantity")
        unit = positive_decimal(reserve_unit, "reservation unit")
        multiplier = positive_decimal(contract_multiplier, "contract multiplier")
        payload = self._bounded_json(intent.model_dump(mode="json"))
        proof = self._bounded_json(evidence)
        with self.transaction():
            old = self.existing(intent)
            if old:
                return False, old
            if self._db.execute("SELECT COUNT(*) FROM execution_intents").fetchone()[0] >= self.max_intents:
                raise RuntimeError("Execution intent row budget exhausted")
            state = "LEGACY_UNKNOWN" if legacy else "AUTHORIZED"
            self._db.execute("""INSERT INTO execution_intents(
                intent_id,client_order_id,fingerprint,payload,evidence,expiry_ns,epochs,state,
                reservation,reserve_unit,updated_ns,route_id,strategy_id,config_version,
                attempt_id,action,target_order_id,cancel_state,contract_multiplier) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (intent.intent_id, intent.client_order_id, self.fingerprint(intent), payload, proof,
                 expiry_ns, canonical(epochs), state, str(exact_product(quantity, unit)), str(unit),
                 time.time_ns(), route_id, strategy_id, config_version, attempt_id, action,
                  target_order_id, "", str(multiplier)))
            self._db.execute("INSERT INTO execution_attempts VALUES(?,?,?)",
                             (intent.intent_id, state, time.time_ns()))
        return True, self.get(intent.intent_id)

    def mark_handoff(self, identity: str):
        with self.transaction():
            changed = self._db.execute("UPDATE execution_intents SET state='HANDOFF',updated_ns=? WHERE intent_id=? AND state='AUTHORIZED'",
                                       (time.time_ns(), identity)).rowcount
            if changed != 1:
                raise RuntimeError("Intent is not authorized for a first handoff")
            self._db.execute("UPDATE execution_attempts SET state='HANDOFF',updated_ns=? WHERE intent_id=?", (time.time_ns(), identity))

    OUTCOMES = frozenset({
        "UNKNOWN", "SUBMITTED", "BLOCKED", "PRE_SEND_FAILED", "CANCEL_PENDING",
        "CANCELLED", "REJECTED", "ACCEPTED",
    })

    def outcome(self, identity: str, state: str, response: dict | None = None):
        if state not in self.OUTCOMES:
            raise ValueError("Unsupported execution outcome")
        body = self._bounded_json(response or {})
        with self.transaction():
            row = self.get(identity)
            if not row:
                raise ValueError("Unknown intent")
            # ACK after a full fill must not resurrect an unresolved order.
            effective = "FILLED" if row["state"] == "FILLED" else state
            release = state in {"BLOCKED", "PRE_SEND_FAILED", "CANCELLED"}
            cancel_state = state if state in {"CANCEL_PENDING", "CANCELLED"} else row["cancel_state"]
            self._db.execute("UPDATE execution_intents SET state=?,response=?,reservation=?,cancel_state=?,updated_ns=? WHERE intent_id=?",
                (effective, body, "0" if release else row["reservation"], cancel_state, time.time_ns(), identity))
            self._db.execute("UPDATE execution_attempts SET state=?,updated_ns=? WHERE intent_id=?", (effective, time.time_ns(), identity))

    def bind_native_order(self, identity: str, order_id: str):
        """Persist the venue order identity separately from mutable ACK bodies."""
        self._check_thread()
        if (not isinstance(identity, str) or not identity
                or not isinstance(order_id, str) or not order_id
                or len(order_id.encode("utf-8")) > 256):
            raise ValueError("Native order binding requires bounded identities")
        with self.transaction():
            row = self.get(identity)
            if not row:
                raise ValueError("Unknown intent")
            current = row["target_order_id"]
            if current and current != order_id:
                raise ValueError("Native order identity conflicts with its original binding")
            self._db.execute(
                "UPDATE execution_intents SET target_order_id=?,updated_ns=? WHERE intent_id=?",
                (order_id, time.time_ns(), identity))

    def find(self, identity: str) -> dict | None:
        """Resolve a durable row by intent/client/native identity, no inference."""
        self._check_thread()
        if not isinstance(identity, str) or not identity:
            return None
        row = self.get(identity)
        if row:
            return row
        row = self._db.execute(
            "SELECT * FROM execution_intents WHERE client_order_id=? OR target_order_id=?",
            (identity, identity)).fetchone()
        if row:
            return dict(row)
        for candidate in self._db.execute(
                "SELECT * FROM execution_intents WHERE response IS NOT NULL"):
            try:
                response = json.loads(candidate["response"])
            except (TypeError, ValueError):
                continue
            if not isinstance(response, dict):
                continue
            for key in ("order_id", "orderId", "client_order_id", "clientOrderId",
                        "target_order_id", "ODNO", "odno", "ord_no"):
                value = response.get(key)
                if value is not None and str(value) == identity:
                    return dict(candidate)
        return None

    def owned(self) -> list[dict]:
        """Durable owned facts for bounded registry restore after restart."""
        self._check_thread()
        marks = ",".join("?" for _ in self.UNRESOLVED)
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM execution_intents WHERE state IN (" + marks + ",'FILLED','CANCELLED') "
            "ORDER BY updated_ns", (*self.UNRESOLVED,))]

    def apply_fill_once(self, identity: str, fill_id: str, *, quantity, price, fee,
                        feedback: tuple[str, str, dict] | None = None,
                        receipt_key: str | None = None) -> bool:
        qty, unit, fees = (positive_decimal(quantity, "fill quantity"), positive_decimal(price, "fill price"),
                           positive_decimal(fee, "fee", zero=True))
        if not isinstance(fill_id, str) or not fill_id or len(fill_id) > 256:
            raise ValueError("An explicit bounded provider-scoped fill ID is required")
        if (receipt_key is not None and
                (not isinstance(receipt_key, str) or not receipt_key or len(receipt_key) > 256)):
            raise ValueError("Composite native fill receipt key must be explicit and bounded")
        storage_key = receipt_key or fill_id
        stamp = canonical([identity, str(qty), str(unit), str(fees)])
        with self.transaction():
            old = self._db.execute("SELECT fingerprint FROM execution_fills WHERE fill_id=?", (storage_key,)).fetchone()
            if old:
                if old[0] != stamp:
                    raise ValueError("Fill identity conflicts with its original contents")
                return False
            row = self.get(identity)
            if not row or row["state"] not in {*self.UNRESOLVED, "FILLED", "CANCEL_PENDING", "CANCELLED"}:
                raise ValueError("Fill requires an existing unresolved/filled/cancelled intent")
            intent = OrderIntent.model_validate_json(row["payload"])
            if receipt_key is not None:
                # Older ledgers stored the raw provider ID globally. Preserve
                # replay idempotence for same-symbol historical rows, while
                # allowing a symbol-scoped native ID collision on another
                # symbol to be recorded under its composite receipt key.
                legacy = self._db.execute(
                    "SELECT intent_id,fingerprint FROM execution_fills WHERE fill_id=?",
                    (fill_id,)).fetchone()
                if legacy:
                    prior = self.get(legacy["intent_id"])
                    if prior:
                        prior_intent = OrderIntent.model_validate_json(prior["payload"])
                        if prior_intent.instrument.raw_symbol == intent.instrument.raw_symbol:
                            if legacy["fingerprint"] != stamp:
                                raise ValueError("Fill identity conflicts with its original contents")
                            return False
            filled = exact_sum(Decimal(row["filled"]), qty)
            if filled > intent.quantity:
                raise ValueError("Fill exceeds intent quantity")
            if self._db.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0] >= self.max_fills:
                raise RuntimeError("Execution fill row budget exhausted")
            key = intent.instrument.key
            prior = self._db.execute("SELECT quantity FROM execution_positions WHERE instrument=?", (key,)).fetchone()
            position = exact_sum(Decimal(prior[0]) if prior else Decimal(0), qty if intent.side == Side.BUY else qty.copy_negate())
            self._db.execute("INSERT INTO execution_fills VALUES(?,?,?)", (storage_key, identity, stamp))
            self._db.execute("INSERT INTO execution_positions VALUES(?,?) ON CONFLICT(instrument) DO UPDATE SET quantity=excluded.quantity", (key, str(position)))
            remaining = exact_difference(intent.quantity, filled)
            state = "FILLED" if remaining == 0 else row["state"]
            # A late fill after a cancel ACK still consumes the factual
            # reservation for that quantity; terminal cancel released it.
            reservation = (Decimal(0) if state == "CANCELLED"
                           else exact_product(remaining, Decimal(row["reserve_unit"])))
            fill_notional = exact_product(exact_product(qty, unit), Decimal(row["contract_multiplier"]))
            self._db.execute("UPDATE execution_intents SET filled=?,notional=?,fees=?,reservation=?,state=?,updated_ns=? WHERE intent_id=?",
                (str(filled), str(exact_sum(Decimal(row["notional"]), fill_notional)), str(exact_sum(Decimal(row["fees"]), fees)),
                 str(reservation), state, time.time_ns(), identity))
            self._db.execute("UPDATE owner_metadata SET value=? WHERE key='account_version'",
                             (str(self.account_version() + 1),))
            if feedback is not None:
                if not isinstance(feedback, tuple) or len(feedback) != 3:
                    raise ValueError("Fill feedback must be a bounded attributed tuple")
                strategy_id, event_id, event = feedback
                if strategy_id != row["strategy_id"]:
                    raise ValueError("Fill feedback attribution differs from the durable order owner")
                self.record_feedback(strategy_id, event_id, event)
        return True

    def position(self, instrument: str) -> Decimal:
        self._check_thread()
        row = self._db.execute("SELECT quantity FROM execution_positions WHERE instrument=?", (instrument,)).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def strategy_position(self, strategy_id: str, instrument: str) -> Decimal:
        """Factual fills attributed to one strategy, without cross-strategy netting."""
        self._check_thread()
        total = Decimal(0)
        for row in self._db.execute("SELECT strategy_id,payload,filled FROM execution_intents"):
            if row["strategy_id"] != strategy_id:
                continue
            intent = OrderIntent.model_validate_json(row["payload"])
            if intent.instrument.key == instrument:
                quantity = Decimal(row["filled"])
                total = exact_sum(total, quantity if intent.side == Side.BUY else quantity.copy_negate())
        return total

    def pending_sell_quantity(self, instrument: str) -> Decimal:
        """Aggregate account sell leaves across every strategy and route."""
        self._check_thread()
        total = Decimal(0)
        for row in self._db.execute("SELECT payload,filled,state FROM execution_intents"):
            if row["state"] not in {*self.UNRESOLVED, "CANCEL_PENDING"}:
                continue
            intent = OrderIntent.model_validate_json(row["payload"])
            if intent.instrument.key == instrument and intent.side == Side.SELL:
                leaves = exact_difference(intent.quantity, Decimal(row["filled"]))
                total = exact_sum(total, leaves)
        return total

    # ------------------------------------------------------- feedback outbox
    def claim_source_event(self, strategy_id: str, event_key: str, document: dict, *,
                           runtime_id: str, restored_checkpoint_sha256: str | None = None) -> bool:
        """Durably claim a source input before invoking strategy code.

        A duplicate identical input is a no-op; reuse of an identity for changed
        contents is an integrity fault. Claims share the existing execution
        ledger and are bounded by the feedback outbox budget.
        """
        self._check_thread()
        if (not isinstance(strategy_id, str) or not strategy_id
                or len(strategy_id.encode("utf-8")) > 128
                or not isinstance(event_key, str) or not event_key
                or len(event_key.encode("utf-8")) > 512 or not isinstance(document, dict)
                or not isinstance(runtime_id, str) or len(runtime_id) != 32
                or any(ch not in "0123456789abcdef" for ch in runtime_id)):
            raise ValueError("Source event claim requires bounded explicit identities and document")
        body = self._bounded_json(document)
        body_bytes = len(body.encode("utf-8"))
        if body_bytes > self.max_feedback_bytes:
            raise RuntimeError("Source event claim exceeds its explicit byte budget")
        fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self.transaction():
            old = self._db.execute(
                "SELECT fingerprint FROM execution_source_events WHERE strategy_id=? AND event_key=?",
                (strategy_id, event_key)).fetchone()
            if old is not None:
                if old[0] != fingerprint:
                    raise ValueError("Source event identity conflicts with its original contents")
                return False
            prior = self._db.execute(
                "SELECT runtime_id FROM execution_source_events WHERE strategy_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (strategy_id,)).fetchone()
            if prior is not None and prior["runtime_id"] != runtime_id:
                if not isinstance(restored_checkpoint_sha256, str):
                    raise RuntimeError("New source event requires a restored source-state contract checkpoint")
                checkpoint = self._db.execute(
                    "SELECT document,sha256 FROM execution_source_checkpoints WHERE strategy_id=?",
                    (strategy_id,)).fetchone()
                if checkpoint is None or checkpoint["sha256"] != restored_checkpoint_sha256:
                    raise RuntimeError("Source recovery checkpoint token is missing or stale")
                body = checkpoint["document"]
                if hashlib.sha256(body.encode("utf-8")).hexdigest() != checkpoint["sha256"]:
                    raise RuntimeError("Source recovery checkpoint is corrupt")
                saved = json.loads(body)
                current_cut = self.source_checkpoint_cut(strategy_id)
                if current_cut.get("pending_feedback") != 0 or saved.get("cut") != current_cut:
                    raise RuntimeError("Source recovery checkpoint no longer matches the durable input cut")
            count, total = self._db.execute(
                "SELECT COUNT(*),COALESCE(SUM(LENGTH(fingerprint)),0) FROM execution_source_events "
                "WHERE strategy_id=?", (strategy_id,)).fetchone()
            if count >= self.max_feedback or total + len(fingerprint) > self.max_feedback_bytes:
                raise RuntimeError("Source event claim budget exhausted")
            self._db.execute(
                "INSERT INTO execution_source_events(strategy_id,event_key,fingerprint,runtime_id) "
                "VALUES(?,?,?,?)", (strategy_id, event_key, fingerprint, runtime_id))
            return True

    def source_checkpoint_cut(self, strategy_id: str) -> dict:
        """Return the bounded source-input and claimed-feedback cursor pair."""
        self._check_thread()
        if not isinstance(strategy_id, str) or not strategy_id:
            raise ValueError("Strategy identity must be explicit")
        latest = self._db.execute(
            "SELECT event_key,fingerprint FROM execution_source_events "
            "WHERE strategy_id=? ORDER BY rowid DESC LIMIT 1", (strategy_id,)).fetchone()
        if latest is None:
            raise ValueError("Source checkpoint requires a durable market-event claim")
        feedback_cursor, pending = self._db.execute(
            "SELECT COALESCE(MAX(seq),0),COALESCE(SUM(CASE WHEN claimed=0 THEN 1 ELSE 0 END),0) "
            "FROM execution_feedback WHERE strategy_id=?", (strategy_id,)).fetchone()
        runtime_id = self._db.execute(
            "SELECT runtime_id FROM execution_source_events WHERE strategy_id=? AND event_key=?",
            (strategy_id, latest["event_key"])).fetchone()[0]
        return {"market_event_id": latest["event_key"],
                "market_claim_sha256": latest["fingerprint"],
                "runtime_id": runtime_id,
                "feedback_cursor": int(feedback_cursor),
                "pending_feedback": int(pending)}

    def save_source_checkpoint(self, strategy_id: str, document: dict) -> str:
        """Persist one bounded source state only at its still-current quiescent cut."""
        self._check_thread()
        if not isinstance(strategy_id, str) or not strategy_id or not isinstance(document, dict):
            raise ValueError("Source checkpoint requires its strategy identity and document")
        body = self._bounded_json(document)
        if len(body.encode("utf-8")) > min(self.max_evidence_bytes, 524_288):
            raise ValueError("Source checkpoint exceeds its explicit ledger byte budget")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        cut = document.get("cut")
        with self.transaction():
            current = self.source_checkpoint_cut(strategy_id)
            if current.get("pending_feedback") != 0 or cut != current:
                raise RuntimeError("Source checkpoint cut changed or has unclaimed feedback")
            self._db.execute("INSERT INTO execution_source_checkpoints VALUES(?,?,?,?) "
                "ON CONFLICT(strategy_id) DO UPDATE SET document=excluded.document,"
                "sha256=excluded.sha256,updated_ns=excluded.updated_ns",
                (strategy_id, body, digest, time.time_ns()))
        return digest

    def load_source_checkpoint(self, strategy_id: str) -> dict:
        """Load and verify a source checkpoint without accepting stale cursor cuts."""
        self._check_thread()
        row = self._db.execute(
            "SELECT document,sha256 FROM execution_source_checkpoints WHERE strategy_id=?",
            (strategy_id,)).fetchone()
        if row is None:
            raise ValueError("No durable source checkpoint exists")
        body = row["document"]
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != row["sha256"]:
            raise ValueError("Durable source checkpoint hash is corrupt")
        value = json.loads(body)
        if not isinstance(value, dict) or value.get("strategy_id") != strategy_id:
            raise ValueError("Durable source checkpoint identity is corrupt")
        return {"document": value, "sha256": row["sha256"]}


    def record_feedback(self, strategy_id: str, event_id: str, event: dict) -> bool:
        """Durably enqueue one attributed factual event before any callback."""
        self._check_thread()
        if (not isinstance(strategy_id, str) or not strategy_id or len(strategy_id.encode("utf-8")) > 128
                or not isinstance(event_id, str) or not event_id or len(event_id.encode("utf-8")) > 512):
            raise ValueError("Feedback requires bounded strategy/event identities")
        if not isinstance(event, dict):
            raise ValueError("Feedback event must be an explicit mapping")
        body = self._bounded_json(event)
        if len(body.encode("utf-8")) > self.max_feedback_bytes:
            raise ValueError("Feedback event exceeds its explicit byte budget")
        if self._db.in_transaction:
            return self._record_feedback_tx(strategy_id, event_id, event)
        with self.transaction():
            return self._record_feedback_tx(strategy_id, event_id, event)

    def _record_feedback_tx(self, strategy_id: str, event_id: str, event: dict) -> bool:
        if (not isinstance(strategy_id, str) or not strategy_id or len(strategy_id.encode("utf-8")) > 128
                or not isinstance(event_id, str) or not event_id or len(event_id.encode("utf-8")) > 512):
            raise ValueError("Feedback requires bounded strategy/event identities")
        if not isinstance(event, dict):
            raise ValueError("Feedback event must be an explicit mapping")
        body = self._bounded_json(event)
        if len(body.encode("utf-8")) > self.max_feedback_bytes:
            raise ValueError("Feedback event exceeds its explicit byte budget")
        old = self._db.execute(
                "SELECT event FROM execution_feedback WHERE strategy_id=? AND event_id=?",
                (strategy_id, event_id)).fetchone()
        if old is not None:
            if old[0] != body:
                raise ValueError("Feedback identity conflicts with its original contents")
            return False
        count, total = self._db.execute(
                "SELECT COUNT(*),COALESCE(SUM(LENGTH(event)),0) FROM execution_feedback WHERE strategy_id=?",
                (strategy_id,)).fetchone()
        if count >= self.max_feedback or total + len(body.encode("utf-8")) > self.max_feedback_bytes:
            raise RuntimeError("Feedback outbox budget exhausted")
        self._db.execute(
                "INSERT INTO execution_feedback(strategy_id,event_id,event,claimed) VALUES(?,?,?,0)",
                (strategy_id, event_id, body))
        return True

    def feedback(self, strategy_id: str, *, after: int = 0, limit: int | None = None) -> list[dict]:
        self._check_thread()
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("Feedback cursor must be a nonnegative integer")
        if limit is None:
            limit = self.max_feedback
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= self.max_feedback:
            raise ValueError("Feedback limit must be within its explicit bound")
        rows = self._db.execute(
            "SELECT seq,event_id,event,claimed FROM execution_feedback "
            "WHERE strategy_id=? AND seq>? ORDER BY seq LIMIT ?",
            (strategy_id, after, limit)).fetchall()
        return [{"seq": row["seq"], "event_id": row["event_id"],
                 "event": json.loads(row["event"]), "claimed": bool(row["claimed"])} for row in rows]

    def claim_feedback(self, strategy_id: str, *, limit: int) -> list[dict]:
        """Claim unclaimed events transactionally; a crash cannot replay them."""
        self._check_thread()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= self.max_feedback:
            raise ValueError("Feedback claim limit must be within its explicit bound")
        with self.transaction():
            rows = self._db.execute(
                "SELECT seq,event_id,event FROM execution_feedback "
                "WHERE strategy_id=? AND claimed=0 ORDER BY seq LIMIT ?",
                (strategy_id, limit)).fetchall()
            for row in rows:
                self._db.execute("UPDATE execution_feedback SET claimed=1 WHERE seq=?", (row["seq"],))
            return [{"seq": row["seq"], "event_id": row["event_id"],
                     "event": json.loads(row["event"])} for row in rows]

    def close(self):
        if not self._closed:
            if threading.get_ident() != self._thread:
                raise RuntimeError('Execution ledger must be closed by its owning thread')
            self._db.close()
            self._lease.close()
            self._authority.close()
            self._closed = True
