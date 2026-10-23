"""Bounded local paper portfolio authority. No venue client or live margin claim."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .execution_ledger import (AccountScope, canonical, exact_difference,
                               exact_product, exact_sum, positive_decimal as _positive_decimal)
from .models import OrderIntent, Side
from .process_lease import ProcessLease
from .trading_contracts import ProductKind, ProductSpec, TradingMode, bounded_identity, validate_scope


def positive_decimal(value, name, *, zero=False):
    try:
        return _positive_decimal(value, name, zero=zero)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(name + " requires an explicit finite decimal") from exc


def signed_decimal(value, name):
    number = Decimal(str(value))
    positive_decimal(number.copy_abs(), name, zero=True)
    return number


def divide(a, b):
    with localcontext() as context:
        context.prec = 128
        return a / b


def aligned(value, step):
    with localcontext() as context:
        context.prec = 128
        return value % step == 0


def reserve_product(quantity, unit):
    with localcontext() as context:
        context.prec = 128
        context.rounding = ROUND_CEILING
        return quantity * unit


class PortfolioLedger:
    """One cooperative scope owner; paper cash and isolated linear simulation only.

    Paths live inside owner_root/<mode>. Owner-root protection/ACL and a local
    volume are deployment requirements; this lease cannot fence other hosts.
    Balances and physical positions are simulated account facts, never evidence
    of actual venue holdings. Explicit snapshots reconcile deviations into gaps.
    """

    CAPABILITIES = {"cash": "paper", "linear_perpetual": "paper simulation assumption",
                    "inverse_perpetual": "unsupported", "hedge": "unsupported",
                    "sandbox_margin": "unverified", "live_margin": "unverified"}

    def __init__(self, path, scope: AccountScope, *, owner_root, mode,
                 max_orders: int, max_fills: int, max_events: int,
                 max_document_bytes: int, max_db_bytes: int, max_wal_bytes: int):
        self.mode = validate_scope(scope, mode)
        self.scope = scope
        for name, value in (("max_orders", max_orders), ("max_fills", max_fills),
                            ("max_events", max_events), ("max_document_bytes", max_document_bytes),
                            ("max_db_bytes", max_db_bytes), ("max_wal_bytes", max_wal_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(name + " must be an explicit positive integer")
            setattr(self, name, value)
        self.path, self.owner_root = Path(path).resolve(), Path(owner_root).resolve()
        if not self.path.is_relative_to(self.owner_root / self.mode.value):
            raise ValueError("Portfolio path must be inside protected owner_root/<mode>")
        self._thread, self._closed = threading.get_ident(), False
        self._lease = ProcessLease(self.owner_root / ".portfolio-owners" /
                                   (hashlib.sha256(scope.key.encode()).hexdigest() + ".lock"))
        self._lease.acquire()
        try:
            binding = self._lease.path.with_suffix(".binding")
            expected = canonical({"scope": scope.key, "mode": self.mode.value, "path": str(self.path)})
            if binding.exists() and binding.read_text(encoding="utf-8") != expected:
                raise ValueError("Account scope is permanently bound to a different portfolio path")
            if not binding.exists():
                # Exclusive creation plus the scope lease avoids replacing a binding.
                with binding.open("x", encoding="utf-8") as handle:
                    handle.write(expected)
                    handle.flush()
                    import os
                    os.fsync(handle.fileno())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, isolation_level=None, timeout=2)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA wal_autocheckpoint=1")
            self._db.execute("PRAGMA journal_size_limit=" + str(max_wal_bytes))
            self._db.execute("PRAGMA max_page_count=" + str(max(1, max_db_bytes // 4096)))
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_orders(
                  intent_id TEXT PRIMARY KEY,client_order_id TEXT UNIQUE NOT NULL,strategy TEXT NOT NULL,
                  fingerprint TEXT NOT NULL,payload TEXT NOT NULL,evidence TEXT NOT NULL,rules TEXT NOT NULL,
                  expiry_ns INTEGER NOT NULL,state TEXT NOT NULL,quantity TEXT NOT NULL,filled TEXT NOT NULL,
                  reservation TEXT NOT NULL,reserve_unit TEXT NOT NULL,reserve_currency TEXT NOT NULL,
                  instrument TEXT NOT NULL,side TEXT NOT NULL,reference_price TEXT NOT NULL,margin_rate TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS portfolio_order_state ON portfolio_orders(state);
                CREATE TABLE IF NOT EXISTS portfolio_receipts(kind TEXT NOT NULL,id TEXT NOT NULL,
                  fingerprint TEXT NOT NULL,document TEXT NOT NULL,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS portfolio_balances(currency TEXT PRIMARY KEY,amount TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_positions(strategy TEXT NOT NULL,instrument TEXT NOT NULL,
                  quantity TEXT NOT NULL,average TEXT NOT NULL,margin TEXT NOT NULL,PRIMARY KEY(strategy,instrument));
                CREATE TABLE IF NOT EXISTS portfolio_gaps(instrument TEXT PRIMARY KEY,document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_strategy_cash(strategy TEXT NOT NULL,currency TEXT NOT NULL,
                  amount TEXT NOT NULL,fees TEXT NOT NULL,funding TEXT NOT NULL,realized TEXT NOT NULL,
                  PRIMARY KEY(strategy,currency));
            """)
            with self.transaction():
                for key, value in (("scope", scope.key), ("mode", self.mode.value), ("path", str(self.path))):
                    old = self._db.execute("SELECT value FROM portfolio_metadata WHERE key=?", (key,)).fetchone()
                    if old and old[0] != value:
                        raise ValueError("Portfolio metadata scope/mode/path mismatch")
                    self._db.execute("INSERT OR IGNORE INTO portfolio_metadata VALUES(?,?)", (key, value))
                self._db.execute("INSERT OR IGNORE INTO portfolio_metadata VALUES('account_version','0')")
                self._db.execute("UPDATE portfolio_orders SET state='UNKNOWN' WHERE state IN ('OPEN','CANCEL_PENDING')")
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._lease.close()
            raise

    def _check(self):
        if self._closed or threading.get_ident() != self._thread:
            raise RuntimeError("Portfolio requires its open owning thread")

    @property
    def transaction_active(self):
        """Matcher memory may only be published after its own outer COMMIT."""
        self._check()
        return self._db.in_transaction

    def _document(self, value):
        text = canonical(value)
        if len(text.encode("utf-8")) > self.max_document_bytes:
            raise ValueError("Portfolio document byte budget exceeded")
        return text

    def _bounds(self):
        if self.path.exists() and self.path.stat().st_size > self.max_db_bytes:
            raise RuntimeError("Portfolio database byte budget exhausted")
        wal = Path(str(self.path) + "-wal")
        if wal.exists() and wal.stat().st_size > self.max_wal_bytes:
            raise RuntimeError("Portfolio WAL byte budget exhausted")
        for table, limit, where in (("portfolio_orders", self.max_orders, ""),
                                   ("portfolio_receipts", self.max_fills, " WHERE kind='fill'"),
                                   ("portfolio_receipts", self.max_events, " WHERE kind!='fill'")):
            if self._db.execute("SELECT COUNT(*) FROM " + table + where).fetchone()[0] > limit:
                raise RuntimeError("Portfolio row budget exhausted")

    @contextmanager
    def transaction(self):
        """Join inner ledger operations; the outer caller commits matcher state atomically."""
        self._check()
        if self._db.in_transaction:
            # A caller may handle an inner rejection and continue its transaction.
            # Savepoints ensure its rejected receipt/position changes cannot leak.
            marker = "nested_" + str(getattr(self, "_nest", 0))
            self._nest = getattr(self, "_nest", 0) + 1
            self._db.execute("SAVEPOINT " + marker)
            try:
                yield self._db
                self._db.execute("RELEASE " + marker)
            except BaseException:
                self._db.execute("ROLLBACK TO " + marker)
                self._db.execute("RELEASE " + marker)
                raise
            return
        self._bounds()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
            self._bounds()
            # At most the entire database can become dirty in this transaction;
            # reserve that conservative WAL bound before the irreversible commit.
            pages = self._db.execute("PRAGMA page_count").fetchone()[0]
            size = self._db.execute("PRAGMA page_size").fetchone()[0]
            wal = Path(str(self.path) + "-wal")
            if (wal.stat().st_size if wal.exists() else 0) + pages * (size + 24) + 32 > self.max_wal_bytes:
                raise RuntimeError("Portfolio conservative WAL transaction budget exhausted")
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def _receipt(self, kind, identity, document):
        bounded_identity(identity, kind + " id")
        text = self._document(document)
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        old = self._db.execute("SELECT fingerprint FROM portfolio_receipts WHERE kind=? AND id=?", (kind, identity)).fetchone()
        if old:
            if old[0] != fingerprint:
                raise ValueError("Immutable " + kind + " receipt collision")
            return False
        self._db.execute("INSERT INTO portfolio_receipts VALUES(?,?,?,?)", (kind, identity, fingerprint, text))
        return True

    def paper_state(self):
        self._check()
        row = self._db.execute("SELECT value FROM portfolio_metadata WHERE key='matcher'").fetchone()
        return json.loads(row[0]) if row else None

    def pragmas(self):
        self._check()
        return {"journal_mode": self._db.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": self._db.execute("PRAGMA synchronous").fetchone()[0]}

    def save_paper_state(self, state):
        if self.mode != TradingMode.PAPER:
            raise ValueError("Paper state requires paper mode")
        text = self._document(state)
        with self.transaction():
            self._db.execute("INSERT OR REPLACE INTO portfolio_metadata VALUES('matcher',?)", (text,))

    def claim_strategy_event(self, strategy, config_version, event_id, evidence):
        """Commit an at-most-once callback claim before running user code.

        A crash after this receipt intentionally prevents automatic callback
        replay; the caller must report the unsent-action ambiguity explicitly.
        Registration evidence can use the reserved event ID '@registration'.
        """
        self._check()
        if self.mode != TradingMode.PAPER or self.transaction_active:
            raise RuntimeError("Strategy event claim requires a standalone paper commit")
        for name, value in (("strategy", strategy), ("config_version", config_version), ("event_id", event_id)):
            bounded_identity(value, name)
        if not evidence:
            raise ValueError("Strategy event claim requires immutable evidence")
        identity = hashlib.sha256(canonical([self.scope.key, strategy, config_version, event_id]).encode()).hexdigest()
        with self.transaction():
            return self._receipt("strategy_event", identity,
                                 {"strategy": strategy, "config_version": config_version,
                                  "event_id": event_id, "evidence": evidence})

    def has_strategy_dispatch(self, strategy):
        """Read-only evidence that a strategy dispatched beyond registration.

        Streams only the existing bounded strategy_event receipts. Registration
        alone is harmless; any action claim, including one written under a
        different config version, fences a fresh instance. Unknown or malformed
        receipt evidence fails closed instead of being ignored.
        """
        self._check()
        if self.mode != TradingMode.PAPER:
            raise ValueError("Strategy dispatch evidence requires paper mode")
        bounded_identity(strategy, "strategy")
        found = False
        rows = self._db.execute(
            "SELECT fingerprint,document FROM portfolio_receipts WHERE kind='strategy_event'")
        for fingerprint, document in rows:
            if not isinstance(fingerprint, str) or not isinstance(document, str):
                raise ValueError("Malformed strategy event receipt")
            try:
                receipt = json.loads(document)
                if not isinstance(receipt, dict) or set(receipt) != {
                        "strategy", "config_version", "event_id", "evidence"}:
                    raise ValueError("Strategy event receipt schema differs")
                for name in ("strategy", "config_version", "event_id"):
                    bounded_identity(receipt[name], "receipt " + name)
                if not receipt["evidence"]:
                    raise ValueError("Strategy event receipt lacks evidence")
            except (TypeError, ValueError) as exc:
                raise ValueError("Malformed strategy event receipt") from exc
            if hashlib.sha256(document.encode("utf-8")).hexdigest() != fingerprint:
                raise ValueError("Malformed strategy event receipt")
            if receipt["strategy"] == strategy and receipt["event_id"] != "@registration":
                found = True
        return found

    def record_group_fact(self, group_id, kind, fact_id, document, *, max_groups, max_observations):
        """Append bounded observational group facts; never mutate child orders."""
        self._check()
        if self.mode != TradingMode.PAPER:
            raise ValueError("Execution group facts require a paper coordinator ledger")
        for name, value in (("group_id", group_id), ("fact_id", fact_id)):
            bounded_identity(value, name)
        if kind not in {"plan", "observation"}:
            raise ValueError("Unsupported execution group fact kind")
        if kind == "plan" and fact_id != "plan":
            raise ValueError("Execution group plan has one immutable identity")
        for value in (max_groups, max_observations):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Group lifetime budgets must be explicit positive integers")
        identity = hashlib.sha256(canonical([group_id, fact_id]).encode()).hexdigest()
        body = {"group_id": group_id, "kind": kind, "fact_id": fact_id, "document": document}
        with self.transaction():
            plan_identity = hashlib.sha256(canonical([group_id, "plan"]).encode()).hexdigest()
            if kind == "observation" and not self._db.execute(
                    "SELECT 1 FROM portfolio_receipts WHERE kind='group_plan' AND id=?", (plan_identity,)).fetchone():
                raise ValueError("Execution group observation requires an immutable plan")
            created = self._receipt("group_" + kind, identity, body)
            for receipt_kind, limit in (("group_plan", max_groups), ("group_observation", max_observations)):
                count = self._db.execute("SELECT COUNT(*) FROM portfolio_receipts WHERE kind=?", (receipt_kind,)).fetchone()[0]
                if count > limit:
                    raise RuntimeError("Execution group lifetime row budget exhausted")
            return created

    def group_facts(self, group_id=None):
        """Bounded append order, including terminal groups; no child-account snapshot claim."""
        self._check()
        if group_id is not None:
            bounded_identity(group_id, "group_id")
        facts = [json.loads(row[0]) for row in self._db.execute(
            "SELECT document FROM portfolio_receipts WHERE kind IN ('group_plan','group_observation') ORDER BY rowid")]
        return [fact for fact in facts if group_id is None or fact["group_id"] == group_id]

    def _balance(self, currency):
        row = self._db.execute("SELECT amount FROM portfolio_balances WHERE currency=?", (currency,)).fetchone()
        return Decimal(row[0]) if row else None

    def _credit(self, currency, delta):
        old = self._balance(currency)
        if old is None:
            raise ValueError("Unverified currency balance: " + currency)
        self._db.execute("UPDATE portfolio_balances SET amount=? WHERE currency=?", (str(exact_sum(old, delta)), currency))

    def seed_balance(self, currency, amount, evidence):
        """One immutable simulated initial balance per currency; never venue equity."""
        if self.mode != TradingMode.PAPER:
            raise ValueError("Only explicit paper balance initialization is supported")
        bounded_identity(currency, "currency")
        amount = positive_decimal(amount, "balance", zero=True)
        if not evidence:
            raise ValueError("Balance initialization requires evidence")
        with self.transaction():
            if self._receipt("seed", currency, {"amount": str(amount), "evidence": evidence}):
                self._db.execute("INSERT INTO portfolio_balances VALUES(?,?)", (currency, str(amount)))

    def _position(self, strategy, instrument):
        row = self._db.execute("SELECT quantity,average,margin FROM portfolio_positions WHERE strategy=? AND instrument=?", (strategy, instrument)).fetchone()
        return tuple(Decimal(v) for v in row) if row else (Decimal(0), Decimal(0), Decimal(0))

    def position(self, strategy, instrument):
        self._check()
        return self._position(strategy, instrument)[0]

    def _strategy_credit(self, strategy, currency, amount=Decimal(0), *, fee=Decimal(0), funding=Decimal(0), realized=Decimal(0)):
        old = self._db.execute("SELECT amount,fees,funding,realized FROM portfolio_strategy_cash WHERE strategy=? AND currency=?", (strategy, currency)).fetchone()
        values = tuple(Decimal(v) for v in old) if old else (Decimal(0),) * 4
        values = [str(exact_sum(a, b)) for a, b in zip(values, (amount, fee, funding, realized))]
        self._db.execute("INSERT OR REPLACE INTO portfolio_strategy_cash VALUES(?,?,?,?,?,?)", (strategy, currency, *values))

    def _reserved(self, currency, strategy=None):
        query = "SELECT reservation FROM portfolio_orders WHERE reserve_currency=?"
        args = [currency]
        if strategy is not None:
            query += " AND strategy=?"
            args.append(strategy)
        return exact_sum(*(Decimal(row[0]) for row in self._db.execute(query, args)))

    def _margin(self, strategy=None):
        query = "SELECT margin FROM portfolio_positions WHERE strategy!='@physical'"
        args = []
        if strategy is not None:
            query += " AND strategy=?"
            args.append(strategy)
        return exact_sum(*(Decimal(row[0]) for row in self._db.execute(query, args)))

    def _capital(self, strategy=None):
        total = self._margin(strategy)
        for row in self._db.execute("SELECT * FROM portfolio_positions WHERE strategy!='@physical'"):
            if strategy is not None and row["strategy"] != strategy:
                continue
            spec = self._db.execute("SELECT rules FROM portfolio_orders WHERE instrument=? LIMIT 1", (row["instrument"],)).fetchone()
            rules = ProductSpec.from_document(json.loads(spec[0]))
            if rules.kind == ProductKind.CASH:
                total = exact_sum(total, exact_product(Decimal(row["quantity"]).copy_abs(), exact_product(Decimal(row["average"]), rules.multiplier)))
        return total

    def get(self, identity):
        self._check()
        row = self._db.execute("SELECT * FROM portfolio_orders WHERE intent_id=?", (identity,)).fetchone()
        return dict(row) if row else None

    def active_orders(self):
        self._check()
        return [dict(row) for row in self._db.execute("SELECT * FROM portfolio_orders WHERE state IN ('OPEN','UNKNOWN','CANCEL_PENDING')")]

    def account_version(self):
        self._check()
        return int(self._db.execute("SELECT value FROM portfolio_metadata WHERE key='account_version'").fetchone()[0])

    def _advance_version(self):
        self._db.execute("UPDATE portfolio_metadata SET value=? WHERE key='account_version'", (str(self.account_version() + 1),))

    def _gross_notional(self, strategy=None):
        # Gross virtual exposure plus every pending side; opposing strategies never offset.
        total = Decimal(0)
        for row in self._db.execute("SELECT * FROM portfolio_orders WHERE state IN ('OPEN','UNKNOWN','CANCEL_PENDING')"):
            if strategy is None or row["strategy"] == strategy:
                rules = ProductSpec.from_document(json.loads(row["rules"]))
                total = exact_sum(total, exact_product(exact_difference(Decimal(row["quantity"]), Decimal(row["filled"])),
                                  exact_product(Decimal(row["reference_price"]), rules.multiplier))
                                if row["state"] not in ("CANCELED", "EXPIRED", "REJECTED", "FILLED") else Decimal(0))
        for row in self._db.execute("SELECT * FROM portfolio_positions WHERE strategy!='@physical'"):
            if strategy is None or row["strategy"] == strategy:
                spec = self._db.execute("SELECT rules FROM portfolio_orders WHERE instrument=? LIMIT 1", (row["instrument"],)).fetchone()
                multiplier = ProductSpec.from_document(json.loads(spec[0])).multiplier
                total = exact_sum(total, exact_product(Decimal(row["quantity"]).copy_abs(), exact_product(Decimal(row["average"]), multiplier)))
        return total

    def admit_order(self, intent: OrderIntent, strategy, evidence, expiry_ns, rules: ProductSpec, *,
                    funding_buffer, account_notional_limit, strategy_notional_limit,
                    account_capital_limit, strategy_capital_limit,
                    reference_price=None, margin_rate=None, now_ns=None):
        """Reserve worst-case gross cash/margin with explicit budgets in one transaction."""
        self._check()
        if self.mode != TradingMode.PAPER:
            raise ValueError("Live/sandbox authoritative margin is UNVERIFIED; admission unavailable")
        bounded_identity(strategy, "strategy")
        if strategy == "@physical":
            raise ValueError("Reserved strategy namespace")
        rules.supported()
        if intent.instrument.model_dump(mode="json") != rules.instrument.model_dump(mode="json") or intent.instrument.venue.value != self.scope.venue:
            raise ValueError("Intent/product/account identity mismatch")
        if intent.side not in (Side.BUY, Side.SELL):
            raise ValueError("Explicit buy/sell side required")
        quantity = positive_decimal(intent.quantity, "quantity")
        price = positive_decimal(intent.limit_price if intent.limit_price is not None else reference_price, "reservation price")
        if not aligned(quantity, rules.lot_size) or not aligned(price, rules.tick_size):
            raise ValueError("Quantity/price violates lot/tick")
        notional = exact_product(quantity, exact_product(price, rules.multiplier))
        if notional < rules.min_notional:
            raise ValueError("Minimum notional not met")
        buffer = positive_decimal(funding_buffer, "funding buffer", zero=True)
        limits = [positive_decimal(v, "explicit risk limit") for v in (account_notional_limit, strategy_notional_limit, account_capital_limit, strategy_capital_limit)]
        rate = Decimal(0) if rules.kind == ProductKind.CASH else positive_decimal(margin_rate, "paper simulation margin rate")
        if rate > 1:
            raise ValueError("Simulation margin rate exceeds one")
        fee_rate = max(Decimal(0), rules.maker_fee_rate, rules.taker_fee_rate)
        # Nonterminating funding-buffer allocation rounds toward more reserve.
        # It must never shave a fraction from a required cash/margin buffer.
        with localcontext() as context:
            context.prec = 128
            context.rounding = ROUND_CEILING
            unit = (price * rules.multiplier * (Decimal(1) if rules.kind == ProductKind.CASH and intent.side == Side.BUY else rate)
                    + price * rules.multiplier * fee_rate + buffer / quantity)
        reserve = reserve_product(quantity, unit)
        if not evidence:
            raise ValueError("Admission requires immutable evidence")
        payload = self._document(intent.model_dump(mode="json"))
        proof, spec = self._document(evidence), self._document(rules.to_document())
        fingerprint = hashlib.sha256(self._document([payload, strategy, proof, spec, expiry_ns, str(price), str(rate), str(buffer), list(map(str, limits))]).encode()).hexdigest()
        with self.transaction():
            old = self.get(intent.intent_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Immutable intent/evidence collision")
                return False, old
            if isinstance(expiry_ns, bool) or not isinstance(expiry_ns, int) or expiry_ns <= (time.time_ns() if now_ns is None else now_ns) or expiry_ns > 2**63 - 1:
                raise ValueError("Order expiry is invalid or elapsed")
            if self._db.execute("SELECT 1 FROM portfolio_gaps LIMIT 1").fetchone():
                raise RuntimeError("External position gap blocks new risk")
            existing_spec = self._db.execute("SELECT rules,margin_rate FROM portfolio_orders WHERE instrument=? LIMIT 1", (rules.instrument.key,)).fetchone()
            if existing_spec and existing_spec[0] != spec:
                raise ValueError("Instrument rules cannot change while journal positions/history exist")
            if existing_spec and Decimal(existing_spec[1]) != rate:
                raise ValueError("Instrument simulation margin rate is pinned; explicit model migration is unsupported")
            if intent.metadata.get("reduce_only", False):
                held = self.position(strategy, rules.instrument.key)
                if not held or (held > 0) == (intent.side == Side.BUY) or quantity > held.copy_abs():
                    raise ValueError("Reduce-only claim does not reduce strategy position")
            # A single currency per account avoids any implicit FX total.
            currencies = [row[0] for row in self._db.execute("SELECT currency FROM portfolio_balances")]
            if currencies != [rules.collateral_currency]:
                raise ValueError("One explicitly verified collateral currency is required")
            if rules.kind == ProductKind.CASH and intent.side == Side.SELL:
                held = self._position(strategy, rules.instrument.key)[0]
                pending = exact_sum(*(exact_difference(Decimal(r[0]), Decimal(r[1])) for r in self._db.execute("SELECT quantity,filled FROM portfolio_orders WHERE strategy=? AND instrument=? AND side='sell' AND state IN ('OPEN','UNKNOWN','CANCEL_PENDING')", (strategy, rules.instrument.key))))
                if quantity > exact_difference(held, pending):
                    raise ValueError("Cash sell exceeds unreserved strategy holdings")
            margin, reserved = self._margin(), self._reserved(rules.collateral_currency)
            if exact_sum(reserved, margin, reserve) > self._balance(rules.collateral_currency):
                raise ValueError("Account available capital exhausted")
            if exact_sum(reserved, self._capital(), reserve) > limits[2]:
                raise ValueError("Account capital limit exceeded")
            if exact_sum(self._reserved(rules.collateral_currency, strategy), self._capital(strategy), reserve) > limits[3]:
                raise ValueError("Strategy capital limit exceeded")
            if exact_sum(self._gross_notional(), notional) > limits[0] or exact_sum(self._gross_notional(strategy), notional) > limits[1]:
                raise ValueError("Gross account/strategy notional limit exceeded")
            self._db.execute("INSERT INTO portfolio_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (intent.intent_id, intent.client_order_id, strategy, fingerprint, payload, proof, spec,
                              expiry_ns, "OPEN", str(quantity), "0", str(reserve), str(unit), rules.collateral_currency,
                              rules.instrument.key, intent.side.value, str(price), str(rate)))
            return True, self.get(intent.intent_id)

    def _update_position(self, strategy, instrument, delta, price, multiplier, rate):
        old, average, margin = self._position(strategy, instrument)
        new = exact_sum(old, delta)
        pnl = Decimal(0)
        if old and (old > 0) != (delta > 0):
            closed = min(old.copy_abs(), delta.copy_abs())
            pnl = exact_product(exact_product(closed, multiplier), exact_difference(price, average))
            if old < 0:
                pnl = pnl.copy_negate()
        if not new:
            average = Decimal(0)
        elif not old or (old > 0) != (new > 0):
            average = price
        elif (old > 0) == (delta > 0):
            average = divide(exact_sum(exact_product(old.copy_abs(), average), exact_product(delta.copy_abs(), price)), new.copy_abs())
        margin = exact_product(exact_product(new.copy_abs(), exact_product(average, multiplier)), rate)
        self._db.execute("INSERT OR REPLACE INTO portfolio_positions VALUES(?,?,?,?,?)", (strategy, instrument, str(new), str(average), str(margin)))
        return pnl

    def apply_fill(self, fill_id, intent_id, quantity, price, *, fee=Decimal(0),
                   fee_currency=None, order_id=None, position_side="net", group_id=None):
        quantity, price = positive_decimal(quantity, "fill quantity"), positive_decimal(price, "fill price")
        fee = signed_decimal(fee, "fee")
        with self.transaction():
            row = self.get(intent_id)
            if row is None:
                raise ValueError("Fill references unknown intent")
            rules = ProductSpec.from_document(json.loads(row["rules"]))
            currency = fee_currency or rules.settlement_currency
            if position_side != "net" or currency != rules.settlement_currency:
                raise ValueError("Unsupported position-side or fee currency")
            document = {"intent_id": intent_id, "quantity": str(quantity), "price": str(price), "fee": str(fee),
                        "fee_currency": currency, "order_id": order_id, "position_side": position_side,
                        "group_id": group_id, "strategy": row["strategy"], "instrument": row["instrument"], "side": row["side"]}
            if not self._receipt("fill", fill_id, document):
                return False
            filled = exact_sum(Decimal(row["filled"]), quantity)
            if filled > Decimal(row["quantity"]):
                raise ValueError("Fill exceeds admitted quantity")
            if not aligned(quantity, rules.lot_size) or not aligned(price, rules.tick_size):
                raise ValueError("Fill violates lot/tick")
            limit = json.loads(row["payload"]).get("limit_price")
            if limit is not None and ((row["side"] == "buy" and price > Decimal(limit)) or (row["side"] == "sell" and price < Decimal(limit))):
                raise ValueError("Fill violates order limit")
            delta = quantity if row["side"] == "buy" else quantity.copy_negate()
            if json.loads(row["payload"]).get("metadata", {}).get("reduce_only", False):
                held = self.position(row["strategy"], row["instrument"])
                if not held or (held > 0) == (delta > 0) or quantity > held.copy_abs():
                    raise ValueError("Reduce-only fill would open/increase risk")
            pnl = self._update_position(row["strategy"], row["instrument"], delta, price, rules.multiplier, Decimal(row["margin_rate"]))
            physical_pnl = self._update_position("@physical", row["instrument"], delta, price, rules.multiplier, Decimal(0))
            if rules.kind == ProductKind.CASH:
                change = exact_product(delta, exact_product(price, rules.multiplier)).copy_negate()
            else:
                change = physical_pnl
            self._credit(currency, change)
            self._credit(currency, fee.copy_negate())
            virtual_change = change if rules.kind == ProductKind.CASH else pnl
            self._strategy_credit(row["strategy"], currency, exact_difference(virtual_change, fee), fee=fee, realized=pnl)
            if rules.kind == ProductKind.LINEAR_PERPETUAL and physical_pnl != pnl:
                # Account netting and strategy inventory realize PnL at different
                # times. Keep this reconciliation category explicit.
                adjustment = exact_difference(physical_pnl, pnl)
                self._strategy_credit("@physical_netting_adjustment", currency, adjustment, realized=adjustment)
            remaining = exact_difference(Decimal(row["quantity"]), filled)
            terminal = row["state"] in ("CANCELED", "REJECTED", "EXPIRED")
            reserve = Decimal(0) if terminal else reserve_product(remaining, Decimal(row["reserve_unit"]))
            state = "FILLED" if not remaining else row["state"]
            self._db.execute("UPDATE portfolio_orders SET filled=?,reservation=?,state=? WHERE intent_id=?", (str(filled), str(reserve), state, intent_id))
            self._advance_version()
            if rules.kind == ProductKind.CASH and (self.position(row["strategy"], row["instrument"]) < 0 or self.position("@physical", row["instrument"]) < 0):
                # Late authoritative fills remain facts even after terminal
                # outcomes; unsupported cash short exposure blocks further risk.
                self._db.execute("INSERT OR REPLACE INTO portfolio_gaps VALUES(?,?)", ("@inventory:" + row["instrument"], self._document({"reason": "late fill created unsupported cash short"})))
            if self._balance(currency) < exact_sum(self._reserved(currency), self._margin()):
                self._db.execute("INSERT OR REPLACE INTO portfolio_gaps VALUES(?,?)", ("@capital", self._document({"reason": "fill exhausted simulated capital"})))
            return True

    def request_cancel(self, intent_id, strategy):
        with self.transaction():
            row = self.get(intent_id)
            if not row or row["strategy"] != strategy:
                raise ValueError("Cancel cannot access another strategy namespace")
            if row["state"] in ("OPEN", "UNKNOWN"):
                self._db.execute("UPDATE portfolio_orders SET state='CANCEL_PENDING' WHERE intent_id=?", (intent_id,))
            return self.get(intent_id)

    def _outcome(self, intent_id, state, confirmed, evidence):
        with self.transaction():
            row = self.get(intent_id)
            if row is None:
                raise ValueError("Unknown intent")
            if not confirmed:
                self._db.execute("UPDATE portfolio_orders SET state='UNKNOWN' WHERE intent_id=? AND state IN ('OPEN','CANCEL_PENDING','UNKNOWN')", (intent_id,))
            else:
                if not evidence:
                    raise ValueError("Confirmed terminal outcome requires authoritative evidence")
                if row["state"] in ("CANCELED", "EXPIRED", "REJECTED") and row["state"] != state:
                    raise ValueError("Contradictory terminal order outcome")
                self._receipt("outcome", intent_id + ":" + state, {"state": state, "evidence": evidence})
                self._db.execute("UPDATE portfolio_orders SET state=?,reservation='0' WHERE intent_id=? AND state!='FILLED'", (state, intent_id))
            return self.get(intent_id)

    def cancel(self, intent_id, *, confirmed=False, evidence=None):
        return self._outcome(intent_id, "CANCELED", confirmed, evidence)

    def expire(self, intent_id, *, confirmed=False, evidence=None):
        return self._outcome(intent_id, "EXPIRED", confirmed, evidence)

    def reject(self, intent_id, *, confirmed=False, evidence=None):
        return self._outcome(intent_id, "REJECTED", confirmed, evidence)

    def apply_funding(self, funding_id, instrument, amount, currency, evidence, *, strategy=None):
        amount = signed_decimal(amount, "funding amount")
        instrument = instrument.key if hasattr(instrument, "key") else instrument
        bounded_identity(instrument, "funding instrument")
        if not evidence:
            raise ValueError("Funding requires evidence")
        with self.transaction():
            specs = [ProductSpec.from_document(json.loads(r[0])) for r in self._db.execute("SELECT rules FROM portfolio_orders WHERE instrument=? LIMIT 1", (instrument,))]
            if not specs or specs[0].kind != ProductKind.LINEAR_PERPETUAL or specs[0].settlement_currency != currency:
                raise ValueError("Funding requires a known perpetual settlement currency")
            if strategy is not None and not self._db.execute("SELECT 1 FROM portfolio_orders WHERE strategy=? AND instrument=?", (strategy, instrument)).fetchone():
                raise ValueError("Unknown funding strategy attribution")
            if not self._receipt("funding", funding_id, {"instrument": instrument, "amount": str(amount), "currency": currency, "evidence": evidence, "strategy": strategy}):
                return False
            self._credit(currency, amount)
            self._strategy_credit(strategy or "@unallocated_funding", currency, amount, funding=amount)
            self._advance_version()
            if self._balance(currency) < exact_sum(self._reserved(currency), self._margin()):
                self._db.execute("INSERT OR REPLACE INTO portfolio_gaps VALUES('@capital',?)", (self._document({"reason": "funding exhausted simulated capital"}),))
            return True

    def reconcile_positions(self, snapshot_id, positions, evidence, *, account_version):
        """Compare supplied account facts; deviations stay explicit, without virtual allocation."""
        if not evidence:
            raise ValueError("Snapshot requires explicit evidence")
        normalized = {bounded_identity(k, "instrument"): str(signed_decimal(v, "position")) for k, v in positions.items()}
        with self.transaction():
            if not self._receipt("snapshot", snapshot_id, {"positions": normalized, "evidence": evidence, "account_version": account_version}):
                return False
            if isinstance(account_version, bool) or not isinstance(account_version, int) or account_version != self.account_version():
                raise ValueError("Stale/unverified snapshot account version")
            known = {r[0]: r[1] for r in self._db.execute("SELECT instrument,quantity FROM portfolio_positions WHERE strategy='@physical'")}
            previous = {r[0] for r in self._db.execute("SELECT instrument FROM portfolio_gaps WHERE instrument NOT LIKE '@%'")}
            for key in set(known) | set(normalized) | previous:
                expected, actual = Decimal(known.get(key, "0")), Decimal(normalized.get(key, "0"))
                if expected != actual:
                    self._db.execute("INSERT OR REPLACE INTO portfolio_gaps VALUES(?,?)", (key, self._document({"expected": str(expected), "external": str(actual), "snapshot_id": snapshot_id})))
                else:
                    self._db.execute("DELETE FROM portfolio_gaps WHERE instrument=?", (key,))
            return True

    def readiness(self):
        self._check()
        gaps = self._db.execute("SELECT COUNT(*) FROM portfolio_gaps").fetchone()[0]
        return {"ready": self.mode == TradingMode.PAPER and not gaps and self._db.execute("SELECT COUNT(*) FROM portfolio_balances").fetchone()[0] == 1,
                "mode": self.mode.value, "external_positions_verified": False,
                "margin_model": "paper simulation assumption" if self.mode == TradingMode.PAPER else "UNVERIFIED",
                "gap_count": gaps, "capabilities": dict(self.CAPABILITIES)}

    def report(self):
        self._check()
        positions, virtual = {}, {}
        for row in self._db.execute("SELECT * FROM portfolio_positions"):
            if row["strategy"] == "@physical":
                positions[row["instrument"]] = row["quantity"]
            else:
                virtual.setdefault(row["strategy"], {})[row["instrument"]] = row["quantity"]
        return {"mode": self.mode.value, "scope": self.scope.key, "account_version": self.account_version(), "balances": {r[0]: r[1] for r in self._db.execute("SELECT * FROM portfolio_balances")},
                "reserved": {r[0]: str(self._reserved(r[0])) for r in self._db.execute("SELECT currency FROM portfolio_balances")},
                "simulated_margin": str(self._margin()), "simulated_capital": str(self._capital()),
                "orders": [dict(r) for r in self._db.execute("SELECT * FROM portfolio_orders")],
                "physical_positions": positions, "virtual_positions": virtual,
                "strategy_cash": [dict(r) for r in self._db.execute("SELECT * FROM portfolio_strategy_cash")],
                "gaps": {r[0]: json.loads(r[1]) for r in self._db.execute("SELECT * FROM portfolio_gaps")}, "readiness": self.readiness()}

    def close(self):
        if not self._closed:
            self._check()
            self._db.close()
            self._closed = True
            self._lease.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
