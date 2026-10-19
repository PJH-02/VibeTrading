"""Durable per-strategy capital, reservations and broker-confirmed fills."""
from __future__ import annotations

import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from .models import OrderIntent, Side


class EtfJournal:
    def __init__(self, path: Path):
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
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(orders)')}
        for name,definition in [('order_type',"TEXT NOT NULL DEFAULT 'limit'"),('accepted_ns','INTEGER'),('fallback_requested','INTEGER NOT NULL DEFAULT 0'),('fallback_done','INTEGER NOT NULL DEFAULT 0'),
                ('account_scope',"TEXT NOT NULL DEFAULT 'mock'"),('permission_failure','INTEGER NOT NULL DEFAULT 0'),('rejection_reason',"TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE orders ADD COLUMN {name} {definition}')
        from .etf_recovery_ledger import ensure_schema
        ensure_schema(self.db)

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
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            else:
                self.db.execute('COMMIT')
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

    def stage(self, intent: OrderIntent, day: str, direction: int, safety_stop: float | None, *, replacement_for: str | None = None, dispatch_evidence: dict | None = None) -> bool:
        """Commit before any network submission; unknown results keep reservations."""
        unit = intent.limit_price or Decimal(str(intent.metadata.get('reserve_unit_price',0)))
        if intent.side == Side.BUY and unit <= 0:
            raise ValueError('Market buy requires a verified reservation price')
        reserve = intent.quantity * unit * Decimal('1.005') if intent.side == Side.BUY else Decimal(0)
        with self.transaction():
            if self.db.execute('SELECT 1 FROM orders WHERE intent_id=?',(intent.intent_id,)).fetchone():
                return False
            book = self.book(intent.job_id)
            if intent.side == Side.BUY and reserve > book['cash']-book['reserved']:
                raise RuntimeError('Order exceeds its independent strategy capital')
            if intent.side == Side.SELL:
                if int(intent.quantity) > book['positions'].get(intent.instrument.raw_symbol,0):
                    raise RuntimeError('Sell exceeds the strategy-owned filled position')
            self.db.execute('INSERT INTO orders(intent_id,strategy,symbol,side,quantity,price,reserve,status,day,created_ns,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (intent.intent_id,intent.job_id,intent.instrument.raw_symbol,intent.side.value,int(intent.quantity),str(unit),str(reserve),'submitting',day,time.time_ns(),intent.model_dump_json()))
            self.db.execute('UPDATE orders SET order_type=? WHERE intent_id=?',('limit' if intent.limit_price is not None else 'market',intent.intent_id))
            self.db.execute('UPDATE orders SET account_scope=? WHERE intent_id=?',(intent.metadata.get('account_scope','mock'),intent.intent_id))
            if dispatch_evidence is not None:
                now = time.time_ns()
                self.db.execute("INSERT INTO dispatch_attempts(intent_id,evidence,state,prepared_ns,updated_ns) VALUES(?,?,'PREPARED',?,?)",
                    (intent.intent_id,json.dumps(dispatch_evidence,sort_keys=True,separators=(',',':'),default=str),now,now))
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
            self.db.execute("UPDATE dispatch_attempts SET state='ACKED',updated_ns=? WHERE intent_id=?",(time.time_ns(),identity))
            self.db.execute("UPDATE orders SET status='pending',order_number=?,organization=?,accepted_ns=? WHERE intent_id=?",
                (output.get('ODNO',output.get('odno')),output.get('KRX_FWDG_ORD_ORGNO',output.get('krx_fwdg_ord_orgno','')),time.time_ns(),identity))
            row=dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
            from .etf_recovery_ledger import exclude
            policy=self.db.execute('SELECT reason,mirror FROM strategy_order_exclusions WHERE intent_id=?',(identity,)).fetchone()
            if policy:
                exclude(self,identity,policy['reason'],mirror=bool(policy['mirror']))
            self.notify(identity+':accepted','mock_order',{'strategy':row['strategy'],'symbol':row['symbol'],
                'side':row['side'],'quantity':row['quantity'],'order_number':row['order_number'],
                'account_scope':row['account_scope'],'order_type':row['order_type'],'price':None if row['order_type']=='market' else row['price']})

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
            if reason:
                row=dict(self.db.execute('SELECT * FROM orders WHERE intent_id=?',(identity,)).fetchone())
                self.notify(identity+':rejected','mock_rejected',{'strategy':row['strategy'],'symbol':row['symbol'],
                    'side':row['side'],'quantity':row['quantity'],'reason':reason[:800]})

    def ambiguous(self, identity: str):
        with self.transaction():
            self.db.execute("UPDATE orders SET status='unknown' WHERE intent_id=?",(identity,))
            self.db.execute("UPDATE dispatch_attempts SET state='UNKNOWN',updated_ns=? WHERE intent_id=?",(time.time_ns(),identity))

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
            terminal = filled == row['quantity'] or str(broker.get('cncl_yn','')).upper() == 'Y' or remaining == 0
            reserve = Decimal(0) if terminal or row['side']=='sell' else (row['quantity']-filled)*Decimal(row['price'])*Decimal('1.005')
            self.db.execute('UPDATE orders SET filled=?,notional=?,reserve=?,status=? WHERE intent_id=?',
                (filled,str(notional),str(reserve),'complete' if terminal else 'pending',identity))
            from .etf_recovery_ledger import mirror_status_fill
            mirror_status_fill(self,row,filled,delta,cost)
            if delta:
                self.notify(identity+':filled:'+str(filled),'mock_fill',{'strategy':row['strategy'],
                    'symbol':row['symbol'],'side':row['side'],'filled_quantity':delta,'total_filled':filled,
                    'ordered_quantity':row['quantity'],'fill_price':str(cost/delta),
                    'order_number':row['order_number'],'account_scope':row['account_scope'],'complete':filled==row['quantity']})
            if not self.logical_book(row['strategy'])['positions'] and not self.pending(row['strategy']):
                self.db.execute('UPDATE books SET direction=0,safety_stop=NULL WHERE strategy=?',(row['strategy'],))

    def summary(self) -> dict:
        return {s:self.book(s) for s in ('C3','KP','KD')}
