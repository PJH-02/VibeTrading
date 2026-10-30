from decimal import Decimal
import json

import pytest

from execution.account_fees import AccountFeePolicy, parse_account_fee_policies, load_account_fee_policies
from execution.execution_ledger import AccountScope


def document(rate="0.00015", *, account="acct", environment="paper"):
    return {"schema_version": 1, "accounts": [{
        "account_scope": {"venue": "kiwoom", "account": account, "product": "cash", "environment": environment},
        "rate": rate,
    }]}


def test_explicit_decimal_scope_and_zero_are_pinned():
    policies = parse_account_fee_policies(document())
    scope = AccountScope("kiwoom", "acct", "cash", "paper")
    assert policies[scope.key] == AccountFeePolicy(scope, Decimal("0.00015"))
    assert parse_account_fee_policies(document("0"))[scope.key].rate == 0
    assert parse_account_fee_policies(document(account="other")) != policies
    assert parse_account_fee_policies(document(environment="sandbox")) != policies


@pytest.mark.parametrize("bad", [0.1, True, "-0.1", "1.01", "NaN", "Infinity", "oops"])
def test_invalid_rates_rejected(bad):
    with pytest.raises(ValueError):
        parse_account_fee_policies(document(bad))


def test_duplicate_scope_and_missing_fields_rejected():
    value = document()
    value["accounts"].append(value["accounts"][0])
    with pytest.raises(ValueError):
        parse_account_fee_policies(value)
    with pytest.raises(ValueError):
        parse_account_fee_policies({"schema_version": 1, "accounts": [{"rate": "0"}]})


def test_loader_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "fees.json"
    path.write_text('{"schema_version":1,"schema_version":1,"accounts":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_account_fee_policies(path)


def cash_fixture(root, *, balance=10000):
    from decimal import Decimal
    from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
    from execution.paper_execution import PaperExecution
    from execution.trading_contracts import ProductKind, ProductSpec
    from portfolio.portfolio_ledger import PortfolioLedger
    scope = AccountScope("kiwoom", "local-fixture", "cash", "paper")
    ledger = PortfolioLedger(root / "paper" / "book.sqlite", scope, owner_root=root, mode="paper",
        max_orders=20, max_fills=40, max_events=80, max_document_bytes=200000,
        max_db_bytes=4000000, max_wal_bytes=8000000)
    ledger.seed_balance("KRW", Decimal(balance), {"fixture": "paper"})
    instrument = InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.EQUITY,
                              raw_symbol="005930", std_symbol="005930")
    rules = ProductSpec(instrument, ProductKind.CASH, Decimal(1), Decimal(1), Decimal(1), Decimal(1),
        "KRW", "KRW", "KRW", "net", "cash", "native fee absent", "none", "cash",
        Decimal(0), Decimal(0))
    matcher = PaperExecution(ledger, entry_latency_ns=0, cancel_latency_ns=1, max_open_orders=8,
        max_depth=8, max_receipts=100, max_state_bytes=500000, stale_after_ns=10000)
    return scope, ledger, rules, matcher


def cash_intent(instrument, identity="buy", qty="5", price="100"):
    from decimal import Decimal
    from core.models import OrderDestination, OrderIntent, Side
    return OrderIntent(intent_id=identity, job_id="job", signal_id="signal", destination=OrderDestination.REAL,
        instrument=instrument, side=Side.BUY, quantity=Decimal(qty), limit_price=Decimal(price),
        client_order_id="client-"+identity, created_ts_ns=1)


def test_policy_binding_exact_fee_math_and_paper_reserve_matcher_attribution(tmp_path):
    from decimal import Decimal, localcontext
    from core.models import BookUpdateType, OrderBookEvent, OrderBookLevel, TradeEvent, Side
    from execution.account_fees import recover_account_fee_policy
    scope, ledger, rules, matcher = cash_fixture(tmp_path)
    policy = AccountFeePolicy(scope, Decimal("0.01"))
    with localcontext() as context:
        context.prec = 2
        assert policy.calculate_fee(Decimal("3"), Decimal("101"), Decimal("1")) == Decimal("3.03")
    intent = cash_intent(rules.instrument, qty="2")
    matcher.on_book(OrderBookEvent(instrument=rules.instrument, venue=rules.instrument.venue,
        provider="fixture", event_id="book", receive_ts_ns=1, wallclock_ts_ns=1, source_ts_ns=1,
        sequence=1, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=Decimal("99"), quantity=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("10"))]))
    created, admitted = ledger.admit_order(intent, "strategy", {"fixture": True}, 1000, rules,
        funding_buffer=0, account_notional_limit=10000, strategy_notional_limit=10000,
        account_capital_limit=10000, strategy_capital_limit=10000, now_ns=1, account_fee_policy=policy)
    assert created and Decimal(admitted["reservation"]) == Decimal("202.00")
    assert recover_account_fee_policy(type(rules).from_document(__import__("json").loads(admitted["rules"])), scope) == policy
    # Submit path uses the same policy binder and exact saved product provenance.
    ledger.close()
    scope, ledger, rules, matcher = cash_fixture(tmp_path / "paper-flow")
    matcher.on_book(OrderBookEvent(instrument=rules.instrument, venue=rules.instrument.venue,
        provider="fixture", event_id="book", receive_ts_ns=1, wallclock_ts_ns=1, source_ts_ns=1,
        sequence=1, sync_generation=1, depth=1,
        bids=[OrderBookLevel(price=Decimal("99"), quantity=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("10"))]))
    reservation = matcher.submit(cash_intent(rules.instrument, qty="2"), "strategy", {"decision": 1}, 1000,
        rules, 2, reference_price=Decimal("100"), funding_buffer=0, account_notional_limit=10000,
        strategy_notional_limit=10000, account_capital_limit=10000, strategy_capital_limit=10000,
        account_fee_policy=AccountFeePolicy(scope, Decimal("0.01")))
    assert Decimal(ledger.get("buy")["reservation"]) == Decimal("202.00")
    matcher.advance(2)
    matcher.on_trade(TradeEvent(instrument=rules.instrument, venue=rules.instrument.venue,
        provider="fixture", event_id="trade", receive_ts_ns=3, wallclock_ts_ns=3, source_ts_ns=3,
        price=Decimal("100"), quantity=Decimal("2"), aggressor_side=Side.SELL))
    cash = ledger.report()["strategy_cash"][0]
    assert cash["strategy"] == "strategy" and Decimal(cash["fees"]) == Decimal("2.00")
    assert Decimal(cash["amount"]) == Decimal("-202.00")
    assert Decimal(ledger.report()["balances"]["KRW"]) == Decimal("9798.00")
    ledger.close()


def test_policy_marker_and_scope_collisions_fail_closed():
    from decimal import Decimal
    from core.models import AssetClass, InstrumentId, Venue
    from execution.account_fees import recover_account_fee_policy
    from execution.trading_contracts import ProductKind, ProductSpec
    scope = AccountScope("kiwoom", "acct", "cash", "paper")
    instrument = InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.EQUITY,
                              raw_symbol="x", std_symbol="x")
    policy = AccountFeePolicy(scope, Decimal("0.01"))
    base = ProductSpec(instrument, ProductKind.CASH, 1, 1, 1, 0, "KRW", "KRW", "KRW", "net", "cash",
        "base", "fund", "margin", 0, 0)
    bound = policy.bind_product(scope, base)
    assert recover_account_fee_policy(bound, scope) == policy
    with pytest.raises(ValueError):
        AccountFeePolicy(scope, Decimal("0.02")).bind_product(scope, bound)
    for other in (AccountScope("kiwoom", "other", "cash", "paper"),
                  AccountScope("kiwoom", "acct", "cash", "sandbox")):
        with pytest.raises(ValueError):
            policy.bind_product(other, base)
        with pytest.raises(ValueError):
            recover_account_fee_policy(bound, other)
    with pytest.raises(ValueError):
        AccountFeePolicy(AccountScope("toss", "acct", "cash", "paper"), Decimal("0.1")).bind_product(
            AccountScope("toss", "acct", "cash", "paper"), base)
    with pytest.raises(ValueError):
        recover_account_fee_policy(type(bound)(**{**bound.__dict__, "fee_provenance":
            'ACCOUNT_CONFIG:{"scope":"x","rate":"0.01","rate":"0.01"}'}), scope)


def test_real_kiwoom_scripted_fee_less_history_configured_recovery_restart(tmp_path):
    import asyncio, json, weakref
    from datetime import datetime
    from decimal import Decimal
    from execution.brokers import KiwoomBrokerAdapter
    from execution.perp_execution import HttpResponse
    from execution.reconciliation import ReconciliationSession
    from execution.reconciliation_sources import NativePollingSource

    scope, ledger, rules, _ = cash_fixture(tmp_path)
    account_fee = AccountFeePolicy(scope, Decimal("0.001"))
    intent = cash_intent(rules.instrument)
    created, _ = ledger.admit_order(intent, "alpha", {"ticket": "fixture"}, 1000000000000000000,
        rules, funding_buffer=0, account_notional_limit=10000, strategy_notional_limit=10000,
        account_capital_limit=10000, strategy_capital_limit=10000, now_ns=1,
        account_fee_policy=account_fee)
    assert created
    adapter = KiwoomBrokerAdapter.__new__(KiwoomBrokerAdapter)
    adapter.account_context = {"venue":"kiwoom", "product":"krx_stock", "environment":"mock",
        "account_alias":"synthetic", "account_identity_verified":True, "account_id":"local-fixture",
        "native_account_id":"1234567890"}
    adapter.use_mock = True
    adapter.base_url = "https://mockapi.kiwoom.com"
    adapter._used_permits = weakref.WeakSet()
    adapter._access_token = lambda: asyncio.sleep(0, result="synthetic-token")
    class ScriptedKiwoomTransport:
        mutation_permit_protocol = 1
        def __init__(self): self.calls = []
        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return HttpResponse(200, {"acnt_ord_cntr_prst_array": [
                {"ord_no":"7654321","stk_cd":"A005930","io_tp_nm":"현금매수","cntr_no":"0000001","cntr_qty":"2","cntr_uv":"100","cntr_tm":"09:30:01"},
                {"ord_no":"7654321","stk_cd":"A005930","io_tp_nm":"현금매수","cntr_no":"0000002","cntr_qty":"1","cntr_uv":"100","cntr_tm":"09:30:02"}]},
                {"cont-yn":"N","next-key":""})
    adapter.transport = ScriptedKiwoomTransport()
    adapter.account_state = lambda: asyncio.sleep(0, result={"complete":True,
        "account_identity":{"native_account_id":"1234567890"}, "positions":[{"stk_cd":"A005930","rmnd_qty":"3","trde_able_qty":"3"}]})
    adapter.list_open_orders = lambda symbol: asyncio.sleep(0, result={"complete":True,"rows":[]})
    day = "20261028"
    day_start = (datetime.strptime(day, "%Y%m%d").date().toordinal() - datetime(1970,1,1).date().toordinal()) * 86400000000000 - 32400000000000
    source_scope = adapter.scope.key
    def make_session():
        src = NativePollingSource(adapter, [{"symbol":"005930","instrument_key":rules.instrument.key,
            "day":day,"native_order_id":"7654321"}], identity_verifier=lambda facts: True,
            clock_ns=lambda:day_start+86_400_000_000_000-1)
        async def order(native_id, symbol=None):
            return {"kind":"order","scope_key":source_scope,"order_id":"7654321","symbol":"005930",
                "instrument_key":rules.instrument.key,"status":"CANCELED","terminal_proven":True,
                "cumulative_quantity":"3"}
        src.query_order = order
        return src, ReconciliationSession(ledger, source_scope, max_orders=4,max_events=30,
            max_document_bytes=50000,max_staleness_ns=100000000000,clock_ns=lambda:day_start+86400000000000-1)
    async def run():
        for iteration in range(2):
            src, session = make_session()
            session.bind_order("buy", "7654321", symbol="005930") if iteration == 0 else None
            await session.start(src.authenticate, src.subscribe)
            await session.reconcile(src, start_ns=day_start, end_ns=day_start+86400000000000-1)
            assert session.snapshot()["ready"] is True
            assert ledger.position("alpha", rules.instrument.key) == Decimal("3")
            if iteration == 0:
                assert session.ingest({"kind":"fill", "scope_key":source_scope,
                    "event_id":f"kiwoom:{day}:7654321:0000001", "order_id":"7654321",
                    "symbol":"005930", "quantity":"2", "price":"100",
                    "fee":"999", "fee_currency":"USD"}, session.generation) is False
            assert Decimal(ledger.report()["strategy_cash"][0]["fees"]) == Decimal("0.30")
            assert Decimal(ledger.report()["strategy_cash"][0]["amount"]) == Decimal("-300.30")
            assert Decimal(ledger.report()["balances"]["KRW"]) == Decimal("9699.70")
            assert ledger.readiness()["gap_count"] == 0
            if iteration == 0:
                assert session.snapshot()["orders"]["buy"]["status"] == "CANCELED"
                await session.stop()
        assert ledger.position("alpha", rules.instrument.key) == Decimal("3")
    asyncio.run(run())
    assert all(call[2]["headers"]["api-id"] == "kt00009" for call in adapter.transport.calls)
    ledger.close()


def test_configured_native_fill_commit_failure_rolls_back_ledger_and_session_version(tmp_path, monkeypatch):
    import json
    from decimal import Decimal
    from execution.reconciliation import ReconciliationSession
    scope, ledger, rules, _ = cash_fixture(tmp_path)
    policy = AccountFeePolicy(scope, Decimal("0.01"))
    ledger.admit_order(cash_intent(rules.instrument), "alpha", {"fixture": 1}, 1000, rules,
        funding_buffer=0, account_notional_limit=10000, strategy_notional_limit=10000,
        account_capital_limit=10000, strategy_capital_limit=10000, now_ns=1,
        account_fee_policy=policy)
    source_scope = json.dumps(["kiwoom", "local-fixture", "cash", "mock"], separators=(",", ":"))
    session = ReconciliationSession(ledger, source_scope, max_orders=4, max_events=10,
        max_document_bytes=50000, max_staleness_ns=1000)
    session.bind_order("buy", "7654321", symbol="005930")
    async def acknowledge():
        return {"success": True, "scope_key": source_scope}
    import asyncio
    asyncio.run(session.start(acknowledge, acknowledge))
    before_version, before_events = ledger.account_version(), session.events()
    database = ledger._db
    class FailingCommit:
        def __getattr__(self, name): return getattr(database, name)
        def execute(self, sql, *args):
            if sql == "COMMIT": raise OSError("injected commit failure")
            return database.execute(sql, *args)
    monkeypatch.setattr(ledger, "_db", FailingCommit())
    event = {"kind":"fill","scope_key":source_scope,"event_id":"fill-rollback","order_id":"7654321",
        "symbol":"005930","quantity":"1","price":"100","fee":None,"fee_currency":None}
    with pytest.raises(OSError, match="injected"):
        session.ingest(event, session.generation)
    assert ledger.get("buy")["filled"] == "0"
    assert ledger.account_version() == before_version
    assert session.events() == before_events
    monkeypatch.setattr(ledger, "_db", database)
    assert session.ingest(event, session.generation)
    assert Decimal(ledger.get("buy")["filled"]) == Decimal("1")
    assert Decimal(ledger.report()["strategy_cash"][0]["fees"]) == Decimal("1.00")
    ledger.close()


def test_policy_rate_must_fit_product_spec_decimal_envelope():
    for rate in ("1e-39", "123456789012345678901234567890123456789"):
        with pytest.raises(ValueError):
            parse_account_fee_policies(document(rate))


def test_kiwoom_numeric_incoming_fee_cannot_bypass_missing_account_config(tmp_path):
    import asyncio, json
    from decimal import Decimal
    from execution.reconciliation import ReconciliationSession
    scope, ledger, rules, _ = cash_fixture(tmp_path)
    ledger.admit_order(cash_intent(rules.instrument), "alpha", {"fixture": 1}, 1000, rules,
        funding_buffer=0, account_notional_limit=10000, strategy_notional_limit=10000,
        account_capital_limit=10000, strategy_capital_limit=10000, now_ns=1)
    source_scope = json.dumps(["kiwoom", "local-fixture", "cash", "mock"], separators=(",", ":"))
    session = ReconciliationSession(ledger, source_scope, max_orders=4, max_events=10,
        max_document_bytes=50000, max_staleness_ns=1000)
    session.bind_order("buy", "7654321", symbol="005930")
    async def acknowledge(): return {"success": True, "scope_key": source_scope}
    asyncio.run(session.start(acknowledge, acknowledge))
    assert session.ingest({"kind":"fill", "scope_key":source_scope, "event_id":"fee-less-config-required",
        "order_id":"7654321", "symbol":"005930", "quantity":"1", "price":"100",
        "fee":"0.01", "fee_currency":"KRW", "fee_known":True}, session.generation)
    event = session.events()[-1]["event"]
    assert event["fee"] is None and event["fee_known"] is False
    assert event["fee_currency"] is None and event["fee_basis"] == "CONFIG_REQUIRED"
    assert ledger.readiness()["gap_count"] > 0
    assert ledger.report()["balances"]["KRW"] == "9900"
    ledger.close()


# Lead-authored independent cost, multi-strategy and true restart regressions.
"""Lead-authored config-cost arithmetic, receipt and restart checks; no broker fee receiver."""
import asyncio,json
from decimal import Decimal as D,localcontext
import pytest
from core.models import AssetClass,InstrumentId,Venue,OrderIntent,OrderDestination,Side,OrderBookEvent,OrderBookLevel
from execution.execution_ledger import AccountScope
from execution.trading_contracts import ProductSpec,ProductKind
from execution.reconciliation import ReconciliationSession
from execution.paper_execution import PaperExecution
from portfolio.portfolio_ledger import PortfolioLedger
from runtime.local_config import parse_account_fee_policies

SCOPE=AccountScope('kiwoom','configured-account','cash','paper')
SOURCE=AccountScope('kiwoom','configured-account','cash','mock').key
SYMBOL='005930';NOW=1_800_000_000_000_000_000

def policy(rate='0.01',scope=SCOPE):
    doc={'schema_version':1,'accounts':[{'account_scope':{'venue':scope.venue,'account':scope.account,'product':scope.product,'environment':scope.environment},'rate':rate}]}
    return parse_account_fee_policies(doc)[scope.key]

def rules():
    return ProductSpec(InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.EQUITY,raw_symbol=SYMBOL,std_symbol=SYMBOL),ProductKind.CASH,D(1),D(1),D(1),D(1),'KRW','KRW','KRW','net','cash','old synthetic zero','cash not applicable','cash',D(0),D(0))

def intent(identity='i',qty='5'):
    return OrderIntent(intent_id=identity,job_id='j',signal_id=identity,destination=OrderDestination.REAL,instrument=rules().instrument,side=Side.BUY,quantity=D(qty),limit_price=D(100),client_order_id='client-'+identity,created_ts_ns=NOW,metadata={})

def book(root):
    b=PortfolioLedger(root/'paper/book.sqlite',SCOPE,owner_root=root,mode='paper',max_orders=20,max_fills=100,max_events=100,max_document_bytes=100000,max_db_bytes=4000000,max_wal_bytes=8000000)
    if not b.report()['balances']:b.seed_balance('KRW',D(10000),{'fixture':'configured cost'})
    return b

BUDGETS=dict(funding_buffer=0,account_notional_limit=10000,strategy_notional_limit=10000,account_capital_limit=10000,strategy_capital_limit=10000)
def admit(b,selected=None):
    return b.admit_order(intent(),'alpha',{'fixture':'configured cost'},NOW+1000,rules(),now_ns=NOW,account_fee_policy=selected or policy(),**BUDGETS)

def session(b):return ReconciliationSession(b,SOURCE,max_orders=10,max_events=80,max_document_bytes=90000,max_staleness_ns=1000,clock_ns=lambda:NOW)
async def ok():return True

def fill(identity,qty,**overrides):
    return {'kind':'fill','scope_key':SOURCE,'event_id':identity,'order_id':'7654321','symbol':SYMBOL,'quantity':qty,'price':'100','fee':None,'fee_known':False,'fee_currency':None,**overrides}

def test_configured_kiwoom_costs_are_ready_without_broker_fee_and_restart_once(tmp_path):
    b=book(tmp_path);admit(b);assert D(b.get('i')['reservation'])==D(505)
    s=session(b);s.bind_order('i','7654321',symbol=SYMBOL);asyncio.run(s.start(ok,ok))
    first=fill('native-1','2');late=fill('native-2','1')
    assert s.ingest(first,s.generation)
    pending={'kind':'order','scope_key':SOURCE,'order_id':'7654321','symbol':SYMBOL,'status':'CANCEL_PENDING','terminal_proven':False,'cumulative_quantity':'2'}
    s.ingest(pending,s.generation);assert D(b.get('i')['reservation'])==D(303)
    assert s.ingest(late,s.generation)
    assert not s.ingest({**first,'fee':'999999','fee_currency':'USD','fee_known':True},s.generation)
    terminal={**pending,'status':'CANCELED','terminal_proven':True,'cumulative_quantity':'3'}
    class NativeFacts:
        async def history(self,start_ns,end_ns):return {'scope_key':SOURCE,'complete':True,'start_ns':start_ns,'end_ns':end_ns,'fills':[first,late],'funding':[]}
        async def account_snapshot(self):return {'scope_key':SOURCE,'complete':True,'identity_verified':True,'observed_ns':NOW,'positions':{rules().instrument.key:'3'},'orders':[terminal],'account':{'native_balance':'not used as configured fee authority'}}
    assert asyncio.run(s.reconcile(NativeFacts(),start_ns=0,end_ns=NOW))['ready']
    assert D(b.report()['balances']['KRW'])==D(9697)
    cash=next(row for row in b.report()['strategy_cash'] if row['strategy']=='alpha')
    assert D(cash['fees'])==D(3);assert D(cash['amount'])==D(-303)
    assert b.position('alpha',rules().instrument.key)==D(3)
    assert all(e['event']['fee_basis']=='ACCOUNT_CONFIG' for e in s.events() if e['kind']=='fill')
    b.close();b=book(tmp_path);s=session(b);assert not s.snapshot()['ready'];asyncio.run(s.start(ok,ok))
    assert asyncio.run(s.reconcile(NativeFacts(),start_ns=0,end_ns=NOW))['ready']
    assert D(b.report()['balances']['KRW'])==D(9697)
    with pytest.raises(ValueError):admit(b,policy('0.02'))
    assert D(b.report()['balances']['KRW'])==D(9697)
    b.close()


def test_configured_cost_retains_128_digit_arithmetic_under_small_global_context(tmp_path):
    b=book(tmp_path);admit(b,policy('0.012345678901234567890123456789'))
    s=session(b);s.bind_order('i','7654321',symbol=SYMBOL)
    with localcontext() as context:
        context.prec=6
        assert s.ingest(fill('precise','3'))
    cash=next(row for row in b.report()['strategy_cash'] if row['strategy']=='alpha')
    assert D(cash['fees'])==D('3.7037036703703703670370370367')
    b.close()


def test_paper_uses_same_config_for_reserve_fill_and_two_strategy_costs(tmp_path):
    b=book(tmp_path)
    m=PaperExecution(b,entry_latency_ns=10,cancel_latency_ns=5,max_open_orders=8,max_depth=8,max_receipts=100,max_state_bytes=500000,stale_after_ns=1000)
    def market(now,sequence):return OrderBookEvent(instrument=rules().instrument,venue=Venue.KIWOOM,provider='synthetic-config-cost',event_id='book-'+str(sequence),receive_ts_ns=now,wallclock_ts_ns=now,source_ts_ns=now,sequence=sequence,sync_generation=1,depth=1,bids=[OrderBookLevel(price=D(99),quantity=D(5))],asks=[OrderBookLevel(price=D(100),quantity=D(5))])
    for ident,qty,strategy,time,seq in [('a','2','alpha',NOW,1),('b','1','beta',NOW+20,2)]:
        m.on_book(market(time,seq))
        m.submit(intent(ident,qty),strategy,{'fixture':'configured cost'},NOW+1000,rules(),time,account_fee_policy=policy(),post_only=False,time_in_force='IOC',**BUDGETS)
        assert D(b.get(ident)['reservation'])==D(qty)*D(101)
        m.advance(time+10)
        assert b.get(ident)['state']=='FILLED'
    cash={row['strategy']:row for row in b.report()['strategy_cash']}
    assert D(cash['alpha']['fees'])==D(2);assert D(cash['beta']['fees'])==D(1)
    assert D(cash['alpha']['amount'])==D(-202);assert D(cash['beta']['amount'])==D(-101)
    assert D(b.report()['balances']['KRW'])==D(9697)
    assert D(b.report()['reserved']['KRW'])==0
    b.close();b=book(tmp_path)
    m2=PaperExecution(b,entry_latency_ns=10,cancel_latency_ns=5,max_open_orders=8,max_depth=8,max_receipts=100,max_state_bytes=500000,stale_after_ns=1000)
    assert D(b.report()['balances']['KRW'])==D(9697)
    assert m2.report()['fills']==2
    b.close()
