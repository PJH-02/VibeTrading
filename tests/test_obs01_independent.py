"""Lead-owned exact input fixtures. Expected numbers are independent literals."""
from decimal import Decimal as D
from core.models import InstrumentId,AssetClass,Venue,Side,OrderIntent,OrderDestination
from execution.trading_contracts import ProductSpec,ProductKind
from execution.execution_ledger import AccountScope
from execution.account_fees import AccountFeePolicy
from portfolio.portfolio_ledger import PortfolioLedger
NOW_MS=1800000000000
NOW_NS=NOW_MS*1000000


def product(venue,asset,symbol,currency,multiplier,kind):
    return ProductSpec(instrument=InstrumentId(venue=venue,asset_class=asset,raw_symbol=symbol,std_symbol=symbol,currency=currency),kind=kind,multiplier=D(multiplier),tick_size=D(1),lot_size=D(1),min_notional=D(0),quote_currency=currency,settlement_currency=currency,collateral_currency=currency,position_mode='net',margin_mode='cash' if kind==ProductKind.CASH else 'isolated',fee_provenance='explicit configured fixture',funding_provenance='explicit attributed fixture',margin_model_provenance='local simulation',maker_fee_rate=D('.00015'),taker_fee_rate=D('.00015'))


def ledger(root,scope):
    return PortfolioLedger(root/'paper'/'book.sqlite3',scope,owner_root=root,mode='paper',max_orders=64,max_fills=100,max_events=512,max_document_bytes=200000,max_db_bytes=8000000,max_wal_bytes=16000000)


def admit(book,rules,identity,strategy,side,qty,price,fee_policy=None):
    intent=OrderIntent(intent_id=identity,job_id=strategy,signal_id='signal-'+identity,destination=OrderDestination.REAL,instrument=rules.instrument,side=side,quantity=D(qty),limit_price=D(price),client_order_id='client-'+identity,created_ts_ns=NOW_NS)
    book.admit_order(intent,strategy,{'source':'lead scripted input'},NOW_NS+1000000000,rules,funding_buffer=D(0),account_notional_limit=D('100000000'),strategy_notional_limit=D('100000000'),account_capital_limit=D('100000000'),strategy_capital_limit=D('100000000'),margin_rate=D('.1'),now_ns=NOW_NS,account_fee_policy=fee_policy)
    return intent


def shared_perp(root):
    rules=product(Venue.BYBIT,AssetClass.CRYPTO_PERP,'BTCUSDT','USDT','10',ProductKind.LINEAR_PERPETUAL)
    book=ledger(root,AccountScope('bybit','shared-private-account','linear_perpetual','paper'))
    book.seed_balance('USDT',D(1000000),{'source':'deposit excluded from profit'})
    admit(book,rules,'alpha-buy','alpha',Side.BUY,'2','100')
    book.apply_fill('alpha-buy-fill','alpha-buy',2,100,fee=2)
    admit(book,rules,'beta-sell','beta',Side.SELL,'3','105')
    book.apply_fill('beta-sell-fill','beta-sell',3,105,fee=3)
    admit(book,rules,'alpha-close','alpha',Side.SELL,'1','110')
    book.apply_fill('alpha-close-fill','alpha-close',1,110,fee=1)
    version=book.account_version()
    assert book.apply_funding('funding-allocated',rules.instrument.key,8,'USDT',{'period':'scripted'},allocations={'alpha':D(3),'beta':D(5)},account_version=version)
    assert not book.apply_funding('funding-allocated',rules.instrument.key,8,'USDT',{'period':'scripted'},allocations={'alpha':D(3),'beta':D(5)},account_version=version)
    return book,rules


def cash_partial_late(root):
    rules=product(Venue.KIS,AssetClass.ETF,'069500','KRW','1',ProductKind.CASH)
    book=ledger(root,AccountScope('kis','private-mock-account','cash','paper'))
    book.seed_balance('KRW',D(1000000),{'source':'deposit excluded from profit'})
    policy=AccountFeePolicy(book.scope,D('.00015'))
    admit(book,rules,'cash-buy','cash-strategy',Side.BUY,10,10000,policy)
    book.apply_fill('buy-first','cash-buy',6,10000,fee=policy.calculate_fee(D(6),D(10000),D(1)))
    book.cancel('cash-buy',confirmed=True,evidence={'scripted':'cancel terminal ack'})
    book.apply_fill('buy-late','cash-buy',4,10000,fee=policy.calculate_fee(D(4),D(10000),D(1)))
    admit(book,rules,'cash-sell','cash-strategy',Side.SELL,4,11000,policy)
    book.apply_fill('sell-fill','cash-sell',4,11000,fee=policy.calculate_fee(D(4),D(11000),D(1)))
    return book,rules


from decimal import Decimal as D
import asyncio,threading,time,hashlib
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def independent_telemetry_buffers():
    from operations.dashboard import latency
    latency._fresh_process_state()
    yield
    latency._fresh_process_state()
from operations.dashboard.projection import paper_projection
from operations.dashboard.pnl import paper_pnl_projection


def project(book,rules,price,now=NOW_MS,currency=None):
    doc=paper_projection([book.path] if hasattr(book,"path") else [Path(book._db.execute("PRAGMA database_list").fetchone()[2])],limit=500,include_observability=True)
    assert not doc["errors"],doc["errors"]
    scope=doc["accounts"][0]["scope"]
    return paper_pnl_projection(doc,generated_ms=now,marks=[{"key":rules.instrument.key,"account_scope":scope,"currency":currency or rules.settlement_currency,"price":str(price),"provider":"lead-local-mark","last_event_ms":NOW_MS}])


def test_real_shared_perp_exact_mark_fees_funding_no_account_profit(tmp_path):
    book,rules=shared_perp(tmp_path)
    try:
        before=book._db.total_changes
        first=project(book,rules,108)
        second=project(book,rules,108)
        assert first==second and book._db.total_changes==before
        rows={x["strategy_id"]:x for x in first}
        expected={"alpha":("100","80","3","3","180",2),"beta":("0","-90","3","5","-88",1)}
        for sid,values in expected.items():
            row=rows[sid]
            for key,v in zip(("realized_pnl","unrealized_pnl","fees","funding","net_pnl"),values):assert D(row[key])==D(v),(sid,key,row)
            assert row["trades"]==values[-1] and row["status"]=="AVAILABLE"
        assert sum(D(x["net_pnl"]) for x in rows.values())==D(92)
        assert all(x["net_pnl"] is None for x in project(book,rules,108,NOW_MS+10001))
        assert all(x["net_pnl"] is None for x in project(book,rules,108,currency="USD"))
    finally:book.close()


def test_real_cash_partial_cancel_late_fill_exact_configfee(tmp_path):
    book,rules=cash_partial_late(tmp_path)
    try:
        rows=project(book,rules,10500);assert len(rows)==1
        row=rows[0]
        assert D(row["realized_pnl"])==D(4000)
        assert D(row["unrealized_pnl"])==D(3000)
        assert D(row["fees"])==D("21.6") and D(row["net_pnl"])==D("6978.4")
        assert row["trades"]==3 and row["position_count"]==1
    finally:book.close()


@pytest.mark.asyncio
async def test_monitor_stat_never_blocks_eventloop_or_shutdown(tmp_path,monkeypatch):
    from operations.dashboard import latency as m
    entered=threading.Event();release=threading.Event();threads=[]
    original=Path.is_dir
    def blocked(path):
        if path==tmp_path:
            threads.append(threading.get_ident());entered.set();release.wait(3)
        return original(path)
    monkeypatch.setattr(Path,"is_dir",blocked)
    stop=asyncio.Event();start=time.monotonic()
    task=asyncio.create_task(m.export_metrics(tmp_path,stop=stop,instance_id="lead-slow"))
    try:
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.001)
        assert entered.is_set() and threads[0]!=threading.get_ident()
        # Trading-loop heartbeat and exporter stop both progress while stat is stalled.
        await asyncio.wait_for(asyncio.sleep(.001),timeout=.1)
        stop.set();await asyncio.wait_for(task,timeout=.1)
        assert time.monotonic()-start<.5
    finally:release.set();task.cancel()


def test_internal_packet_timing_preserves_equality_and_repr():
    from runtime.bus import MarketEventPacket
    from dataclasses import fields
    timing=[x for x in fields(MarketEventPacket) if x.name in {"receive_monotonic_ns","normalization_ended_ns"}]
    assert len(timing)==2 and all(not x.compare and not x.repr for x in timing)
    a=MarketEventPacket(None,"x",b"payload",b"live",1,2)
    b=MarketEventPacket(None,"x",b"payload",b"live",100,200)
    assert a==b and hash(a)==hash(b) and repr(a)==repr(b)


def test_real_unallocated_cash_without_gap_and_fullprecision(tmp_path):
    book,rules=shared_perp(tmp_path)
    try:
        with book.transaction():
            book._strategy_credit('@unallocated_fee','USDT',D('-1.125'),fee=D('1.125'))
        rows=project(book,rules,108)
        assert all(x['net_pnl'] is None and x['attribution_status']=='INCOMPLETE' for x in rows if x['strategy_id']!='UNALLOCATED')
        row=next(x for x in rows if x['strategy_id']=='UNALLOCATED')
        assert D(row['fees'])==D('1.125')
        assert D(row['unallocated_amount'])==D('-1.125')
    finally:book.close()


def test_cash_arithmetic_does_not_round_to_global_decimal_context():
    from decimal import localcontext
    value='123456789012345678901234567890.125'
    doc={'accounts':[],'positions':[],'orders':[],'fills':[],'pnl':[{'strategy':'precision','account':'s','currency':'KRW','mode':'paper','realized_pnl':value,'fees':'0.001','funding':'0.002'}]}
    with localcontext() as ctx:
        ctx.prec=10
        row=paper_pnl_projection(doc,generated_ms=NOW_MS)[0]
    assert row['net_pnl']=='123456789012345678901234567890.126'


def test_packet_preparation_preserves_original_payload_and_internal_timing():
    from scripts.refactoring.encoding_probe import cases
    from runtime import bus as module
    from runtime.bus import prepare_market_event
    event=cases()["raw_snapshot"]
    a=prepare_market_event(event);b=prepare_market_event(event)
    assert a.payload==module.dumps(event) and a==b
    assert a.normalization_ended_ns is not None
    assert a.receive_monotonic_ns==event.receive_monotonic_ns


@pytest.mark.asyncio
async def test_scripted_order_route_advances_with_stalled_monitor_io(tmp_path,monkeypatch):
    from operations.dashboard import latency as m
    import sys
    sys.path.insert(0,str(Path(__file__).parent/"refactoring"))
    try:
        import test_exec01_owner
    finally:
        sys.path.pop(0)
    monitor=tmp_path/'monitor';monitor.mkdir()
    entered=threading.Event();release=threading.Event()
    original=Path.is_dir
    def blocked(path):
        if path==monitor:
            entered.set();release.wait(3)
        return original(path)
    monkeypatch.setattr(Path,'is_dir',blocked)
    stop=asyncio.Event();task=asyncio.create_task(m.export_metrics(monitor,stop=stop,instance_id='lead-order-progress'))
    try:
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.001)
        assert entered.is_set()
        # Existing credential-free contract: strategy -> native scripted cash broker -> fills.
        await asyncio.wait_for(test_exec01_owner.test_explicit_cross_venue_source_plan_uses_synthetic_stock_route_and_facts(tmp_path),timeout=1)
        assert any(x['stage']=='adapter_ack' for x in m.snapshot()['metrics'])
        stop.set();await asyncio.wait_for(task,timeout=.1)
    finally:release.set();task.cancel()


def test_cgroup_unknown_resources_are_nullable_and_reader_compatible(tmp_path,monkeypatch):
    from operations.dashboard import latency as m
    monkeypatch.setattr(m,'_cgroup_resources',lambda:dict.fromkeys(('cgroup_cpu_seconds','cgroup_memory_bytes','cgroup_io_some_avg10','cgroup_io_full_avg10')))
    doc=m.snapshot()
    for key in ('cgroup_cpu_seconds','cgroup_memory_bytes','cgroup_io_some_avg10','cgroup_io_full_avg10'):assert doc['resources'][key] is None
    import json
    path=tmp_path/'latency.json';path.write_text(json.dumps(doc))
    assert m.read_latency_snapshots([path],now_ms=doc['generated_ms'])


def test_actual_native_ledger_replays_global_fill_arrival_not_intent_update(tmp_path):
    from execution.execution_ledger import ExecutionLedger,AccountScope
    from core.models import InstrumentId,Venue,AssetClass,OrderIntent,OrderDestination,Side
    from operations.dashboard.pnl import native_ledger_pnl_projection
    scope=AccountScope('bybit','private-scripted','perpetual','sandbox')
    path=tmp_path/'native.sqlite3'
    book=ExecutionLedger(path,scope,max_intents=16,max_evidence_bytes=100000,max_fills=100,authority_root=tmp_path)
    inst=InstrumentId(venue=Venue.BYBIT,asset_class=AssetClass.CRYPTO_PERP,raw_symbol='BTCUSDT',std_symbol='BTCUSDT',currency='USDT')
    try:
        for ident,side,qty in [('A',Side.BUY,2),('C',Side.BUY,1),('B',Side.SELL,1)]:
            intent=OrderIntent(intent_id=ident,job_id='lead-native',signal_id=ident,destination=OrderDestination.REAL,instrument=inst,side=side,quantity=D(qty),limit_price=D(100),client_order_id='c-'+ident,created_ts_ns=NOW_NS)
            book.authorize(intent,evidence={'source':'local scripted fixture'},expiry_ns=NOW_NS+100000,epochs={},reserve_unit=D(100),strategy_id='lead-native')
        for intent,fill,price,fee in [('A','a-first',100,'.1'),('C','c',200,'.2'),('B','b',150,'.15'),('A','a-late',120,'.12')]:
            assert book.apply_fill_once(intent,fill,quantity=1,price=price,fee=fee)
        assert not book.apply_fill_once('A','a-late',quantity=1,price=120,fee='.12')
        # Updating/canceling an old order must never move its fills in history.
        book.outcome('C','CANCELLED',{'source':'late terminal ACK'})
        products=[{'scope':['bybit','private-scripted','perpetual','sandbox'],'instrument':inst.key,'currency':'USDT','fee_currency':'USDT','settlement_currency':'USDT','multiplier':'1','funding_applicable':False,'funding_complete':True,'source':'lead explicit linear product'}]
        marks=[{'account_scope':scope.key,'key':inst.key,'currency':'USDT','price':'170','provider':'lead-mark','last_event_ms':NOW_MS}]
        before=book._db.total_changes
        rows=native_ledger_pnl_projection([path],product_metadata=products,marks=marks,generated_ms=NOW_MS)
        assert len(rows)==1;row=rows[0]
        assert D(row['realized_pnl'])==D(0) and D(row['unrealized_pnl'])==D(70),row
        assert D(row['fees'])==D('.57') and D(row['net_pnl'])==D('69.43')
        assert row['trades']==4 and row['mode']=='sandbox'
        assert book._db.total_changes==before
        assert native_ledger_pnl_projection([path],product_metadata=products,marks=marks,generated_ms=NOW_MS)==rows
        anchor_path=tmp_path/'anchor.sqlite3'
        anchor=ExecutionLedger(anchor_path,AccountScope('binance','private-anchor','perpetual','sandbox'),max_intents=16,max_evidence_bytes=100000,max_fills=100,authority_root=tmp_path)
        limits=dict(max_groups=16,max_facts=100,max_bytes=100000)
        try:
            for target in (book,anchor):
                target.record_group_fact('lead-group','plan','plan',{'spec':{'strategy_id':'lead-native'}},**limits)
            fact={'receipt_id':'lead-funding-1','scope_key':scope.key,'native_account_id':'private-scripted','source':'native_funding_receipt','currency':'USDT','amount':'3','allocations':{'lead-group':'3'},'unallocated':'0','period_ns':1000}
            for target in (book,anchor):target.record_group_fact('lead-group','fund1','funding-receipt',fact,**limits)
            for paths in ([path,anchor_path],[anchor_path,path]):
                projected=native_ledger_pnl_projection(paths,product_metadata=products,marks=marks,generated_ms=NOW_MS)
                row=next(x for x in projected if x['strategy_id']=='lead-native')
                assert D(row['funding'])==D(3) and D(row['net_pnl'])==D('72.43'),row
            for n,amount in enumerate(('1.25','.75')):
                fact={**fact,'receipt_id':'lead-unallocated-'+str(n),'period_ns':2000+n,'amount':amount,'allocations':{},'unallocated':amount}
                for target in (book,anchor):target.record_group_fact('lead-group','unallocated'+str(n),'funding-receipt',fact,**limits)
            projected=native_ledger_pnl_projection([anchor_path,path],product_metadata=products,marks=marks,generated_ms=NOW_MS)
            strategy=next(x for x in projected if x['strategy_id']=='lead-native')
            account=next(x for x in projected if x['strategy_id']=='UNALLOCATED')
            assert strategy['net_pnl'] is None and strategy['attribution_status']=='INCOMPLETE'
            assert D(account['funding'])==D(2) and account['net_pnl'] is None
        finally:anchor.close()
    finally:book.close()


def test_cgroup_reads_only_own_scope_and_rejects_escape(monkeypatch):
    import builtins,io
    from operations.dashboard import latency as m
    original=builtins.open
    scope=['0::/fixture-scope\n']
    def proc(path,*args,**kwargs):
        if str(path)=='/proc/self/cgroup':return io.StringIO(scope[0])
        return original(path,*args,**kwargs)
    files={'/sys/fs/cgroup/fixture-scope/memory.current':'4096\n',
           '/sys/fs/cgroup/fixture-scope/cpu.stat':'usage_usec 2500000\n',
           '/sys/fs/cgroup/fixture-scope/io.pressure':'some avg10=1.25 avg60=0 total=1\nfull avg10=0.50 avg60=0 total=0\n'}
    calls=[]
    def opened(path,*args,**kwargs):
        calls.append(str(path));assert str(path) in files
        return io.StringIO(files[str(path)])
    monkeypatch.setattr(builtins,'open',proc);monkeypatch.setattr(Path,'open',opened)
    assert m._cgroup_resources()=={'cgroup_cpu_seconds':2.5,'cgroup_memory_bytes':4096,'cgroup_io_some_avg10':1.25,'cgroup_io_full_avg10':.5}
    assert len(calls)==3
    scope[0]='0::/../../proc\n';calls.clear()
    assert all(x is None for x in m._cgroup_resources().values()) and not calls
