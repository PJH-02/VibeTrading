"""Durable per-strategy capital, reservations and broker-confirmed fills."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.models import OrderIntent, Side


class EtfJournal:
    def __init__(self, path: Path, *, max_feedback_rows: int = 4096,
                 max_feedback_bytes: int = 8 * 1024 * 1024):
        if (isinstance(max_feedback_rows, bool) or not isinstance(max_feedback_rows, int)
                or max_feedback_rows <= 0 or isinstance(max_feedback_bytes, bool)
                or not isinstance(max_feedback_bytes, int) or max_feedback_bytes <= 0):
            raise ValueError('ETF feedback row/byte budgets must be explicit positive integers')
        self.max_feedback_rows, self.max_feedback_bytes = max_feedback_rows, max_feedback_bytes
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS books(strategy TEXT PRIMARY KEY,cash TEXT NOT NULL,day TEXT,entries INTEGER NOT NULL DEFAULT 0,direction INTEGER NOT NULL DEFAULT 0,safety_stop REAL);
            CREATE TABLE IF NOT EXISTS positions(strategy TEXT,symbol TEXT,quantity INTEGER NOT NULL,PRIMARY KEY(strategy,symbol));
            CREATE TABLE IF NOT EXISTS orders(intent_id TEXT PRIMARY KEY,strategy TEXT,symbol TEXT,side TEXT,quantity INTEGER,price TEXT,reserve TEXT,status TEXT,order_number TEXT,organization TEXT,day TEXT,filled INTEGER DEFAULT 0,notional TEXT DEFAULT '0',created_ns INTEGER,payload TEXT,cancel_requested INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,strategy TEXT,payload TEXT,created_ns INTEGER);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,created_ns INTEGER,sent_ns INTEGER);
            CREATE TABLE IF NOT EXISTS assumed_fills(id TEXT PRIMARY KEY,strategy TEXT,symbol TEXT,side TEXT,quantity INTEGER,price TEXT,fee TEXT,day TEXT,event_ns INTEGER,reason TEXT);
            CREATE TABLE IF NOT EXISTS account_reports(day TEXT PRIMARY KEY,payload TEXT NOT NULL,asof_ns INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS broker_trades(account_scope TEXT,day TEXT,order_number TEXT,symbol TEXT,side TEXT,quantity INTEGER,notional TEXT,PRIMARY KEY(account_scope,day,order_number));
            CREATE TABLE IF NOT EXISTS report_exclusions(account_scope TEXT,day TEXT,order_number TEXT,symbol TEXT,reason TEXT,created_ns INTEGER,PRIMARY KEY(account_scope,day,order_number));
            CREATE TABLE IF NOT EXISTS report_adjustments(day TEXT PRIMARY KEY,net_pnl TEXT NOT NULL,fees TEXT NOT NULL,evidence TEXT NOT NULL,created_ns INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS dispatch_attempts(intent_id TEXT PRIMARY KEY,evidence TEXT NOT NULL,state TEXT NOT NULL,prepared_ns INTEGER NOT NULL,handoff_ns INTEGER,updated_ns INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS order_feedback(row_id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,strategy TEXT NOT NULL,order_id TEXT NOT NULL,payload TEXT NOT NULL,payload_bytes INTEGER NOT NULL,claimed INTEGER NOT NULL DEFAULT 0,created_ns INTEGER NOT NULL);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(orders)')}
        for name,definition in [('order_type',"TEXT NOT NULL DEFAULT 'limit'"),('accepted_ns','INTEGER'),('fallback_requested','INTEGER NOT NULL DEFAULT 0'),('fallback_done','INTEGER NOT NULL DEFAULT 0'),
                ('account_scope',"TEXT NOT NULL DEFAULT 'mock'"),('permission_failure','INTEGER NOT NULL DEFAULT 0'),('rejection_reason',"TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE orders ADD COLUMN {name} {definition}')
        if 'cancel_confirmed' not in columns:
            self.db.execute('ALTER TABLE orders ADD COLUMN cancel_confirmed INTEGER NOT NULL DEFAULT 0')
        from .etf_recovery_ledger import ensure_schema
        ensure_schema(self.db)

    def append_order_feedback(self, event, *, observation_ns: int) -> int:
        """Durably append one bounded factual callback receipt before source code runs."""
        from execution.trading_contracts import StrategyOrderEvent
        if not isinstance(event, StrategyOrderEvent):
            raise TypeError('ETF feedback append requires a validated StrategyOrderEvent')
        event_id = getattr(event, 'event_id', None)
        strategy = getattr(event, 'strategy_id', None)
        order_id = getattr(event, 'order_id', None)
        if not all(isinstance(value, str) and value and len(value.encode()) <= 512
                   for value in (event_id, strategy, order_id)):
            raise ValueError('ETF order feedback requires bounded event/strategy/order identities')
        if isinstance(observation_ns, bool) or not isinstance(observation_ns, int) or observation_ns <= 0:
            raise ValueError('ETF feedback observation clock must be a positive nanosecond timestamp')
        payload = self._order_feedback_payload(event)
        size = len(payload.encode('utf-8'))
        with self.transaction():
            return self._append_order_feedback_in_transaction(event_id, strategy, order_id,
                                                               payload, size, observation_ns)

    @staticmethod
    def _order_feedback_payload(event) -> str:
        from collections.abc import Mapping
        def plain(value):
            if isinstance(value, Mapping):
                return {str(key): plain(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [plain(item) for item in value]
            if value.__class__.__name__ == 'AccountScope':
                return {'venue': value.venue, 'account': value.account,
                        'product': value.product, 'environment': value.environment}
            if isinstance(value, Decimal):
                return str(value)
            return value
        return json.dumps({name: plain(getattr(event, name)) for name in (
            'event_id','strategy_id','account_scope','order_id','status','event_ts_ns',
            'quantity','filled_quantity','remaining_quantity','fill_id','fill_quantity',
            'fill_price','fee','fee_currency','metadata')}, sort_keys=True, separators=(',', ':'),
            default=str, allow_nan=False)

    def _append_order_feedback_in_transaction(self, event_id, strategy, order_id,
                                               payload, size, observation_ns) -> int:
        old = self.db.execute('SELECT row_id,payload FROM order_feedback WHERE event_id=?', (event_id,)).fetchone()
        if old:
            if old['payload'] != payload:
                raise ValueError('ETF order feedback event identity collision')
            return int(old['row_id'])
        rows, used = self.db.execute('SELECT COUNT(*),COALESCE(SUM(payload_bytes),0) FROM order_feedback').fetchone()
        if rows >= self.max_feedback_rows or used + size > self.max_feedback_bytes:
            raise RuntimeError('ETF durable order feedback capacity exhausted')
        cur = self.db.execute('INSERT INTO order_feedback(event_id,strategy,order_id,payload,payload_bytes,created_ns) VALUES(?,?,?,?,?,?)',
            (event_id, strategy, order_id, payload, size, observation_ns))
        return int(cur.lastrowid)

    def enable_order_feedback(self, strategy: str):
        if strategy not in {'C3', 'KP', 'KD'}:
            raise ValueError('ETF order feedback may be enabled only for an original KR source')
        self.db.execute('INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)',
                        ('order_feedback:' + strategy, 'enabled'))

    def order_feedback_enabled(self, strategy: str) -> bool:
        return bool(self.db.execute('SELECT 1 FROM metadata WHERE key=? AND value=?',
                                    ('order_feedback:' + strategy, 'enabled')).fetchone())

    def mark_order_feedback_fault(self, strategy: str, event_id: str, reason: str):
        if strategy not in {'C3','KP','KD'}:
            raise ValueError('ETF feedback fault requires an original KR strategy')
        value = json.dumps({'event_id': str(event_id)[:512], 'reason': str(reason)[:800],
                            'created_ns': time.time_ns()}, sort_keys=True, separators=(',', ':'))
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)',
                            ('order_feedback_fault:' + strategy, value))

    def order_feedback_fault(self, strategy: str) -> dict | None:
        row = self.db.execute('SELECT value FROM metadata WHERE key=?',
                              ('order_feedback_fault:' + strategy,)).fetchone()
        return json.loads(row[0]) if row else None

    def bind_order_feedback_scope(self, route: str, identity: str):
        if route not in {'mock','mock2'} or not isinstance(identity, str) or not identity or len(identity) > 200:
            raise ValueError('ETF feedback scope requires an explicit owned mock identity')
        key = 'order_feedback_scope:' + route
        row = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        if row and row[0] != identity:
            raise RuntimeError('ETF feedback scope changed physical mock account identity')
        self.db.execute('INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)', (key, identity))

    def _record_order_feedback_tx(self, row, *, status: str, raw_state=None,
                                  prior_filled: int | None = None,
                                  prior_notional: Decimal | None = None,
                                  observation_ns: int | None = None):
        """Append the factual receipt in the same transaction as the owner fact."""
        if not self.order_feedback_enabled(row['strategy']):
            return None
        from execution.execution_ledger import AccountScope
        from execution.trading_contracts import StrategyOrderEvent
        route = row['account_scope']
        key = 'order_feedback_scope:' + route
        account = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        if route not in {'mock','mock2'} or account is None:
            raise RuntimeError('ETF feedback requires an initialized owned KIS mock scope')
        quantity, filled = int(row['quantity']), int(row['filled'])
        remaining = max(0, quantity - filled)
        cumulative = Decimal(str(row['notional']))
        delta = max(0, filled - int(prior_filled if prior_filled is not None else filled))
        delta_notional = cumulative - Decimal(str(prior_notional if prior_notional is not None else cumulative))
        fill_price = delta_notional / Decimal(delta) if delta and delta_notional > 0 else None
        raw = raw_state or {}
        stable_raw = {key: str(raw[key]) for key in ('cncl_yn','tot_ccld_qty','tot_ccld_amt','rmn_qty')
                      if key in raw}
        if observation_ns is not None:
            stable_raw['owner_observation_ns'] = str(observation_ns)
        event_key = '|'.join((row['intent_id'], status, str(filled), str(cumulative),
                              str(row['order_number']), json.dumps(stable_raw, sort_keys=True)))
        event_id = 'kis-feedback:' + hashlib.sha256(event_key.encode()).hexdigest()
        old = self.db.execute('SELECT row_id FROM order_feedback WHERE event_id=?', (event_id,)).fetchone()
        if old:
            return int(old[0])
        event = StrategyOrderEvent(event_id=event_id, strategy_id=row['strategy'],
            account_scope=AccountScope('kis', account[0], 'cash', route),
            order_id=row['intent_id'], status=status, event_ts_ns=None,
            quantity=Decimal(quantity), filled_quantity=Decimal(filled),
            remaining_quantity=Decimal(remaining),
            fill_id=(hashlib.sha256((row['intent_id'] + ':cum:' + str(filled)).encode()).hexdigest()
                     if delta else None),
            fill_quantity=Decimal(delta) if delta else None, fill_price=fill_price,
            fee=None, fee_currency=None, metadata={'raw_state': stable_raw,
                'source_timestamp_ns': None,
                'owner_observation_ns': observation_ns,
                'fee_basis': 'existing conservative journal model; not broker fee',
                'cumulative_notional': str(cumulative)})
        payload = self._order_feedback_payload(event)
        try:
            return self._append_order_feedback_in_transaction(event_id, row['strategy'], row['intent_id'],
                payload, len(payload.encode('utf-8')), time.time_ns())
        except RuntimeError as exc:
            if str(exc) != 'ETF durable order feedback capacity exhausted':
                raise
            # The surrounding factual order transaction still commits. Its
            # caller sees sentinel zero and must stop before any source action.
            return 0

    def record_order_observation(self, identity: str, *, observation_ns: int):
        """Append one current durable owner observation; source owns timing policy."""
        if isinstance(observation_ns, bool) or not isinstance(observation_ns, int) or observation_ns <= 0:
            raise ValueError('ETF owner observation time must be a positive nanosecond timestamp')
        with self.transaction():
            row = self.db.execute('SELECT * FROM orders WHERE intent_id=?', (identity,)).fetchone()
            if row is None or not self.order_feedback_enabled(row['strategy']):
                return None
            row = dict(row)
            quantity, filled = int(row['quantity']), int(row['filled'])
            if row['status'] == 'unknown':
                status = 'UNKNOWN'
            elif row['cancel_confirmed'] and filled < quantity:
                status = 'CANCELLED'
            elif filled >= quantity:
                status = 'FILLED'
            elif filled:
                status = 'PARTIAL_FILL'
            else:
                status = 'ACCEPTED'
            return self._record_order_feedback_tx(row, status=status,
                raw_state={}, observation_ns=observation_ns)

    def order_feedback_event(self, row_id: int):
        from execution.execution_ledger import AccountScope
        from execution.trading_contracts import StrategyOrderEvent
        row = self.db.execute('SELECT payload FROM order_feedback WHERE row_id=?', (row_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        scope = AccountScope(**data['account_scope'])
        for key in ('quantity','filled_quantity','remaining_quantity','fill_quantity','fill_price','fee'):
            if data.get(key) is not None:
                data[key] = Decimal(str(data[key]))
        return StrategyOrderEvent(**(data | {'account_scope': scope}))

    def latest_order_feedback(self, order_id: str):
        row = self.db.execute('SELECT row_id FROM order_feedback WHERE order_id=? ORDER BY row_id DESC LIMIT 1',
                              (order_id,)).fetchone()
        return int(row[0]) if row else None

    def claim_order_feedback(self, row_id: int, strategy: str) -> dict | None:
        """Claim once; commit precedes callback and is never rolled back on callback failure."""
        with self.transaction():
            row = self.db.execute('SELECT * FROM order_feedback WHERE row_id=? AND strategy=?',
                                  (row_id, strategy)).fetchone()
            if row is None or row['claimed']:
                return None
            self.db.execute('UPDATE order_feedback SET claimed=1 WHERE row_id=? AND claimed=0', (row_id,))
            return json.loads(row['payload'])

    def order_feedback(self, strategy: str, after: int = 0, limit: int = 64) -> list[tuple[int, dict]]:
        if not isinstance(strategy, str) or not strategy or isinstance(after, bool) or after < 0:
            raise ValueError('ETF feedback read cursor/strategy is invalid')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
            raise ValueError('ETF feedback read limit must be from 1 to 256')
        rows = self.db.execute('SELECT row_id,payload FROM order_feedback WHERE strategy=? AND row_id>? ORDER BY row_id LIMIT ?',
                               (strategy, after, limit))
        return [(int(row['row_id']), json.loads(row['payload'])) for row in rows]

    def initialize(self, total_cash: Decimal, account_identity: str):
        if not total_cash.is_finite() or total_cash < 3:
            raise ValueError('ETF account capital must fund three positive allocations')
        row = self.db.execute("SELECT value FROM metadata WHERE key='account_identity'").fetchone()
        if row:
            if row[0] != account_identity:
                raise RuntimeError('ETF journal belongs to another KIS account')
            if self.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0:
                # Read-only preflight may have initialized books before final
                # credentials/cash allocation were confirmed. No executed or
                # submitted history exists, so refresh the initial thirds.
                with self.transaction():
                    self.db.execute('UPDATE books SET cash=?',(str(total_cash // 3),))
                    self.db.execute("UPDATE metadata SET value=? WHERE key='initial_cash'",(str(total_cash),))
            return
        allocation = total_cash // 3
        with self.transaction():
            self.db.executemany('INSERT INTO books(strategy,cash) VALUES(?,?)', [(s,str(allocation)) for s in ('C3','KP','KD')])
            self.db.execute('INSERT INTO metadata VALUES(?,?)', ('account_identity',account_identity))
            self.db.execute('INSERT INTO metadata VALUES(?,?)', ('initial_cash',str(total_cash)))

    def transaction(self):
        from contextlib import contextmanager
        @contextmanager
        def transact():
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    try: self.db.execute('ROLLBACK')
                    except Exception: pass
                raise
        return transact()

    def book(self, strategy: str) -> dict:
        row = dict(self.db.execute('SELECT * FROM books WHERE strategy=?',(strategy,)).fetchone())
        row['positions'] = {r['symbol']:r['quantity'] for r in self.db.execute('SELECT * FROM positions WHERE strategy=? AND quantity>0',(strategy,))}
        row['cash'] = Decimal(row['cash'])
        row['reserved'] = sum((Decimal(r[0]) for r in self.db.execute("SELECT reserve FROM orders WHERE strategy=? AND status IN ('submitting','unknown','pending')",(strategy,))), Decimal(0))
        return row

    def roll_day(self, day: str):
        self.db.execute('UPDATE books SET day=?,entries=0 WHERE day IS NULL OR day<>?',(day,day))

    def assumed_positions(self,strategy=None,before=None):
        rows=self.db.execute('SELECT * FROM assumed_fills ORDER BY event_ns')
        positions={}
        for row in rows:
            if strategy and row['strategy']!=strategy: continue
            if before and row['day']>=before: continue
            key=row['symbol']; positions[key]=positions.get(key,0)+(1 if row['side']=='buy' else -1)*row['quantity']
        return {s:q for s,q in positions.items() if q}

    def logical_book(self,strategy):
        book=self.book(strategy)
        from .etf_recovery_ledger import undo_excluded_book
        book=undo_excluded_book(self.db,strategy,book)
        for symbol,quantity in self.assumed_positions(strategy).items():
            book['positions'][symbol]=book['positions'].get(symbol,0)+quantity
        book['positions']={s:q for s,q in book['positions'].items() if q}
        for row in self.db.execute('SELECT * FROM assumed_fills WHERE strategy=?',(strategy,)):
            book['cash']-= (1 if row['side']=='buy' else -1)*Decimal(row['price'])*row['quantity']+Decimal(row['fee'])
        return book

    def assume_fill(self,identity,strategy,symbol,side,quantity,price,day,event_ns,reason,fee_rate=Decimal('.000148'),*,notify=True):
        if self.db.execute('SELECT 1 FROM assumed_fills WHERE id=?',(identity,)).fetchone(): return False
        price=Decimal(str(price)); quantity=int(quantity)
        if not price.is_finite() or price<=0 or quantity<=0: raise ValueError('Invalid assumed execution')
        if side=='sell' and quantity>self.logical_book(strategy)['positions'].get(symbol,0):
            raise RuntimeError('Cannot sell unowned assumed shares')
        fee=price*quantity*Decimal(str(fee_rate))
        self.db.execute('INSERT INTO assumed_fills VALUES(?,?,?,?,?,?,?,?,?,?)',
            (identity,strategy,symbol,side,quantity,str(price),str(fee),day,event_ns,reason))
        if notify:
            self.notify(identity+':assumed','mock_assumed',{'strategy':strategy,'symbol':symbol,'side':side,
                'quantity':quantity,'price':str(price),'fee':str(fee),'reason':reason,'event_ns':event_ns})
        return True

    def recover_assumptions(self,fee_rate=Decimal('.000148')):
        for row in self.db.execute("SELECT * FROM orders WHERE status='rejected' AND permission_failure=1").fetchall():
            intent=OrderIntent.model_validate_json(row['payload']); quote=intent.metadata.get('intent_quote') or {}
            if intent.metadata.get('recovery_cleanup'):
                continue
            price=quote.get('ask' if row['side']=='buy' else 'bid')
            if price is None or abs(int(quote.get('intent_ns',0))-int(quote.get('received_ns',0)))>10_000_000_000: continue
            day=row['day']; day=day if '-' in day else day[:4]+'-'+day[4:6]+'-'+day[6:]
            with self.transaction():
                self.assume_fill(row['intent_id'],row['strategy'],row['symbol'],row['side'],row['quantity'],price,
                    day,int(quote['intent_ns']),row['rejection_reason'],fee_rate)

    def assumed_report(self,day,marks=None,opening_marks=None):
        marks,opening_marks=marks or {},opening_marks or {}
        cash=Decimal(0); fees=Decimal(0); count=0
        for row in self.db.execute('SELECT * FROM assumed_fills WHERE day=?',(day,)):
            fees+=Decimal(row['fee']); count+=1
            cash-=(1 if row['side']=='buy' else -1)*Decimal(row['price'])*row['quantity']+Decimal(row['fee'])
        for symbol,quantity in self.assumed_positions(before=day).items():
            cash-=quantity*Decimal(str(opening_marks[symbol]))
        for symbol,quantity in self.assumed_positions().items():
            cash+=quantity*Decimal(str(marks[symbol]))
        return {'net_pnl':str(cash),'fees':str(fees),'fill_count':count,'positions':self.assumed_positions()}

    def decision(self, strategy: str, identity: str, payload: dict) -> bool:
        return bool(self.db.execute('INSERT OR IGNORE INTO decisions VALUES(?,?,?,?)',
            (identity,strategy,json.dumps(payload,default=str),time.time_ns())).rowcount)

    def pending(self, strategy: str | None = None) -> list[dict]:
        sql = "SELECT * FROM orders WHERE status IN ('submitting','unknown','pending')"
        return [dict(r) for r in self.db.execute(sql + (' AND strategy=?' if strategy else ''), (strategy,) if strategy else ())]

    @staticmethod
    def _evidence(intent, payload, evidence, required):
        if evidence is None:
            if required: raise ValueError('Guarded ETF stage requires dispatch evidence')
            return None
        meta = intent.metadata
        expected = {'contract':'etf-dispatch-v1','intent_sha256':hashlib.sha256(payload.encode()).hexdigest(),
            'account_scope':meta.get('account_scope'),'strategy':intent.job_id,'signal_id':intent.signal_id,
            'valid_until_ts_ns':meta.get('valid_until_ts_ns'),'quote':meta.get('intent_quote'),
            'source_provider':meta.get('source_provider')}
        if type(evidence) is not dict or evidence != expected:
            raise ValueError('ETF dispatch evidence differs from immutable intent')
        encoded = json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False)
        if encoded != json.dumps(expected,sort_keys=True,separators=(',',':'),allow_nan=False):
            raise ValueError('ETF dispatch evidence types differ from immutable intent')
        if len(encoded.encode()) > 16384:
            raise ValueError('ETF dispatch evidence exceeds the 16KiB protocol envelope')
        if (expected['account_scope'] not in {'mock','mock2'} or type(expected['valid_until_ts_ns']) is not int
                or expected['valid_until_ts_ns'] <= 0 or any(not isinstance(expected[k],str)
                or not expected[k] or len(expected[k])>200 for k in ('strategy','signal_id','source_provider'))):
            raise ValueError('ETF dispatch evidence identities are invalid')
        quote = expected['quote']
        if type(quote) is not dict or set(quote) != {'bid','ask','received_ns','intent_ns'}:
            raise ValueError('ETF dispatch evidence requires the exact quote')
        if (any(type(quote[k]) is not int or quote[k] <= 0 for k in ('received_ns','intent_ns'))
                or any(not isinstance(quote[k],str) for k in ('bid','ask'))):
            raise ValueError('ETF dispatch quote types are invalid')
        try: bid, ask = Decimal(quote['bid']),Decimal(quote['ask'])
        except InvalidOperation as exc: raise ValueError('ETF dispatch quote prices are invalid') from exc
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid:
            raise ValueError('ETF dispatch quote prices are invalid')
        return encoded

    def stage(self, intent: OrderIntent, day: str, direction: int, safety_stop: float | None, *, replacement_for: str | None = None, dispatch_evidence: dict | None = None, require_evidence: bool = False) -> bool:
        """Commit before any network submission; unknown results keep reservations."""
        if type(require_evidence) is not bool: raise ValueError('Explicit guarded stage mode required')
        payload = intent.model_dump_json()
        evidence = self._evidence(intent,payload,dispatch_evidence,require_evidence)
        if require_evidence and (intent.metadata.get('direction') != direction
                or intent.metadata.get('safety_stop') != safety_stop
                or intent.metadata.get('replacement_for') != replacement_for):
            raise ValueError('ETF stage context differs from immutable intent')
        unit = intent.limit_price or Decimal(str(intent.metadata.get('reserve_unit_price',0)))
        if intent.side == Side.BUY and unit <= 0:
            raise ValueError('Market buy requires a verified reservation price')
        reserve = intent.quantity * unit * Decimal('1.005') if intent.side == Side.BUY else Decimal(0)
        with self.transaction():
            old = self.db.execute('SELECT payload,day FROM orders WHERE intent_id=?',(intent.intent_id,)).fetchone()
            if old:
                old_evidence = self.db.execute('SELECT evidence FROM dispatch_attempts WHERE intent_id=?',(intent.intent_id,)).fetchone()
                if old['payload'] != payload or old['day'] != day or (old_evidence[0] if old_evidence else None) != evidence:
                    raise ValueError('ETF immutable intent/evidence collision')
                return False
            book = self.book(intent.job_id)
            if intent.side == Side.BUY and reserve > book['cash']-book['reserved']:
                raise RuntimeError('Order exceeds its independent strategy capital')
            if intent.side == Side.SELL:
                if int(intent.quantity) > book['positions'].get(intent.instrument.raw_symbol,0):
                    raise RuntimeError('Sell exceeds the strategy-owned filled position')
            self.db.execute('INSERT INTO orders(intent_id,strategy,symbol,side,quantity,price,reserve,status,day,created_ns,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (intent.intent_id,intent.job_id,intent.instrument.raw_symbol,intent.side.value,int(intent.quantity),str(unit),str(reserve),'submitting',day,time.time_ns(),payload))
            self.db.execute('UPDATE orders SET order_type=? WHERE intent_id=?',('limit' if intent.limit_price is not None else 'market',intent.intent_id))
            self.db.execute('UPDATE orders SET account_scope=? WHERE intent_id=?',(intent.metadata.get('account_scope','mock'),intent.intent_id))
            if evidence is not None:
                now = time.time_ns()
                self.db.execute("INSERT INTO dispatch_attempts(intent_id,evidence,state,prepared_ns,updated_ns) VALUES(?,?,'PREPARED',?,?)",
                    (intent.intent_id,evidence,now,now))
            from .etf_recovery_ledger import active, exclude
            iso_day=day if '-' in day else day[:4]+'-'+day[4:6]+'-'+day[6:]
            if active(self.db,intent.job_id,iso_day):
                exclude(self,intent.intent_id,'Recovery ledger owns strategy accounting',
                        mirror=not intent.metadata.get('recovery_cleanup',False))
            if replacement_for:
                self.db.execute('UPDATE orders SET fallback_done=1 WHERE intent_id=?',(replacement_for,))
            if intent.side == Side.BUY:
                # One logical entry may contain both the 1x and 2x vehicles.
                self.db.execute('UPDATE books SET entries=1,direction=?,safety_stop=? WHERE strategy=?', (direction,safety_stop,intent.job_id))
        return True

    def dispatch_state(self, identity: str):
        row = self.db.execute('SELECT state FROM dispatch_attempts WHERE intent_id=?',(identity,)).fetchone()
        return row[0] if row else None

    def dispatch_handoff(self, identity: str):
        """Persist the single attempt immediately before the adapter POST call."""
        with self.transaction():
            now = time.time_ns()
            changed = self.db.execute("UPDATE dispatch_attempts SET state='HANDOFF',handoff_ns=?,updated_ns=? WHERE intent_id=? AND state='PREPARED'",
                (now,now,identity)).rowcount
            if changed != 1:
                raise RuntimeError('ETF dispatch attempt is not PREPARED')

    def dispatch_not_sent(self, identity: str, reason: str, *, before_post: bool = False):
        with self.transaction():
            states = ('PREPARED','HANDOFF') if before_post else ('PREPARED','PREPARED')
            changed = self.db.execute("UPDATE dispatch_attempts SET state='NOT_SENT',reason=?,updated_ns=? WHERE intent_id=? AND state IN (?,?)",
                (reason[:800],time.time_ns(),identity,*states)).rowcount
            if changed:
                self.db.execute("UPDATE orders SET status='blocked',reserve='0',rejection_reason=? WHERE intent_id=?",(reason[:800],identity))
                row = dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
                self._record_order_feedback_tx(row, status='REJECTED',
                    raw_state={'provenance':'LOCAL_NOT_SENT','reason':reason[:800]})

    def recover_dispatch_attempts(self):
        """Restart never resubmits: pre-handoff is unsent, handoff is uncertain."""
        with self.transaction():
            prepared = [r[0] for r in self.db.execute("SELECT intent_id FROM dispatch_attempts WHERE state='PREPARED'")]
            for identity in prepared:
                self.db.execute("UPDATE orders SET status='blocked',reserve='0',rejection_reason='Restart before dispatch handoff' WHERE intent_id=?",(identity,))
            self.db.execute("UPDATE dispatch_attempts SET state='NOT_SENT',reason='Restart before dispatch handoff',updated_ns=? WHERE state='PREPARED'",(time.time_ns(),))
            self.db.execute("UPDATE orders SET status='unknown' WHERE intent_id IN (SELECT intent_id FROM dispatch_attempts WHERE state='HANDOFF')")
            self.db.execute("UPDATE dispatch_attempts SET state='UNKNOWN',reason='Restart after dispatch handoff',updated_ns=? WHERE state='HANDOFF'",(time.time_ns(),))

    def accepted(self, identity: str, output: dict):
        with self.transaction():
            prior = self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone()
            if prior is None: raise ValueError('ACK requires an existing ETF intent')
            number = output.get('ODNO',output.get('odno'))
            organization = output.get('KRX_FWDG_ORD_ORGNO',output.get('krx_fwdg_ord_orgno',''))
            if (not isinstance(number,str) or not number or len(number)>200
                    or not isinstance(organization,str) or len(organization)>200
                    or any(c.isspace() or ord(c)<32 or ord(c)==127 for c in number+organization)):
                raise ValueError('ETF ACK requires bounded native order identity')
            if ((prior['order_number'] and prior['order_number'] != number)
                    or (prior['organization'] and organization and prior['organization'] != organization)):
                raise ValueError('ETF ACK conflicts with bound native order identity')
            self.db.execute("UPDATE dispatch_attempts SET state=CASE WHEN state IN ('REJECTED','NOT_SENT') THEN state ELSE 'ACKED' END,updated_ns=? WHERE intent_id=?",(time.time_ns(),identity))
            self.db.execute("UPDATE orders SET status=CASE WHEN status IN ('complete','rejected','blocked') THEN status ELSE 'pending' END,order_number=?,organization=?,accepted_ns=COALESCE(accepted_ns,?) WHERE intent_id=?",
                (number,organization or prior['organization'] or '',time.time_ns(),identity))
            row=dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
            from .etf_recovery_ledger import exclude
            policy=self.db.execute('SELECT reason,mirror FROM strategy_order_exclusions WHERE intent_id=?',(identity,)).fetchone()
            if policy:
                exclude(self,identity,policy['reason'],mirror=bool(policy['mirror']))
            self.notify(identity+':accepted','mock_order',{'strategy':row['strategy'],'symbol':row['symbol'],
                'side':row['side'],'quantity':row['quantity'],'order_number':row['order_number'],
                'account_scope':row['account_scope'],'order_type':row['order_type'],'price':None if row['order_type']=='market' else row['price']})
            return self._record_order_feedback_tx(row, status='ACCEPTED')

    def notify(self, identity: str, kind: str, payload: dict):
        self.db.execute('INSERT OR IGNORE INTO notifications VALUES(?,?,?,?,NULL)',
            (identity,kind,json.dumps(payload,default=str),time.time_ns()))

    def fallback_candidates(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM orders WHERE fallback_requested=1 AND fallback_done=0 AND status='complete'")]

    def reject(self, identity: str, reason: str = '', *, permission_failure=False):
        with self.transaction():
            self.db.execute("UPDATE dispatch_attempts SET state='REJECTED',reason=?,updated_ns=? WHERE intent_id=?",(reason[:800],time.time_ns(),identity))
            self.db.execute("UPDATE orders SET status='rejected',reserve='0' WHERE intent_id=?",(identity,))
            self.db.execute('UPDATE orders SET rejection_reason=?,permission_failure=? WHERE intent_id=?',(reason,int(permission_failure),identity))
            row=dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
            receipt = self._record_order_feedback_tx(row, status='REJECTED',
                raw_state={'provenance':'KIS_REJECTED','reason':reason[:800]})
            if reason:
                row=dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
                self.notify(identity+':rejected','mock_rejected',{'strategy':row['strategy'],'symbol':row['symbol'],
                    'side':row['side'],'quantity':row['quantity'],'reason':reason[:800]})
            return receipt

    def ambiguous(self, identity: str):
        with self.transaction():
            self.db.execute("UPDATE orders SET status='unknown' WHERE intent_id=?",(identity,))
            self.db.execute("UPDATE dispatch_attempts SET state='UNKNOWN',updated_ns=? WHERE intent_id=?",(time.time_ns(),identity))
            row=dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
            return self._record_order_feedback_tx(row, status='UNKNOWN',
                raw_state={'provenance':'KIS_OUTCOME_UNKNOWN'})

    def apply_status(self, identity: str, broker: dict):
        """Apply cumulative fill deltas once, including partial/cancelled fills."""
        with self.transaction():
            row = dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
            filled = int(Decimal(str(broker.get('tot_ccld_qty',0))))
            notional = Decimal(str(broker.get('tot_ccld_amt') or 0))
            if filled and notional == 0:
                notional = filled * Decimal(str(broker.get('avg_prvs') or broker.get('avg_ccld_unpr') or 0))
            if filled < row['filled'] or filled > row['quantity'] or notional < Decimal(row['notional']) or (filled and notional <= 0):
                raise RuntimeError('Broker cumulative fill is inconsistent')
            delta, cost = filled-row['filled'], notional-Decimal(row['notional'])
            book = self.book(row['strategy'])
            sign = 1 if row['side'] == 'buy' else -1
            owned = book['positions'].get(row['symbol'],0)+sign*delta
            if owned < 0:
                raise RuntimeError('Broker fill exceeds strategy-owned quantity')
            # Conservative fee accrual (0.015% per side) is released only by a
            # later explicit account reconciliation; never spend assumed fees.
            cash = book['cash'] - sign*cost - cost*Decimal('0.00015')
            self.db.execute('UPDATE books SET cash=? WHERE strategy=?',(str(cash),row['strategy']))
            self.db.execute('INSERT INTO positions VALUES(?,?,?) ON CONFLICT(strategy,symbol) DO UPDATE SET quantity=excluded.quantity', (row['strategy'],row['symbol'],owned))
            remaining = int(Decimal(str(broker.get('rmn_qty',row['quantity']-filled))))
            cancel_confirmed = str(broker.get('cncl_yn','')).upper() == 'Y' or bool(row.get('cancel_confirmed', 0))
            terminal = row['status'] == 'complete' or filled == row['quantity'] or cancel_confirmed or remaining == 0
            reserve = Decimal(0) if terminal or row['side']=='sell' else (row['quantity']-filled)*Decimal(row['price'])*Decimal('1.005')
            self.db.execute('UPDATE orders SET filled=?,notional=?,reserve=?,status=?,cancel_confirmed=CASE WHEN ? THEN 1 ELSE cancel_confirmed END WHERE intent_id=?',
                (filled,str(notional),str(reserve),'complete' if terminal else 'pending',int(cancel_confirmed),identity))
            updated = dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
            from .etf_recovery_ledger import mirror_status_fill
            mirror_status_fill(self,row,filled,delta,cost)
            if delta:
                self.notify(identity+':filled:'+str(filled),'mock_fill',{'strategy':row['strategy'],
                    'symbol':row['symbol'],'side':row['side'],'filled_quantity':delta,'total_filled':filled,
                    'ordered_quantity':row['quantity'],'fill_price':str(cost/delta),
                    'order_number':row['order_number'],'account_scope':row['account_scope'],'complete':filled==row['quantity']})
            if not self.logical_book(row['strategy'])['positions'] and not self.pending(row['strategy']):
                self.db.execute('UPDATE books SET direction=0,safety_stop=NULL WHERE strategy=?',(row['strategy'],))
            if cancel_confirmed and filled < row['quantity']:
                feedback_status = 'CANCELLED'
            elif filled >= row['quantity']:
                feedback_status = 'FILLED'
            elif terminal:
                feedback_status = 'UNKNOWN'
            elif filled:
                feedback_status = 'PARTIAL_FILL'
            else:
                feedback_status = 'ACCEPTED'
            return self._record_order_feedback_tx(updated, status=feedback_status,
                raw_state=broker, prior_filled=row['filled'],
                prior_notional=Decimal(row['notional']))

    def summary(self) -> dict:
        return {s:self.book(s) for s in ('C3','KP','KD')}
