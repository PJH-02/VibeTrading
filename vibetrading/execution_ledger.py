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

from .models import OrderIntent, Side
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

    def __init__(self, path: Path, scope: AccountScope, *, max_intents: int,
                 max_evidence_bytes: int, max_fills: int, authority_root: Path):
        for value in (max_intents, max_evidence_bytes, max_fills):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Execution ledger budgets must be explicit positive integers")
        self.path = Path(path).resolve()
        self.scope = scope
        self.max_intents, self.max_evidence_bytes, self.max_fills = max_intents, max_evidence_bytes, max_fills
        self._thread = threading.get_ident()
        self._closed = False
        self._authority = ProcessLease.account(authority_root, scope.venue, scope.account, scope.environment)
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
                    fees TEXT NOT NULL DEFAULT '0',response TEXT,updated_ns INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_attempts(
                    intent_id TEXT PRIMARY KEY,state TEXT NOT NULL,updated_ns INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_fills(
                    fill_id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_positions(instrument TEXT PRIMARY KEY,quantity TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_controls(
                    scope TEXT PRIMARY KEY,epoch INTEGER NOT NULL,closed INTEGER NOT NULL);
            """)
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
                  epochs: dict[str, int], reserve_unit: Decimal, legacy: bool = False) -> tuple[bool, dict]:
        quantity = positive_decimal(intent.quantity, "quantity")
        unit = positive_decimal(reserve_unit, "reservation unit")
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
                reservation,reserve_unit,updated_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (intent.intent_id, intent.client_order_id, self.fingerprint(intent), payload, proof,
                 expiry_ns, canonical(epochs), state, str(exact_product(quantity, unit)), str(unit), time.time_ns()))
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

    def outcome(self, identity: str, state: str, response: dict | None = None):
        if state not in {"UNKNOWN", "SUBMITTED", "BLOCKED", "PRE_SEND_FAILED"}:
            raise ValueError("Unsupported execution outcome")
        body = self._bounded_json(response or {})
        with self.transaction():
            row = self.get(identity)
            if not row:
                raise ValueError("Unknown intent")
            # ACK after a full fill must not resurrect an unresolved order.
            effective = "FILLED" if row["state"] == "FILLED" else state
            release = state in {"BLOCKED", "PRE_SEND_FAILED"}
            self._db.execute("UPDATE execution_intents SET state=?,response=?,reservation=?,updated_ns=? WHERE intent_id=?",
                (effective, body, "0" if release else row["reservation"], time.time_ns(), identity))
            self._db.execute("UPDATE execution_attempts SET state=?,updated_ns=? WHERE intent_id=?", (effective, time.time_ns(), identity))

    def apply_fill_once(self, identity: str, fill_id: str, *, quantity, price, fee) -> bool:
        qty, unit, fees = (positive_decimal(quantity, "fill quantity"), positive_decimal(price, "fill price"),
                           positive_decimal(fee, "fee", zero=True))
        if not isinstance(fill_id, str) or not fill_id or len(fill_id) > 256:
            raise ValueError("An explicit bounded provider-scoped fill ID is required")
        stamp = canonical([identity, str(qty), str(unit), str(fees)])
        with self.transaction():
            old = self._db.execute("SELECT fingerprint FROM execution_fills WHERE fill_id=?", (fill_id,)).fetchone()
            if old:
                if old[0] != stamp:
                    raise ValueError("Fill identity conflicts with its original contents")
                return False
            row = self.get(identity)
            if not row or row["state"] not in {*self.UNRESOLVED, "FILLED"}:
                raise ValueError("Fill requires an existing unresolved/filled intent")
            intent = OrderIntent.model_validate_json(row["payload"])
            filled = exact_sum(Decimal(row["filled"]), qty)
            if filled > intent.quantity:
                raise ValueError("Fill exceeds intent quantity")
            if self._db.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0] >= self.max_fills:
                raise RuntimeError("Execution fill row budget exhausted")
            key = intent.instrument.key
            prior = self._db.execute("SELECT quantity FROM execution_positions WHERE instrument=?", (key,)).fetchone()
            position = exact_sum(Decimal(prior[0]) if prior else Decimal(0), qty if intent.side == Side.BUY else qty.copy_negate())
            self._db.execute("INSERT INTO execution_fills VALUES(?,?,?)", (fill_id, identity, stamp))
            self._db.execute("INSERT INTO execution_positions VALUES(?,?) ON CONFLICT(instrument) DO UPDATE SET quantity=excluded.quantity", (key, str(position)))
            remaining = exact_difference(intent.quantity, filled)
            state = "FILLED" if remaining == 0 else row["state"]
            self._db.execute("UPDATE execution_intents SET filled=?,notional=?,fees=?,reservation=?,state=?,updated_ns=? WHERE intent_id=?",
                (str(filled), str(exact_sum(Decimal(row["notional"]), exact_product(qty, unit))), str(exact_sum(Decimal(row["fees"]), fees)),
                 str(exact_product(remaining, Decimal(row["reserve_unit"]))), state, time.time_ns(), identity))
            self._db.execute("UPDATE owner_metadata SET value=? WHERE key='account_version'",
                             (str(self.account_version() + 1),))
        return True

    def position(self, instrument: str) -> Decimal:
        self._check_thread()
        row = self._db.execute("SELECT quantity FROM execution_positions WHERE instrument=?", (instrument,)).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def close(self):
        if not self._closed:
            if threading.get_ident() != self._thread:
                raise RuntimeError('Execution ledger must be closed by its owning thread')
            self._db.close()
            self._lease.close()
            self._authority.close()
            self._closed = True
