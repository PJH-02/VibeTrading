"""Live Kiwoom ETF signals -> independent books -> KIS mock cash orders.

Entry rules are the frozen C3|vt30|ETF, KP and KD source selections. Confirmed
bars produce decisions; orders go to subsequent available prices. Broker ACK
is never treated as a fill. The long-running loop cannot select a real account.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import logging
import time
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from contextlib import closing
from pathlib import Path

from execution.process_lease import ProcessLease
from strategies.kr import _etf_plugin
from strategies.kr._intraday import intraday_decision as _intraday_decision

import numpy as np
import pandas as pd
import nats

from .bus import NatsJetStreamBus
from strategies.kr.etf_history import EtfHistory
from storage.etf_csv_history import ETF_HISTORY_ROLES
from portfolio.etf_journal import EtfJournal
from operations.health import touch
from execution.kis_execution import KisCashBroker, KisOrderRejected, account_permission_rejection
from core.models import AssetClass, Bar1mEvent, BookUpdateType, EventType, InstrumentId, OrderDestination, OrderIntent, Side, Signal, SignalAction, Venue, event_from_dict
from feeds.providers.kiwoom import KiwoomAuthClient, KST
from feeds.providers.kis_client import KisAuthClient
from .settings import get_settings

log = logging.getLogger(__name__)
SYMBOLS = {'069500','122630','114800','252670','229200','233740','251340'}


class EtfDispatchBlocked(RuntimeError):
    """Known pre-handoff refusal; no broker order was sent by this attempt."""


def intraday_decision(strategy: str, feature: dict, book: dict) -> tuple[int, float, str]:
    """Frozen M1-P compatibility dispatcher to lazily resolved KR helpers."""
    if strategy in {'KP', 'KD'}:
        return _etf_plugin.intraday_decision_for(strategy, feature, book)
    return _intraday_decision(strategy, feature, book)


class EtfTradingLoop:
    def __init__(self, *, settings=None, bus=None, event_source=None, gap_recovery_verifier=None, authority_root=None):
        self.settings = settings if settings is not None else get_settings()
        authority_root = authority_root if authority_root is not None else self.settings.env.get('EXECUTION_AUTHORITY_ROOT')
        if not authority_root:
            raise ValueError('ETF requires an explicit shared EXECUTION_AUTHORITY_ROOT')
        self._close_task, self._closing = None, False
        self.broker = KisCashBroker(self.settings, mode='mock')
        self.brokers = {'mock':self.broker,'mock2':KisCashBroker(self.settings,mode='mock2')}
        self._acquire_ownership(Path(authority_root))
        try:
            self.kiwoom = KiwoomAuthClient(self.settings)
            self.market_client = KisAuthClient(self.settings,mode='real')
            root = self.settings.state_dir / 'etf-mock'
            self.journal = EtfJournal(root / 'journal.sqlite3')
            self.history = EtfHistory(self.kiwoom, root / 'warmup')
            self.bus = bus if bus is not None else NatsJetStreamBus(self.settings)
            self._event_source = event_source
            self._gap_recovery_verifier = gap_recovery_verifier
            self._feed_invalid = set()
            self._feed_snapshots = set()
            self._feed_history_ready = False
            self._feed_gap_generation = 0
            self.today: dict[str, dict[int, dict]] = {s:{} for s in ETF_HISTORY_ROLES}
            self.quotes: dict[str, tuple[Decimal,Decimal,int]] = {}
            self.features: dict[str, dict] = {}
            self._lock = asyncio.Lock()
            self._day = ''
            self._warmup_task = None
            self.phase = 'starting'
            self._c3_attempt_ns = 0
            self._checkpoint_recovery_state, self._checkpoint_recovery_reason = 'IDLE', None
            self._checkpoint_initialized = False
        except BaseException:
            if hasattr(self,'journal'): self.journal.db.close()
            for lease in reversed(tuple(self._owner_leases.values())): lease.close()
            raise

    def _acquire_ownership(self, root):
        self._owner_leases, self._account_ids = {}, {}
        planned = {}
        for route, broker in self.brokers.items():
            if route not in {'mock','mock2'} or broker.mode != route:
                raise ValueError('ETF requires explicit mock routes')
            account = broker.account_params()['CANO']
            planned[route] = ProcessLease.account(root,'kis',account,'mock')
            self._account_ids[route] = account
        if set(planned) != {'mock','mock2'} or len({x.path for x in planned.values()}) != 2:
            raise ValueError('The two mock routes must refer to distinct physical accounts')
        try:
            for route in sorted(planned,key=lambda name:str(planned[name].path)):
                lease = planned[route]
                lease.acquire()
                self._owner_leases[route] = lease
            for broker in self.brokers.values(): broker.authority_check = self._check_ownership
        except BaseException:
            for lease in reversed(tuple(self._owner_leases.values())): lease.close()
            raise

    def _check_ownership(self):
        if getattr(self,'_closing',False) or set(getattr(self,'_owner_leases',{})) != {'mock','mock2'}:
            raise EtfDispatchBlocked('ETF account ownership is closed or missing')
        try:
            for route, lease in self._owner_leases.items():
                broker = self.brokers[route]
                if (broker.mode != route or broker.account_params()['CANO'] != self._account_ids[route]
                        or getattr(broker,'authority_check',None) != self._check_ownership):
                    raise RuntimeError('ETF physical account configuration changed')
                lease.check()
        except (RuntimeError,OSError) as exc:
            self._closing = True
            raise EtfDispatchBlocked('ETF account ownership verification failed') from exc

    async def close(self):
        if getattr(self,'_close_task',None) is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close(),name='etf-owner-close')
        await asyncio.shield(self._close_task)

    async def _close(self):
        runner = getattr(self,'_runner_task',None)
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner,return_exceptions=True)
        background = [task for name in ('_warmup_task','_report_task')
                      if (task := getattr(self,name,None)) is not None]
        for task in background: task.cancel()
        await asyncio.gather(*background,return_exceptions=True)
        await self.bus.close()
        self.journal.db.close()
        for lease in reversed(tuple(self._owner_leases.values())): lease.close()

    def broker_for(self,symbol,scope=None):
        scope=scope or _etf_plugin.account_scope_for(symbol)
        return self.brokers[scope] if hasattr(self,'brokers') else self.broker

    def intent_quote(self,symbol):
        bid,ask,received=getattr(self,'quotes',{}).get(symbol,(None,None,0))
        return {'bid':str(bid),'ask':str(ask),'received_ns':received,'intent_ns':time.time_ns()} if bid and ask else {}

    def recover_assumptions(self):
        rate=Decimal(self.settings.env.get('ETF_ASSUMED_FEE_RATE','.000148'))
        self.journal.recover_assumptions(rate)

    def begin_checkpoint_recovery(self):
        if getattr(self,'_runner_task',None) is not None:
            raise RuntimeError('ETF checkpoint recovery requires a stopped runner')
        if getattr(self,'_checkpoint_recovery_state','IDLE') == 'QUARANTINED':
            raise RuntimeError('ETF runtime is permanently quarantined')
        if getattr(self,'_checkpoint_recovery_state','IDLE') == 'RECOVERING':
            raise RuntimeError('ETF checkpoint recovery is already in progress')
        self._checkpoint_recovery_state = 'RECOVERING'
        self._checkpoint_recovery_reason = 'checkpoint_restore_started'
        self._checkpoint_initialized = False
        self.phase = 'recovery_pending'

    def quarantine_checkpoint_recovery(self, reason):
        self._checkpoint_recovery_state = 'QUARANTINED'
        self._checkpoint_recovery_reason = str(reason)[:256] or 'checkpoint_restore_failed'
        self.phase = 'recovery_quarantined'

    def mark_checkpoint_recovery_ready(self, day):
        if self._checkpoint_recovery_state != 'RECOVERING':
            return False
        if not day or self._day != day:
            return False
        self._checkpoint_recovery_state = 'READY'
        self._checkpoint_recovery_reason = None
        self._checkpoint_initialized = True
        # Do not force legacy alpha readiness. The existing initialize phase is
        # preserved so normal quote/history/timer checks still own economic
        # readiness; this gate only proves the checkpoint cut.
        return True

    def _check_checkpoint_dispatch(self):
        # This fence is independent of ETF_EXECUTION_GUARDS. A runtime that has
        # not proved its exact restored cut must never reach a broker mutation.
        if getattr(self,'_checkpoint_recovery_state','IDLE') in {'RECOVERING','QUARANTINED'}:
            raise EtfDispatchBlocked('ETF runtime is not READY after checkpoint recovery')

    async def initialize(self):
        self._check_ownership()
        if self.execution_guards_enabled():
            self.journal.recover_dispatch_attempts()
        states={}
        for scope,broker in self.brokers.items():
            await broker.connect()
            states[scope]=await broker.account_state()
        state=states['mock']
        power = await self.broker.buying_power('069500', Decimal('1'))
        # Divide actual cash balance into thirds. Buying power can already
        # contain a broker haircut; enforce that separately for each order.
        available = Decimal(str(state['summary'].get('dnca_tot_amt') or power['ord_psbl_cash']))
        values = self.broker.client.provider.values
        identity = hashlib.sha256(('mock:'+values['KIS_ACCOUNT_NUMBER']+':01').encode()).hexdigest()
        self.journal.initialize(available, identity)
        secondary=self.brokers['mock2'].client.provider.values
        if secondary['KIS_ACCOUNT_NUMBER']==values['KIS_ACCOUNT_NUMBER']:
            raise RuntimeError('The two mock routes must refer to distinct accounts')
        secondary_id=hashlib.sha256(('mock2:'+secondary['KIS_ACCOUNT_NUMBER']+':01').encode()).hexdigest()
        old=self.journal.db.execute("SELECT value FROM metadata WHERE key='mock2_account_identity'").fetchone()
        if old and old[0]!=secondary_id: raise RuntimeError('Mock2 journal account identity changed')
        self.journal.db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('mock2_account_identity',secondary_id))
        if getattr(self,'_event_source',None) is None:
            await self.bus.connect(initialize=False)
        # Pending broker fills are reconciled before the aggregate balance check.
        if self.journal.pending():
            await self.reconcile()
            states={scope:await broker.account_state() for scope,broker in self.brokers.items()}
        for scope,state in states.items():
            expected={}
            for book in self.journal.summary().values():
                for symbol,quantity in book['positions'].items():
                    if self.broker_for(symbol) is self.brokers[scope]: expected[symbol]=expected.get(symbol,0)+quantity
            actual={p['pdno']:int(Decimal(p['hldg_qty'])) for p in state['positions'] if int(Decimal(p['hldg_qty']))>0}
            if actual!=expected: raise RuntimeError(f'{scope} broker positions differ from strategy-owned positions')
        self.recover_assumptions()
        self._day = datetime.now(KST).strftime('%Y-%m-%d')
        self.journal.roll_day(self._day)
        self.restore_today(self._day)
        self.phase = 'waiting_session'

    def restore_today(self, day):
        # Intraday deployment must retain the opening minutes used by KP/KD.
        root=self.settings.state_dir/'etf-csv-sync'/'sessions'
        for symbol in self.today:
            path=root/(day+'-'+symbol+'.jsonl')
            if not path.exists(): continue
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row=json.loads(line)
                    timestamp=pd.Timestamp(row['timestamp'])
                    if row['date']!=day or not '09:01'<=timestamp.strftime('%H:%M')<='15:20': continue
                    row['timestamp']=timestamp
                    row['trading_value']=np.nan
                    open_ns=int(timestamp.tz_localize('Asia/Seoul').tz_convert('UTC').value)-60_000_000_000
                    self.today[symbol][open_ns]=row
                except (ValueError,KeyError): continue

    async def prepare(self, day: str):
        self.phase = 'warming_up'
        gap_generation = getattr(self,'_feed_gap_generation',0)
        try:
            await self.history.prepare(day)
            if gap_generation == getattr(self,'_feed_gap_generation',0):
                self._feed_history_ready = True
                self._restore_feed_readiness()
            self.phase = 'feed_recovery_required' if getattr(self,'_feed_invalid',set()) else 'ready'
        except Exception:
            self.phase = 'warmup_failed'
            log.exception('ETF history preparation failed')

    @staticmethod
    def _feed_base(symbol):
        return '229200' if symbol in {'229200','233740','251340'} else '069500'

    def _invalidate_market_feed(self, symbol):
        self.quotes.pop(symbol,None)
        if not hasattr(self,'_feed_invalid'):
            self._feed_invalid, self._feed_snapshots = set(),set()
        self._feed_invalid.add(symbol)
        self._feed_snapshots.discard(symbol)
        self._feed_history_ready = False
        self._feed_gap_generation = getattr(self,'_feed_gap_generation',0)+1
        base = self._feed_base(symbol)
        getattr(self,'features',{}).pop(base,None)
        # Existing bars may span the missing interval. Start a new live tail;
        # do not silently treat the pre-gap feature as a continuous window.
        getattr(self,'today',{}).get(base,{}).clear()
        self.phase = 'feed_recovery_required'

    def _restore_feed_readiness(self):
        if not getattr(self,'_feed_history_ready',False):
            return
        for symbol in tuple(getattr(self,'_feed_invalid',set())):
            if symbol not in self._feed_snapshots:
                continue
            try:
                if self.history.day != self._day:
                    continue
                self.history.vehicle_reference(symbol)
                self.fresh_quote(symbol)
                # Prior-day warmup and a book snapshot cannot establish the
                # causal coverage of missing intraday inputs. The transport
                # owner must separately attest this exact gap generation.
                verifier = getattr(self,'_gap_recovery_verifier',None)
                if verifier is None:
                    continue
                verified = verifier(symbol,self._feed_gap_generation)
                if inspect.isawaitable(verified):
                    if inspect.iscoroutine(verified):
                        verified.close()
                    continue
                if verified is not True:
                    continue
            except Exception:
                continue
            self._feed_invalid.remove(symbol)
        if not getattr(self,'_feed_invalid',set()) and self.phase == 'feed_recovery_required':
            self.phase = 'ready'

    def _feed_entry_blocked(self, strategy):
        base = '229200' if strategy == 'KD' else '069500'
        return any(self._feed_base(symbol) == base for symbol in getattr(self,'_feed_invalid',set()))

    def fresh_quote(self, symbol: str) -> tuple[Decimal,Decimal]:
        bid, ask, received = self.quotes.get(symbol, (Decimal(0),Decimal(0),0))
        if bid <= 0 or ask <= 0 or bid > ask or time.time_ns()-received > 10_000_000_000:
            raise RuntimeError(f'Fresh valid Kiwoom quote required for {symbol}')
        return bid, ask

    def execution_guards_enabled(self) -> bool:
        env = getattr(getattr(self,'settings',None),'env',{})
        return env.get('ETF_EXECUTION_GUARDS','false').lower() == 'true'

    def entry_blocked(self, strategy: str, scope: str | None = None) -> bool:
        """Honor the existing operational kill switch without opening its writer."""
        if self._feed_entry_blocked(strategy):
            return True
        if (self.settings.state_dir/'etf-mock'/'STOP').exists():
            return True
        if (self.settings.state_dir/'etf-mock'/'RECOVERY_IN_PROGRESS').exists():
            return True
        path = self.settings.state_dir/'vibetrading.sqlite3'
        if not path.exists():
            return True
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            account = 'account:kis-'+scope if scope else 'account:kis-mock'
            active = db.execute("SELECT 1 FROM kill_switch WHERE active=1 AND scope IN ('global',?,'etf-mock',?) LIMIT 1",(account,strategy)).fetchone()
            return bool(active)

    def _dispatch_evidence(self, intent):
        if not self.execution_guards_enabled():
            return None
        return {'contract':'etf-dispatch-v1','intent_sha256':hashlib.sha256(intent.model_dump_json().encode()).hexdigest(),
            'account_scope':intent.metadata['account_scope'],'strategy':intent.job_id,
            'signal_id':intent.signal_id,'valid_until_ts_ns':intent.metadata.get('valid_until_ts_ns'),
            'quote':intent.metadata.get('intent_quote'),'source_provider':intent.metadata.get('source_provider')}

    def _check_dispatch(self, intent):
        self._check_checkpoint_dispatch()
        self._check_ownership()
        symbol = intent.instrument.raw_symbol
        if intent.side == Side.BUY:
            if self.entry_blocked(intent.job_id,intent.metadata['account_scope']):
                raise EtfDispatchBlocked('ETF BUY dispatch halted by kill switch')
        else:
            owned = self.journal.book(intent.job_id)['positions'].get(symbol,0)
            reserved = sum(row['quantity']-row['filled'] for row in self.journal.pending(intent.job_id)
                if row['intent_id'] != intent.intent_id and row['symbol'] == symbol and row['side'] == 'sell')
            if intent.quantity > owned-reserved:
                raise EtfDispatchBlocked('ETF SELL dispatch exceeds unreserved owned shares')
        # Evaluate time after synchronous control/journal reads as well as
        # after adapter awaits; lock contention must not extend freshness.
        now = time.time_ns()
        expiry = intent.metadata.get('valid_until_ts_ns')
        if type(expiry) is not int or now >= expiry:
            raise EtfDispatchBlocked('ETF signal dispatch TTL expired or missing')
        bid, ask, received = self.quotes.get(symbol,(Decimal(0),Decimal(0),0))
        age = now-received
        quote_budget = 90_000_000_000 if intent.side == Side.SELL and intent.metadata.get('closing_auction') else 10_000_000_000
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid or not 0 <= age <= quote_budget:
            raise EtfDispatchBlocked('ETF dispatch quote is stale or invalid')

    async def _submit_staged(self, broker, intent):
        """The observational bus never determines whether broker handoff occurred."""
        guarded = self.execution_guards_enabled()
        self._check_checkpoint_dispatch()
        try:
            self._check_ownership()
            if broker is not self.brokers.get(intent.metadata.get('account_scope')):
                raise EtfDispatchBlocked('ETF broker differs from the owned physical account route')
            try:
                await self.bus.publish_order_intent(intent)
            except Exception as exc:
                log.warning('ETF intent observation deferred id=%s (%s)',intent.intent_id,type(exc).__name__)
            # The observation await can yield to a READY->RECOVERING transition;
            # recheck synchronously before either dispatch branch.
            self._check_checkpoint_dispatch()
            if guarded:
                namespace = type(broker).__dict__
                if (namespace.get('execution_permit_protocol') != 1 or 'submit_order_with_permit' not in namespace
                        or 'submit_order' in getattr(broker,'__dict__',{})):
                    raise EtfDispatchBlocked('ETF broker lacks an explicit paired dispatch permit capability')
                def permit():
                    self._check_dispatch(intent)
                    self.journal.dispatch_handoff(intent.intent_id)
                    try:
                        # FULL commit can itself consume the remaining TTL.
                        # This is still synchronously before session.post.
                        self._check_dispatch(intent)
                    except BaseException as exc:
                        self.journal.dispatch_not_sent(intent.intent_id,str(exc),before_post=True)
                        raise
                output = await broker.submit_order_with_permit(intent,permit)
                if self.journal.dispatch_state(intent.intent_id) != 'HANDOFF':
                    raise EtfDispatchBlocked('ETF broker returned without consuming its dispatch permit')
                return output
            return await broker.submit_order(intent)
        except KisOrderRejected as exc:
            self.journal.reject(intent.intent_id,str(exc),permission_failure=account_permission_rejection(exc))
            self.recover_assumptions()
            log.exception('KIS explicitly rejected ETF order')
        except BaseException as exc:
            if guarded and self.journal.dispatch_state(intent.intent_id) in {'PREPARED','NOT_SENT'}:
                self.journal.dispatch_not_sent(intent.intent_id,str(exc) or type(exc).__name__)
            else:
                self.journal.ambiguous(intent.intent_id)
                self.phase = 'order_reconciliation_required'
            raise

    async def place(self, strategy: str, symbol: str, side: Side, quantity: int, identity: str, *, direction: int = 0, safety_stop: float | None = None, auction_market: bool = False, recovery_cleanup: bool = False, valid_until_ts_ns: int | None = None):
        self._check_checkpoint_dispatch()
        self._check_ownership()
        key = hashlib.sha256((identity+':'+symbol+':'+side.value).encode()).hexdigest()
        requested_quantity, requested_expiry = quantity,valid_until_ts_ns
        old = self.journal.db.execute('SELECT payload,day FROM orders WHERE intent_id=?',(key,)).fetchone()
        if old:
            previous = OrderIntent.model_validate_json(old['payload'])
            meta = previous.metadata
            actual = (previous.job_id,previous.instrument.raw_symbol,previous.side,
                Decimal(str(meta.get('requested_quantity',previous.quantity))),meta.get('direction',0),
                meta.get('safety_stop'),meta.get('closing_auction',False),meta.get('recovery_cleanup',False),
                meta.get('requested_expiry'))
            if (old['day'] != self._day.replace('-','')
                    or actual != (strategy,symbol,side,Decimal(quantity),direction,safety_stop,auction_market,recovery_cleanup,valid_until_ts_ns)):
                raise ValueError('ETF immutable request collision')
            return
        valid_until_ts_ns = valid_until_ts_ns if valid_until_ts_ns is not None else time.time_ns()+_etf_plugin.FOLLOWUP_TTL_NS
        if side == Side.BUY and self._feed_entry_blocked(strategy):
            raise EtfDispatchBlocked('ETF entry feed requires snapshot and history recovery')
        broker=self.broker_for(symbol)
        scope=_etf_plugin.account_scope_for(symbol)
        # Every signal starts with a limit order. During the closing auction
        # retain the last continuous quote for the initial owned-position exit.
        if auction_market and side == Side.SELL:
            bid, ask, received = self.quotes.get(symbol,(Decimal(0),Decimal(0),0))
            if bid <= 0 or ask < bid or time.time_ns()-received > _etf_plugin.FOLLOWUP_TTL_NS:
                raise RuntimeError('Closing-auction exit needs a valid recent quote')
        else:
            bid, ask = self.fresh_quote(symbol)
        price = _etf_plugin.limit_price(side,bid,ask)
        if side == Side.BUY:
            power = await broker.buying_power(symbol, price)
            quantity = min(quantity, int(Decimal(str(power['max_buy_qty']))))
        if quantity <= 0:
            return
        history_context = self.history.vehicle_reference(symbol) if self.history.day else None
        intent = OrderIntent(intent_id=key,job_id=strategy,signal_id=identity,destination=OrderDestination.REAL,
            instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol=symbol,std_symbol=symbol,currency='KRW',exchange='KRX'),
            side=side,client_order_id=key[:32],quantity=Decimal(quantity),limit_price=price,
            metadata={'account_mode':'mock','account_scope':scope,'intent_quote':self.intent_quote(symbol),'source_provider':'kiwoom','strategy':strategy,'closing_auction':auction_market,'direction':direction,'safety_stop':safety_stop,'recovery_cleanup':recovery_cleanup,
                'vehicle_history':history_context,'valid_until_ts_ns':valid_until_ts_ns,
                'requested_quantity':requested_quantity,'requested_expiry':requested_expiry})
        if not self.journal.stage(intent,self._day.replace('-',''),direction,safety_stop,dispatch_evidence=self._dispatch_evidence(intent),require_evidence=self.execution_guards_enabled()):
            return
        output = await self._submit_staged(broker,intent)
        if output is not None:
            self.journal.accepted(key, output)
            log.info('KIS mock accepted strategy=%s symbol=%s side=%s qty=%s order=%s',strategy,symbol,side.value,quantity,output.get('ODNO',output.get('odno')))

    async def enter(self, strategy: str, side: int, leverage: float, identity: str, safety_stop: float | None = None):
        if self.entry_blocked(strategy):
            log.warning('ETF entry blocked by kill switch strategy=%s',strategy)
            return
        market = _etf_plugin.market_for(strategy)
        targets = _etf_plugin.entry_targets(strategy,side,leverage)
        # Both signal ETFs and actual leveraged/inverse vehicles must have
        # their own historical series before entering a position.
        for symbol, weight in targets.items():
            if weight > 0:
                self.history.vehicle_reference(symbol)
        book = self.journal.logical_book(strategy)
        if book['entries'] or book['positions'] or self.journal.pending(strategy):
            return
        if self.journal.pending() and any(r['status'] in {'unknown','submitting'} for r in self.journal.pending()):
            raise RuntimeError('Ambiguous order blocks all new entries until reconciled')
        capital = book['cash']-book['reserved']
        signal_symbol = _etf_plugin.signal_symbol_for(strategy)
        bid, ask = self.fresh_quote(signal_symbol)
        signal = Signal(signal_id=hashlib.sha256(identity.encode()).hexdigest(),strategy_id=strategy,
            instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,raw_symbol=signal_symbol,std_symbol=f'KIWOOM:KRX:{signal_symbol}'),
            action=SignalAction.ENTER_LONG if side==1 else SignalAction.ENTER_SHORT,
            event_ts_ns=time.time_ns(),reference_price=(bid+ask)/2,valid_until_ts_ns=time.time_ns()+_etf_plugin.FOLLOWUP_TTL_NS,
            source_event_id=identity,metadata={'account_mode':'mock','leverage':leverage,'weights':targets})
        try:
            await self.bus.publish_signal(signal)
        except Exception as exc:
            log.warning('ETF signal observation deferred id=%s (%s)',signal.signal_id,type(exc).__name__)
        prices = {}
        for symbol, weight in targets.items():
            if weight <= 0:
                continue
            symbol_bid, symbol_ask = self.fresh_quote(symbol)
            prices[symbol] = _etf_plugin.limit_price(Side.BUY,symbol_bid,symbol_ask)
        planned = _etf_plugin.plan_entry_quantities(capital,targets,prices)
        for symbol, quantity in planned:
            await self.place(strategy,symbol,Side.BUY,quantity,identity,direction=side,safety_stop=safety_stop,valid_until_ts_ns=signal.valid_until_ts_ns)

    async def exit(self, strategy: str, identity: str, *, auction_market: bool = False):
        if self.journal.pending(strategy):
            return
        actual=self.journal.book(strategy)['positions']
        planned=self.journal.logical_book(strategy)['positions']
        for symbol,quantity in planned.items():
            real_quantity=min(quantity,actual.get(symbol,0))
            if real_quantity:
                await self.place(strategy,symbol,Side.SELL,real_quantity,identity,auction_market=auction_market)
        for symbol,quantity in planned.items():
            quantity=max(0,quantity-actual.get(symbol,0))
            if not quantity: continue
            bid,_=self.fresh_quote(symbol)
            key=hashlib.sha256((identity+':assumed:'+symbol).encode()).hexdigest()
            with self.journal.transaction():
                from portfolio.etf_recovery_ledger import active, record_fill
                fee=Decimal(self.settings.env.get('ETF_ASSUMED_FEE_RATE','.000148'))
                if active(self.journal.db,strategy,self._day):
                    record_fill(self.journal,key,strategy,symbol,'sell',quantity,bid,self._day,time.time_ns(),
                                basis='fresh executable bid; close reconstructed position',
                                source='live_Kiwoom_quote',fee_rate=fee)
                else:
                    self.journal.assume_fill(key,strategy,symbol,'sell',quantity,bid,self._day,time.time_ns(),
                        'Close position from permission-rejected assumed entry',fee)

    async def settle_recovery_overhang(self):
        """Close only physical inventory absent from the corrected strategy book."""
        from portfolio.etf_recovery_ledger import active
        for strategy in ('C3','KP','KD'):
            if not active(self.journal.db,strategy,self._day) or self.journal.pending(strategy):
                continue
            actual=self.journal.book(strategy)['positions']
            desired=self.journal.logical_book(strategy)['positions']
            for symbol,qty in actual.items():
                extra=qty-max(0,desired.get(symbol,0))
                if extra>0:
                    if symbol not in self.quotes or time.time_ns()-self.quotes[symbol][2]>10_000_000_000:
                        continue
                    await self.place(strategy,symbol,Side.SELL,extra,
                        f'{strategy}:{self._day}:recovery_cleanup:{int(time.time())//60}',
                        recovery_cleanup=True)

    async def reconcile(self):
        self._check_ownership()
        for order in self.journal.pending():
            broker=self.broker_for(order['symbol'],order['account_scope'])
            if not order['order_number']:
                self.phase = 'order_reconciliation_required'
                # KIS cash orders have no broker client-id idempotency field.
                # Guessing by symbol/time could assign someone else's order.
                continue
            try:
                if _etf_plugin.followup_due(order,time.time_ns()):
                    # Persist once before POST: an uncertain cancellation also
                    # needs broker reconciliation, never automatic resubmission.
                    self.journal.db.execute('UPDATE orders SET cancel_requested=1,fallback_requested=1 WHERE intent_id=?',(order['intent_id'],))
                    try:
                        await broker.cancel_order(order['order_number'],order['organization'])
                    except Exception:
                        log.exception('Cancel outcome requires status reconciliation')
                    # Cancellation ACK is not proof that all remaining shares
                    # were cancelled. Read cumulative fills again before market.
                state = await broker.order_status(order['order_number'],day=order['day'],symbol=order['symbol'])
                self.journal.apply_status(order['intent_id'],state)
            except Exception:
                log.exception('KIS fill/cancel reconciliation failed; position remains pending')
        for original in self.journal.fallback_candidates():
            await self.replace_with_market(original)

    async def replace_with_market(self, original: dict):
        """One persistent replacement for the confirmed-cancelled remainder."""
        self._check_ownership()
        key = hashlib.sha256((original['intent_id']+':market-remainder').encode()).hexdigest()
        old = self.journal.db.execute('SELECT payload,day FROM orders WHERE intent_id=?',(key,)).fetchone()
        if old:
            saved = OrderIntent.model_validate_json(old['payload'])
            if (old['day'] != original['day'] or saved.job_id != original['strategy']
                    or saved.instrument.raw_symbol != original['symbol'] or saved.side.value != original['side']
                    or saved.metadata.get('account_scope') != original['account_scope']
                    or saved.metadata.get('replacement_for') != original['intent_id']):
                raise ValueError('ETF immutable replacement collision')
            return
        remaining = original['quantity']-original['filled']
        if remaining <= 0:
            self.journal.db.execute('UPDATE orders SET fallback_done=1 WHERE intent_id=?',(original['intent_id'],))
            return
        previous = OrderIntent.model_validate_json(original['payload'])
        broker=self.broker_for(original['symbol'],original['account_scope'])
        reserve_price = Decimal(0)
        if previous.side == Side.BUY:
            power = await broker.buying_power(original['symbol'],Decimal(0),market=True)
            reserve_price = Decimal(str(power.get('psbl_qty_calc_unpr') or 0))
            if reserve_price <= 0:
                raise RuntimeError('Broker market-buy reservation price is missing; retain replacement plan')
            book = self.journal.book(original['strategy'])
            remaining = min(remaining,int(Decimal(str(power['max_buy_qty']))),int((book['cash']-book['reserved'])/(reserve_price*Decimal('1.005'))))
        else:
            remaining = min(remaining,self.journal.book(original['strategy'])['positions'].get(original['symbol'],0))
        if remaining <= 0:
            self.journal.db.execute('UPDATE orders SET fallback_done=1 WHERE intent_id=?',(original['intent_id'],))
            log.warning('No affordable/owned remainder for market replacement id=%s',original['intent_id'])
            return
        replacement = previous.model_copy(update={'intent_id':key,'client_order_id':key[:32],
            'quantity':Decimal(remaining),'limit_price':None,'created_ts_ns':time.time_ns(),
            'metadata':previous.metadata|{'account_scope':original['account_scope'],'replacement_for':original['intent_id'],'intent_quote':self.intent_quote(original['symbol']),
                'reserve_unit_price':str(reserve_price),'execution':'market_after_5_seconds'}})
        if not self.journal.stage(replacement,original['day'],int(previous.metadata.get('direction',0)),previous.metadata.get('safety_stop'),replacement_for=original['intent_id'],dispatch_evidence=self._dispatch_evidence(replacement),require_evidence=self.execution_guards_enabled()):
            return
        output = await self._submit_staged(broker,replacement)
        if output is not None:
            self.journal.accepted(key,output)
            log.info('KIS mock market remainder strategy=%s symbol=%s qty=%s',original['strategy'],original['symbol'],remaining)

    async def evaluate_c3(self):
        """Use confirmed native day opens as soon as live trading begins."""
        identity = f'C3:{self._day}:open'
        if self.journal.db.execute('SELECT 1 FROM decisions WHERE id=?',(identity,)).fetchone():
            return
        if time.time_ns()-self._c3_attempt_ns < 2_000_000_000:
            return
        self._c3_attempt_ns = time.time_ns()
        etf = await self.kiwoom.post_api('/api/dostk/stkinfo','ka10001',{'stk_cd':'069500'})
        snapshot=json.loads((self.settings.state_dir/'kis-index'/'snapshot.json').read_text(encoding='utf-8'))
        if snapshot.get('day')!=self._day:
            raise RuntimeError('Current KIS KOSPI200 opening snapshot unavailable')
        index_open = abs(float(snapshot['bstp_nmix_oprc']))
        etf_open = abs(float(etf['open_pric']))
        if etf_open <= 0 or index_open <= 0:
            return
        decision = _etf_plugin.module_for('C3').native_open_decision(
            self.history,etf_open,index_open)
        leverage,detail = decision['leverage'],decision['detail']
        if leverage > 0:
            for symbol,weight in _etf_plugin.entry_targets('C3',1,leverage).items():
                if weight > 0:
                    self.fresh_quote(symbol)
        if self.journal.decision('C3',identity,detail) and leverage > 0:
            await self.enter('C3',1,leverage,identity)

    async def event(self, data: dict, *, state_only: bool = False):
        event = event_from_dict(data)
        symbol = event.instrument.raw_symbol
        if state_only and event.event_type != EventType.BAR_1M:
            return
        if event.event_type == EventType.DATA_GAP and event.instrument.asset_class == AssetClass.ETF and symbol in SYMBOLS:
            self._invalidate_market_feed(symbol)
            return
        if (event.provider != 'kiwoom' and not(event.provider=='toss'and event.ingest_mode=='backfill')) or symbol not in SYMBOLS:
            return
        now = datetime.now(KST)
        if event.trading_date != now.strftime('%Y-%m-%d'):
            return
        if event.event_type == EventType.ORDER_BOOK and event.bids and event.asks:
            if symbol in getattr(self,'_feed_invalid',set()) and (event.update_type != BookUpdateType.SNAPSHOT or event.fast is False):
                return
            self.quotes[symbol] = (max(x.price for x in event.bids),min(x.price for x in event.asks),event.receive_ts_ns)
            if symbol in getattr(self,'_feed_invalid',set()):
                self._feed_snapshots.add(symbol)
                self._restore_feed_readiness()
            if (symbol=='069500' and self.phase=='ready'
                    and '09:00' <= now.strftime('%H:%M') <= '09:05'):
                async with self._lock:
                    try:
                        await self.evaluate_c3()
                    except Exception:
                        log.exception('C3 native open waiting for index/quote')
            return
        if event.event_type == EventType.INDEX_QUOTE and event.instrument.asset_class == AssetClass.INDEX:
            return
        if event.event_type == EventType.TRADE and symbol=='069500':
            if self.phase=='ready' and event.quantity > 0 and '09:00' <= now.strftime('%H:%M') <= '09:05':
                async with self._lock:
                    try:
                        await self.evaluate_c3()
                    except Exception:
                        log.exception('C3 open signal waiting for native inputs/quotes')
            return
        if event.event_type != EventType.BAR_1M or symbol not in self.today:
            return
        if event.open_ts_ns >= event.close_ts_ns:
            return  # Auction is not a continuous minute signal bar.
        timestamp = pd.Timestamp(event.close_ts_ns,unit='ns',tz='UTC').tz_convert('Asia/Seoul').tz_localize(None)
        if not '09:01' <= timestamp.strftime('%H:%M') <= '15:20':
            return
        self.today[symbol][event.open_ts_ns] = {'timestamp':timestamp,'date':event.trading_date,'symbol':symbol,'source':event.provider.upper(),
            **{key:float(getattr(event,key)) for key in ('open','high','low','close','volume')},'trading_value':np.nan}
        if state_only:
            return
        if symbol not in {'069500','229200'}:
            return  # Keep vehicle bars; KP/KD signal thresholds use their base ETFs.
        if self.phase != 'ready' or event.ingest_mode != 'realtime' or time.time_ns()-event.close_ts_ns > 120_000_000_000:
            return
        if (self.settings.state_dir/'etf-mock'/'RECOVERY_IN_PROGRESS').exists():
            return
        frame = pd.DataFrame(list(self.today[symbol].values())).sort_values('timestamp')
        async with self._lock:
            c3_identity = f'C3:{self._day}:open'
            c3_decided = self.journal.db.execute('SELECT 1 FROM decisions WHERE id=?',(c3_identity,)).fetchone()
            if symbol == '069500' and timestamp.strftime('%H:%M') <= '09:05' and event.volume > 0 and not c3_decided:
                try:
                    await self.evaluate_c3()
                except Exception:
                    log.exception('C3 input/entry not ready')
            strategy = 'KP' if symbol == '069500' else 'KD'
            book = self.journal.logical_book(strategy)
            if timestamp.minute in {0,30} and timestamp.strftime('%H:%M') <= '15:00':
                bid, ask = self.fresh_quote(symbol)
            else:
                bid = ask = None
            decision = _etf_plugin.intraday_source_decision(
                strategy,self.history,timestamp,frame,book,bid=bid,ask=ask)
            action = decision['action']
            if action == 'paper_stop':
                identity = f'{strategy}:{event.close_ts_ns}:safety_stop'
                if self.journal.decision(strategy,identity,{'action':action}):
                    await self.exit(strategy,identity)
                return
            if timestamp.minute not in {0,30} or timestamp.strftime('%H:%M') > '15:00':
                return
            direction, leverage = decision['direction'], decision['leverage']
            identity = f'{strategy}:{event.close_ts_ns}:{action}'
            if not self.journal.decision(strategy,identity,{'direction':direction,'leverage':leverage,'action':action,'feature_id':decision['identity']}):
                return
            if action == 'entry':
                stop = decision['stop']
                if stop is not None and not np.isfinite(stop):
                    return
                await self.enter(strategy,direction,leverage,identity,stop)

    async def report_daily(self, day, now):
        from feeds.etf_csv_sync import completed_session
        identity = 'daily:'+day
        if self.journal.db.execute('SELECT 1 FROM notifications WHERE id=?',(identity,)).fetchone():
            return
        if not completed_session('XKRX',day,now,5):
            return
        data = await self.account_report(day)
        self.journal.notify(identity, 'mock_daily', data)

    async def account_report(self, day):
        """Store a current valuation for read-only Telegram queries."""
        closed = self.journal.db.execute("SELECT payload,created_ns FROM notifications WHERE id=? AND kind='mock_daily'", ('daily:'+day,)).fetchone()
        if closed:
            return await self.persist_account_report(day, json.loads(closed['payload']), closed['created_ns'])
        states={scope:await broker.account_state() for scope,broker in getattr(self,'brokers',{'mock':self.broker}).items()}
        end=start=Decimal(0); fees=Decimal(0); fees_available=True; positions={}; accounts={}
        for scope,state in states.items():
            summary=state['summary']; final=Decimal(str(summary['tot_evlu_amt'])); initial=Decimal(str(summary['bfdy_tot_asst_evlu_amt']))
            if not final.is_finite() or not initial.is_finite() or initial<=0: raise RuntimeError('Previous-day mock equity is unavailable')
            end+=final; start+=initial
            cost=summary.get('thdt_tlex_amt'); fees_available &= cost not in (None,'')
            fees+=Decimal(str(cost or 0))
            accounts[scope]={'net_pnl':str(final-initial),'equity':str(final),'fees':cost}
            positions.update({scope+':'+r['pdno']:r['hldg_qty'] for r in state['positions'] if Decimal(str(r['hldg_qty']))>0})
        marks={s:rows[max(rows)]['close'] for s,rows in self.today.items() if rows} if hasattr(self,'today') else {}
        opening={}
        if hasattr(self,'history'):
            for symbol,frame in self.history.daily.items():
                prior=frame[frame.date<day]
                if len(prior): opening[symbol]=prior.iloc[-1]['close']
        from portfolio.etf_recovery_ledger import excluded_orders
        excluded_symbols={r['symbol']for r in excluded_orders(self.journal.db,day=day)}
        for symbol in set(self.journal.assumed_positions())|excluded_symbols:
            client=self.market_client if hasattr(self,'market_client')else self.broker_for(symbol).client
            result=await client.get('/uapi/domestic-stock/v1/quotations/inquire-price',
                tr_id='FHKST01010100',params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol})
            marks[symbol]=Decimal(str(result['body']['output']['stck_prpr']))
            if marks[symbol]<=0: raise RuntimeError('Invalid close price for assumed position')
            await asyncio.sleep(.6)
        assumed=self.journal.assumed_report(day,marks,opening)
        data = {'day':day,'net_pnl':str(end-start),
            'equity':str(end),'fees':str(fees) if fees_available else None,'accounts':accounts,'assumed':assumed,
            'adjusted_net_pnl':str(end-start+Decimal(assumed['net_pnl'])),
            'adjusted_fees':str(fees+Decimal(assumed['fees'])) if fees_available else None,
            'fee_basis':'KIS 당일 제비용','pending_orders':len(self.journal.pending()),
            'positions':positions,'validation':self.validation_summary(day),
            '_recovery_marks':{s:str(v)for s,v in marks.items()}}
        return await self.persist_account_report(day, data)

    async def persist_account_report(self, day, data, asof_ns=None):
        broker_fills = []
        brokers = getattr(self, 'brokers', {'mock':self.broker})
        if all(hasattr(broker, 'daily_fills') for broker in brokers.values()):
            for scope, broker in brokers.items():
                broker_fills.extend((scope, day, row['order_number'], row['symbol'], row['side'], row['quantity'], row['notional'])
                    for row in await broker.daily_fills(day))
            with self.journal.transaction():
                self.journal.db.execute('DELETE FROM broker_trades WHERE day=?', (day,))
                self.journal.db.executemany('INSERT OR REPLACE INTO broker_trades VALUES(?,?,?,?,?,?,?)', broker_fills)
                data['broker_fills_complete'] = True
        from portfolio.etf_recovery_ledger import filter_account_report
        marks={s:rows[max(rows)]['close']for s,rows in self.today.items()if rows}if hasattr(self,'today')else{}
        # account_report already obtained quotes for assumed/excluded positions;
        # use that exact valuation when supplied, otherwise the latest closed bar.
        marks.update(data.pop('_recovery_marks',{}))
        data=filter_account_report(self.journal,data,marks)
        self.journal.db.execute('INSERT OR REPLACE INTO account_reports VALUES(?,?,?)',
            (day,json.dumps(data,default=str),asof_ns or time.time_ns()))
        return data

    async def refresh_account_report(self, day):
        try:
            await self.account_report(day)
        except Exception as exc:
            log.warning('Trade valuation refresh deferred (%s)', type(exc).__name__)

    def validation_summary(self,day):
        if not hasattr(self,'settings'): return None
        path=self.settings.state_dir/'etf-mock/mock2-validation.json'
        if not path.exists(): return None
        data=json.loads(path.read_text())
        if not data.get('complete') or datetime.fromtimestamp(data['created_ns']/1e9,KST).strftime('%Y-%m-%d')!=day: return None
        gross=sum((Decimal(str(row.get('notional') or 0))*(1 if row['side']=='sell' else -1) for row in data['orders']),Decimal(0))
        net=Decimal(data['validation_net_pnl'])
        return {'net_pnl':str(net),'fees':str(gross-net),'included_in_actual':True}

    async def timer(self):
        last_reconcile = 0.
        last_health = 0.
        last_daily_attempt = 0.
        last_report = 0.
        report_task = None
        while True:
            now = datetime.now(KST)
            day = now.strftime('%Y-%m-%d')
            if now.weekday()<5 and '09:00'<=now.strftime('%H:%M')<='15:35' and time.monotonic()-last_report>=60 and (report_task is None or report_task.done()):
                last_report = time.monotonic()
                report_task = asyncio.create_task(self.refresh_account_report(day))
                self._report_task = report_task
            if day != self._day:
                self._day = day
                self.journal.roll_day(day)
                self.today = {s:{} for s in ETF_HISTORY_ROLES}
                self.phase = 'waiting_session'
            if now.weekday() < 5 and '08:40' <= now.strftime('%H:%M') <= '15:30' and (self.history.day != day or (getattr(self,'_feed_invalid',set()) and not getattr(self,'_feed_history_ready',False))):
                if self._warmup_task is None or self._warmup_task.done():
                    self._warmup_task = asyncio.create_task(self.prepare(day))
            async with self._lock:
                pending = self.journal.pending()
                due = any(_etf_plugin.followup_due(r,time.time_ns()) for r in pending)
                if (pending and (due or time.monotonic()-last_reconcile >= 1)) or self.journal.fallback_candidates():
                    try:
                        await self.reconcile()
                    except Exception:
                        log.exception('ETF reconciliation/replacement deferred')
                    last_reconcile = time.monotonic()
                recovering=(self.settings.state_dir/'etf-mock'/'RECOVERY_IN_PROGRESS').exists()
                if not recovering and self.phase=='ready':
                    try:
                        await self.settle_recovery_overhang()
                    except Exception:
                        log.exception('Recovery physical inventory reconciliation deferred')
                clock = now.strftime('%H:%M:%S')
                for strategy in ('C3','KP','KD'):
                    if recovering:
                        continue
                    session = _etf_plugin.session_action(strategy,pd.Timestamp(now))
                    # Submit while continuous quotes are still tradable. KRX's
                    # closing auction cannot be reconstructed as a past fill.
                    if session == 'session_exit':
                        try:
                            await self.exit(strategy,f'{strategy}:{day}:session_exit:{int(time.time())//30}',auction_market=strategy=='C3')
                        except Exception:
                            log.exception('ETF session liquidation waiting for valid quote/broker')
                    if session == 'overnight_recovery' and self.journal.book(strategy)['positions'] and self.journal.book(strategy)['day'] == day and self.journal.book(strategy)['entries'] == 0:
                        try:
                            await self.exit(strategy,f'{strategy}:{day}:overnight_recovery:{int(time.time())//30}')
                        except Exception:
                            log.exception('ETF overnight recovery waiting for quotes')
                if clock>='15:35:00' and now.weekday()<5 and time.monotonic()-last_daily_attempt>=60:
                    last_daily_attempt=time.monotonic()
                    try:
                        await self.report_daily(day,now)
                    except Exception:
                        log.exception('Mock daily report deferred pending broker response')
            if time.monotonic()-last_health >= 10:
                touch(self.settings,'etf-mock',detail={'phase':self.phase,'account_mode':'mock',
                    'account_routes':{'mock':['069500','229200'],'mock2':['122630','251340']},
                    'assumed_positions':self.journal.assumed_positions(),
                    'day':day,'books':{s:{k:str(v) if isinstance(v,Decimal) else v for k,v in b.items()} for s,b in self.journal.summary().items()},
                    'pending_orders':len(self.journal.pending()),'c3_inputs_ready':not self.history.index.empty,
                    'vehicle_bars':{s:{'completed_today':len(rows),
                        'latest_close':max(rows.values(),key=lambda r:r['timestamp'])['close'] if rows else None} for s,rows in self.today.items()}})
                last_health = time.monotonic()
            await asyncio.sleep(.1 if self.journal.pending() else 1)

    async def run(self, *, initialized: bool = False):
        if getattr(self,'_runner_task',None) is not None:
            raise RuntimeError('ETF runner already started')
        self._check_ownership()
        self._runner_task = asyncio.current_task()
        try:
            await self._run(initialized=initialized)
        finally:
            self._runner_task = None
            if getattr(self,'_close_task',None) is None:
                await self.close()

    async def _run(self, *, initialized: bool):
        self._check_ownership()
        if not initialized:
            await self.initialize()
        client, subscription, pending, iterator = None,None,None,None
        source = getattr(self,'_event_source',None)
        if source is None:
            queue = asyncio.Queue(maxsize=10000)
            async def receive(msg):
                await queue.put(json.loads(msg.data))
            client = await nats.connect(self.settings.nats.url)
            subscription = await client.subscribe('live.md.*.*.etf.>',cb=receive)
        else:
            iterator = source.__aiter__()
        timer = asyncio.create_task(self.timer())
        try:
            while True:
                if timer.done():
                    timer.result()
                    raise RuntimeError('ETF timer ended')
                if iterator is None:
                    try:
                        data = await asyncio.wait_for(queue.get(),timeout=1)
                    except asyncio.TimeoutError:
                        continue
                else:
                    if pending is None:
                        pending = asyncio.create_task(anext(iterator),name='local-etf-next-event')
                    # Quiet timers never cancel the pending source read. The
                    # source's bounded queue remains the only market backlog.
                    done,_ = await asyncio.wait((pending,timer),timeout=1,return_when=asyncio.FIRST_COMPLETED)
                    if timer in done:
                        timer.result()
                        raise RuntimeError('ETF timer ended')
                    if pending not in done:
                        continue
                    try:
                        data = pending.result()
                    except StopAsyncIteration as exc:
                        raise RuntimeError('ETF local event source ended') from exc
                    pending = None
                try:
                    await self.event(data)
                except Exception:
                    log.exception('ETF event processing failed; submitted state retained')
        finally:
            if iterator is not None:
                for symbol in SYMBOLS:
                    self._invalidate_market_feed(symbol)
            timer.cancel()
            if pending is not None:
                pending.cancel()
            await asyncio.gather(*[task for task in (timer,pending) if task is not None],return_exceptions=True)
            if iterator is not None and getattr(iterator,'aclose',None) is not None:
                await iterator.aclose()
            if subscription is not None:
                await subscription.unsubscribe()
            if client is not None:
                await client.close()


async def check(warmup: bool):
    loop = EtfTradingLoop()
    try:
        await loop.initialize()
        if warmup:
            await loop.history.prepare(datetime.now(KST).strftime('%Y-%m-%d'))
            etf = await loop.kiwoom.post_api('/api/dostk/stkinfo','ka10001',{'stk_cd':'069500'})
            index = await loop.kiwoom.post_api('/api/dostk/sect','ka20001',{'mrkt_tp':'0','inds_cd':'201'})
            c3_check = loop.history.c3(abs(float(etf['open_pric'])),abs(float(index['open_pric'])))
        else:
            c3_check = None
        market_power = await loop.broker.buying_power('069500',Decimal(0),market=True)
        if Decimal(str(market_power.get('psbl_qty_calc_unpr') or 0)) <= 0:
            raise RuntimeError('Market fallback reservation price is unavailable')
        result = {'mock':True,'books':loop.journal.summary(),'c3_ready':not loop.history.index.empty,'phase':loop.phase,
            'reference_usage':loop.history.reference_usage,'execution':'immediate_limit_then_5s_confirmed_remaining_market',
            'vehicle_history':{s:loop.history.vehicle_reference(s) for s in ETF_HISTORY_ROLES} if warmup else {},
            'market_reservation_price':market_power['psbl_qty_calc_unpr'],'native_open_signal_probe':c3_check}
        print(json.dumps(result,default=str))
        await loop.bus.close()
        loop.journal.db.close()
    finally:
        await loop.close()



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['check','run'])
    parser.add_argument('--warmup',action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(check(args.warmup) if args.command=='check' else EtfTradingLoop().run())


if __name__ == '__main__':
    main()
