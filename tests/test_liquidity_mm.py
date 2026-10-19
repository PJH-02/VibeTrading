from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vibetrading.liquidity_mm import MODEL_VERSION
from vibetrading.liquidity_mm.config import Config, duration_seconds
from vibetrading.liquidity_mm.domain import Instrument, Inventory, Order, worst_inventory
from vibetrading.liquidity_mm.exchange import ExchangeError, TestnetClient
from vibetrading.liquidity_mm.journal import Journal, RiskLedger, RunLock
from vibetrading.liquidity_mm.market import Bin, DataFault, Episode, Market, Survival
from vibetrading.liquidity_mm.replay import FifoSimulator
from vibetrading.liquidity_mm.runner import Runner
from vibetrading.liquidity_mm.strategy import Decision, Evidence, Strategy


@pytest.fixture
def inst():
    return Instrument("SOLUSDT", D("0.01"), D("0.1"), D("0.1"), D("5"), D("10000"))


def book(bids=None, asks=None, *, u=10, seq=100, snapshot=True):
    return {"topic": "orderbook.50.SOLUSDT", "type": "snapshot" if snapshot else "delta", "ts": 100000,
            "cts": 100000, "data": {"u": u, "seq": seq, "b": bids if bids is not None else [["100", "10"], ["99.99", "10"]],
                                    "a": asks if asks is not None else [["100.01", "1"], ["100.02", "1"]]}}


def trade(key="t1", side="Buy", price="100.01", qty="1", ts=100010):
    return {"i": key, "S": side, "p": price, "v": qty, "T": ts}


@pytest.mark.parametrize("text,expected", [("30m", 1800), ("2h", 7200), ("60s", 60), ("1d", 86400), ("15", 15)])
def test_duration(text, expected):
    assert duration_seconds(text) == expected


@pytest.mark.parametrize("text", ["0", "-1h", "forever", "8d", "nan", "1.2.3m"])
def test_duration_rejects_unbounded_or_invalid(text):
    with pytest.raises(ValueError):
        duration_seconds(text)


@pytest.mark.parametrize("change", [{"entry_latency_seconds": 4}, {"horizon_seconds": 30, "entry_latency_seconds": 10},
                                   {"max_units": 4}, {"max_holding_seconds": 121}, {"symbol": "BTCUSD"},
                                   {"stop_bps": D("NaN")}, {"warmup_seconds": -1}, {"depth_haircut": D("1.1")}])
def test_configuration_rejects_invalid_risk(change):
    with pytest.raises(ValueError):
        replace(Config(), **change).validate()


def test_exact_tick_lot_rounding(inst):
    assert inst.ticks("100.019") == 10001
    assert inst.ticks("100.019", ceil=True) == 10002
    assert inst.lots("0.299999") == 2
    assert inst.price(10001) == D("100.01")
    assert not inst.valid_order(100, 1)
    assert inst.valid_order(100, 1, reduction=True)


def test_book_delta_snapshot_duplicates_and_noncontiguous_sequence(inst):
    m = Market(inst)
    m.book(book(), 1)
    delta = book([["100", "0"], ["99.98", "3"]], [], u=15, seq=119, snapshot=False)
    assert m.book(delta, 2)
    assert m.bid == 9999
    assert not m.book(delta, 3)
    m.book(book(u=1, seq=1), 4)
    assert m.bid == 10000
    assert m.update_id == 1


def test_regression_invalidates_book(inst):
    m = Market(inst)
    m.book(book(), 1)
    with pytest.raises(DataFault, match="sequence"):
        m.book(book([], [], u=9, seq=99, snapshot=False), 2)
    assert not m.ready


def test_delta_before_snapshot_and_crossed_book(inst):
    m = Market(inst)
    with pytest.raises(DataFault):
        m.book(book(snapshot=False), 0)
    with pytest.raises(DataFault):
        m.book(book(asks=[["100", "1"]]), 0)


def test_late_trades_do_not_rewrite_old_information(inst):
    m = Market(inst)
    m.book(book(), 0)
    assert m.trade(trade(ts=99000), 11)
    assert not m.trade(trade(ts=99000), 12)
    assert m.bins[2].buy == 10
    assert m.bins[0].buy == 0
    assert m.late_trades == 1
    assert not m.consumption["Buy"]


def test_unknown_side_stays_unknown(inst):
    m = Market(inst)
    m.trade(trade(side="UNKNOWN"), 1)
    assert m.bins[0].buy == m.bins[0].sell == 0
    assert m.unknown_fraction(6) == 1


def test_net_supply_identity_and_boundary_censor(inst):
    m = Market(inst)
    m.book(book(), 0)
    m.trade(trade(qty="0.6"), .2)
    m.book(book([], [["100.01", "0.7"]], u=11, seq=101, snapshot=False), .5)
    m.advance(1.1)
    assert m.bins[0].supply_ask == 3  # 7 - 10 + 6
    assert m.bins[0].consumed_ask == 6


def test_cancellation_alone_is_not_depletion_signal(inst):
    m = Market(inst)
    m.book(book(), 0)
    m.book(book([], [["100.01", "0"]], u=11, seq=101, snapshot=False), .5)
    m.advance(1.1)
    assert m.bins[0].consumed_ask == 0
    assert not m.depleted("Buy", 16)


def test_bid_recovery_requires_real_sell_consumption(inst):
    m = Market(inst)
    m.book(book(), 0)
    m.advance(1.1)
    assert len(m.recoveries["Sell"]) == 0
    m.trade(trade(side="Sell", price="100", qty="1"), 1.2)
    m.advance(2.2)
    assert len(m.recoveries["Sell"]) == 1


def test_episode_real_time_start_and_censor(inst):
    m = Market(inst)
    for k in range(6):
        m.bins[k] = Bin(buy=70, sell=30, trades=4)
    assert m.evaluate_episode(30) == "Buy"
    assert m.episode.start == 30
    # No past episode is invented and same bin does not learn twice.
    m.evaluate_episode(30.1)
    assert sum(m.survival.risk["Buy"]) == 0
    m.evaluate_episode(35)
    assert sum(m.survival.risk["Buy"]) == 1
    ends = sum(m.survival.ends["Buy"])
    m.censor()
    assert sum(m.survival.ends["Buy"]) == ends


def test_hazard_prior_and_direction_separation():
    s = Survival()
    assert s.probability("Buy", 0, 10) == .25
    s.observe("Buy", 0, False)
    assert s.probability("Buy", 0, 5) > s.probability("Sell", 0, 5)


def test_delayed_remaining_volume_not_reused(inst):
    m = Market(inst)
    m.episode = Episode("Buy", 0)
    m.rates["Buy"] = {10: 300., 30: 300., 120: 100.}
    assert m.volume("Buy", 20, 60, 0) < m.volume("Buy", 5, 60, 0) < m.volume("Buy", 0, 60, 0)


def test_projection_caps_observed_depth_and_includes_own_exit(inst):
    m = Market(inst)
    m.book(book(), 0)
    m.episode = Episode("Buy", 0)
    m.rates["Buy"] = {10: 100., 30: 100., 120: 100.}
    s = Strategy(Config(), m, D(".0002"), D(".00055"))
    target, _, _ = s.projection("Buy", 1, 0, 5, 60, stress=True)
    assert target == max(m.asks)
    assert s.projection("Buy", 10000, 0, 5, 60, stress=True)[0] == 0


def test_supply_absorption_prevents_projection(inst):
    m = Market(inst)
    m.book(book(), 0)
    m.episode = Episode("Buy", 30)
    m.rates["Buy"] = {10: 10., 30: 10., 120: 10.}
    for k in range(6):
        m.bins[k].supply_ask = 100
    s = Strategy(Config(), m, D(0), D(0))
    assert s.projection("Buy", 1, 30, 5, 60, stress=True)[0] == 0


def test_pending_cancel_and_unknown_are_not_netted(inst):
    inv = Inventory(net=1)
    buy = Order("b", "Buy", "ENTRY", 10000, 2, 0, 60, status="PENDING_CANCEL")
    sell = Order("s", "Sell", "ENTRY", 10002, 2, 0, 60, status="UNKNOWN")
    assert worst_inventory(inv, [buy, sell]) == (3, -1)
    buy.status = "Cancelled"
    buy.reported_filled = 1
    assert worst_inventory(inv, [buy, sell]) == (2, -1)


def test_inventory_duplicate_partial_late_fill_oldest_deadline():
    inv = Inventory()
    assert inv.fill("1", "Buy", 3, 10000, D(".1"), 10, deadline=60, target=10010, stop=9990)
    assert not inv.fill("1", "Buy", 3, 10000, D(".1"), 11, deadline=60, target=10010, stop=9990)
    inv.fill("2", "Buy", 1, 9998, D(".1"), 25, deadline=80, target=10012, stop=9989)
    assert inv.oldest_fill == 10
    assert inv.deadline == 60
    assert inv.target == 10010
    assert inv.stop == 9990
    inv.fill("3", "Sell", 4, 10005, D(".1"), 30, deadline=60, target=10010, stop=9990)
    assert inv.net == 0
    assert inv.realized_ticks_lots == 22
    assert inv.fees == D(".3")


def test_fifo_touch_does_not_fill_and_partial_actual_trade(inst):
    m = Market(inst)
    m.book(book(), 0)
    sim = FifoSimulator()
    o = Order("1", "Buy", "ENTRY", 10000, 2, 0, 60)
    sim.add(o, 0, 5)
    sim.advance(5, m)
    # Queue cancellation gets no fill credit.
    m.book(book([["100", "1"]], [], u=11, seq=101, snapshot=False), 6)
    assert sim.trade("Sell", 10000, 99, 6) == []
    assert sim.trade("Sell", 10000, 2, 7) == [(o, 1)]
    assert sim.trade("Sell", 10000, 1, 8) == [(o, 1)]


def test_fifo_cancel_latency_accepts_late_fill(inst):
    m = Market(inst)
    m.book(book(bids=[["100", "0.1"]]), 0)
    sim = FifoSimulator()
    o = Order("1", "Buy", "ENTRY", 10000, 1, 0, 60)
    sim.add(o, 0, 5)
    sim.advance(5, m)
    sim.cancel(o, 6, 5)
    assert sim.trade("Sell", 10000, 2, 7) == [(o, 1)]
    assert o.status == "Filled"


def test_fifo_one_trade_not_used_twice(inst):
    m = Market(inst)
    m.book(book(bids=[["100", "0.1"]]), 0)
    sim = FifoSimulator()
    first = Order("1", "Buy", "ENTRY", 10000, 2, 0, 60)
    second = Order("2", "Buy", "ENTRY", 10000, 2, 0, 60)
    sim.add(first, 0, 5)
    sim.add(second, 0, 5)
    sim.advance(5, m)
    assert sum(qty for _, qty in sim.trade("Sell", 10000, 4, 6)) == 3
    assert first.filled == 2 and second.filled == 1


def test_fifo_no_before_activation_and_postonly_rejection(inst):
    m = Market(inst)
    m.book(book(), 0)
    sim = FifoSimulator()
    o = Order("1", "Buy", "ENTRY", 10001, 1, 0, 60)
    sim.add(o, 0, 5)
    assert sim.trade("Sell", 10001, 1000, 4) == []
    sim.advance(5, m)
    assert o.status == "Rejected"


def test_risk_ledger_restart_keeps_halt_and_highwater(tmp_path):
    path = tmp_path / "risk.db"
    risk = RiskLedger(path)
    assert risk.observe("2026-10-03", D(100))[0] == D(100)
    risk.observe("2026-10-03", D(110))
    risk.halt("2026-10-03", "daily_equity_loss_limit")
    risk.close()
    reopened = RiskLedger(path)
    assert reopened.observe("2026-10-04", D(105)) == (D(105), D(110), True)
    reopened.close()


def test_run_lock_blocks_concurrent_process(tmp_path):
    with RunLock(tmp_path / "lock"):
        with pytest.raises(RuntimeError, match="Another"):
            with RunLock(tmp_path / "lock"):
                pass
    with RunLock(tmp_path / "lock"):
        pass


def test_evidence_no_samples_rejected_and_policy_bound(tmp_path):
    config = replace(Config(), evidence_mode="calibrated", evidence_file=str(tmp_path / "evidence.json"))
    data = {"model_version": MODEL_VERSION, "symbol": "SOLUSDT", "horizon_seconds": 60, "out_of_sample": True,
            "policy_sha256": config.policy_digest(), "cells": {}}
    Path(config.evidence_file).write_text(json.dumps(data))
    assert Evidence(config).gate("Buy", 0, 0)[0] is False
    with pytest.raises(ValueError, match="frozen"):
        Evidence(replace(config, stop_bps=D(40)))


@pytest.mark.asyncio
async def test_testnet_client_blocks_mainnet_and_readonly_mutation():
    client = object.__new__(TestnetClient)
    client.credentials = SimpleNamespace(testnet=True)
    client.base = "https://api.bybit.com"
    client.trading = True
    with pytest.raises(ExchangeError, match="testnet"):
        await client.request("POST", "/v5/order/create", {})
    client.base = "https://api-testnet.bybit.com"
    client.trading = False
    with pytest.raises(ExchangeError, match="disabled"):
        await client.request("POST", "/v5/order/create", {})


@pytest.fixture
def runner(tmp_path, inst):
    client = SimpleNamespace(server_clock_offset_ms=0, clock_rtt_ms=10,
                             now_ms=lambda: 100000, cancel=AsyncMock(return_value={}),
                             create=AsyncMock(return_value={"result": {"orderId": "ex1"}}))
    details = {"maker": D(".0002"), "taker": D(".00055"),
               "wallet": {"totalEquity": D(1000), "totalAvailableBalance": D(1000), "accountIMRate": D(0), "accountMMRate": D(0)},
               "ticker": {"nextFundingTime": "10000000", "markPrice": "100"}}
    r = Runner(Config(), client, inst, details, "testnet", 60, tmp_path)
    r.started = time.monotonic()
    yield r
    r.journal.close()
    r.risk_ledger.close()


def test_terminal_order_notification_before_execution_keeps_reservation(runner):
    order = Order("1", "Buy", "ENTRY", 10000, 2, 0, 60)
    runner.orders[order.client_id] = order
    runner.order_event({"symbol": "SOLUSDT", "orderLinkId": "1", "orderStatus": "Filled", "cumExecQty": "0.2"}, 3)
    assert order.leaves == 2
    row = {"symbol": "SOLUSDT", "orderLinkId": "1", "execId": "f1", "execQty": "0.2", "execPrice": "100",
           "execFee": "0", "execTime": "100000", "execType": "Trade", "isMaker": True, "side": "Buy"}
    runner.execution_event(row, 4)
    runner.execution_event(row, 5)
    assert order.leaves == 0
    assert runner.inventory.net == 2


@pytest.mark.asyncio
async def test_cancel_ack_does_not_release_reservation(runner):
    order = Order("1", "Buy", "ENTRY", 10000, 2, 0, 60, status="New")
    runner.orders[order.client_id] = order
    await runner.cancel(order)
    assert order.status == "PENDING_CANCEL" and order.leaves == 2


@pytest.mark.asyncio
async def test_submission_timeout_unknown_no_second_order(runner):
    runner.inventory.net = 1
    runner.inventory.target = 10010
    runner.client.create.side_effect = asyncio.TimeoutError()
    await runner.submit(role="EXIT", qty=1, price=10010)
    assert len(runner.orders) == 1
    order = next(iter(runner.orders.values()))
    assert order.status == "UNKNOWN" and order.leaves == 1
    assert await runner.submit(role="EXIT", qty=1, price=10010) is None
    assert runner.client.create.call_count == 1


@pytest.mark.asyncio
async def test_exit_reservations_never_exceed_confirmed_inventory(runner):
    runner.inventory.net = 2
    await runner.submit(role="EXIT", qty=5, price=10010)
    assert next(iter(runner.orders.values())).qty == 2
    assert await runner.submit(role="EXIT", qty=2, price=10010) is None


def test_native_stop_execution_detected_before_order_event(runner):
    runner.inventory.fill("entry", "Buy", 1, 10000, D(0), 1, deadline=60, target=10010, stop=9990)
    row = {"symbol": "SOLUSDT", "orderLinkId": "", "orderId": "stop1", "stopOrderType": "StopLoss",
           "execId": "f1", "execQty": "0.1", "execPrice": "99.90", "execFee": "0.01", "execTime": "100000",
           "execType": "Trade", "isMaker": False, "side": "Sell"}
    runner.execution_event(row, 4)
    assert runner.inventory.net == 0
    assert runner.fill_counts["NATIVE_STOP_TAKER"] == 1


def test_oldest_fill_timeout_not_reset_by_scalein(runner):
    now = time.monotonic()
    runner.market.book(book(), now)
    runner.inventory.fill("entry", "Buy", 1, 10000, D(0), now - 121, deadline=now + 20, target=10010, stop=9990)
    runner.inventory.fill("entry2", "Buy", 1, 10000, D(0), now - 1, deadline=now + 60, target=10011, stop=9990)
    assert runner.risk(now) == "hard_max_holding"


def eligible_market(inst, direction):
    m = Market(inst)
    if direction == "Buy":
        m.book(book(asks=[[str(D("100.05") + D(".01") * k), "0.1"] for k in range(16)]), 150)
    else:
        m.book(book(bids=[[str(D("99.95") - D(".01") * k), "0.1"] for k in range(16)],
                    asks=[["100", "10"], ["100.01", "10"]]), 150)
    m.episode = Episode(direction, 120)
    opposite = "Sell" if direction == "Buy" else "Buy"
    m.recoveries[opposite].extend([140, 145])
    m.rates[direction] = {10: 1000., 30: 1000., 120: 1000.}
    m.rates[opposite] = {10: .1, 30: .1, 120: .1}
    for k in range(18, 30):
        m.bins[k] = Bin(buy=1000 if direction == "Buy" else 1, sell=1000 if direction == "Sell" else 1,
                        consumed_ask=1000 if direction == "Buy" else 0,
                        consumed_bid=1000 if direction == "Sell" else 0, trades=10)
    return m


@pytest.mark.parametrize("direction", ["Buy", "Sell"])
def test_eligible_candidate_passes_full_price_quantity_cost_policy(inst, direction):
    m = eligible_market(inst, direction)
    strategy = Strategy(Config(), m, D(".0002"), D(".00055"))
    decision = strategy.decide(150, Inventory(), [], D(1000), D(1000), 10000000, 100000)
    assert decision.approved, decision.reason
    assert decision.direction == direction
    assert decision.qty == 1
    assert decision.evidence == "EXPERIMENTAL_TESTNET_UNVALIDATED"
    assert decision.service_needed <= decision.predicted_volume
    assert decision.deadline == 210
    if direction == "Buy":
        assert decision.target > decision.entry > decision.stop
    else:
        assert decision.target < decision.entry < decision.stop


def test_actual_fees_and_funding_can_veto_candidate(inst):
    m = eligible_market(inst, "Buy")
    strategy = Strategy(Config(), m, D(".01"), D(".01"))
    assert strategy.decide(150, Inventory(), [], D(1000), D(1000), 10000000, 100000).reason == "all_cost_edge"
    assert strategy.decide(150, Inventory(), [], D(1000), D(1000), 105000, 100000).reason == "funding_window"


def test_opposing_flow_plus_defensive_book_collapse_is_immediate(runner):
    now = time.monotonic()
    runner.market.book(book(bids=[["100", "0.1"]]), now)
    runner.market.bins[int(now // 5)] = Bin(sell=10, buy=1)
    runner.inventory.fill("entry", "Buy", 1, 10000, D(0), now - 1, deadline=now + 60, target=10010, stop=9990)
    assert runner.risk(now) == "opposing_flow_and_defense_collapse"


@pytest.mark.asyncio
async def test_original_target_already_executable_uses_reduce_only_ioc(runner):
    now = time.monotonic()
    runner.market.book(book(bids=[["100.10", "5"]], asks=[["100.11", "5"]]), now)
    runner.inventory.fill("entry", "Buy", 1, 10000, D(0), now - 1, deadline=now + 60, target=10005, stop=9990)
    await runner.maintain_exit(now, True)
    order = next(iter(runner.orders.values()))
    assert order.price == 10005
    assert order.role == "REDUCE"
    assert runner.client.create.call_args.kwargs["ioc"] is True


@pytest.mark.asyncio
async def test_cleanup_reconciles_partial_iocs_until_verified_flat(runner, monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr("vibetrading.liquidity_mm.runner.asyncio.sleep", no_sleep)
    now = time.monotonic()
    runner.inventory.fill("entry", "Buy", 2, 10000, D(0), now - 1, deadline=now + 60, target=10010, stop=9990)
    remote = {"net": 2, "fills": [], "creates": 0}

    async def positions(symbol=None):
        return [{"symbol": "SOLUSDT", "side": "Buy", "size": str(runner.instrument.qty(remote["net"])), "positionIdx": 0}]

    async def rows(path, payload):
        return remote["fills"]

    async def request(method, path, payload, public=False):
        if "orderbook" in path:
            return {"result": {"b": [["100", "10"]], "a": [["100.01", "10"]]}}
        raise AssertionError(path)

    async def create(symbol, instrument, order, ioc=False):
        assert ioc and order.role == "REDUCE" and order.side == "Sell"
        remote["creates"] += 1
        remote["net"] -= 1
        status = "Filled" if order.qty == 1 else "PartiallyFilledCanceled"
        row = {"symbol": "SOLUSDT", "orderLinkId": order.client_id, "execId": f"r{remote['creates']}",
               "execQty": "0.1", "execPrice": "99.95", "execFee": "0.005", "execTime": "100000",
               "execType": "Trade", "isMaker": False, "side": "Sell"}
        remote["fills"].append(row)
        # Private terminal notification before the fill is deliberately tested.
        for msg in ({"topic": "order", "data": [{"symbol": "SOLUSDT", "orderLinkId": order.client_id,
                            "orderStatus": status, "cumExecQty": "0.1"}]}, {"topic": "execution", "data": [row]}):
            runner.queue.put_nowait(("private", time.monotonic(), 100000, msg))
        return {"result": {"orderId": f"o{remote['creates']}"}}

    runner.client.open_orders = AsyncMock(return_value=[])
    runner.client.positions = positions
    runner.client.rows = rows
    runner.client.request = request
    runner.client.wallet = AsyncMock(return_value=runner.wallet)
    runner.client.create = create
    result = await runner.bounded_cleanup()
    assert result["verified_flat"] is True
    assert runner.inventory.net == remote["net"] == 0
    assert remote["creates"] == 2
    assert runner.fill_counts["REDUCE_TAKER"] == 2
    assert runner.inventory.fees == D(".01")


@pytest.mark.asyncio
async def test_unknown_order_blocks_cleanup_replacement(runner, monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr("vibetrading.liquidity_mm.runner.asyncio.sleep", no_sleep)
    runner.inventory.net = 1
    runner.orders["unknown"] = Order("unknown", "Sell", "REDUCE", 9990, 1, 0, 60, status="UNKNOWN")
    runner.reconcile = AsyncMock(return_value={"owned_open_orders": 0, "unknown_orders": 1,
        "open_orders": 0, "exchange_net_lots": 1, "ledger_net_lots": 1})
    result = await runner.bounded_cleanup()
    assert result["verified_flat"] is False
    runner.client.create.assert_not_called()


@pytest.mark.asyncio
async def test_successful_risk_reduction_enters_cooldown_and_continues(runner):
    runner.market.episode = Episode("Buy", time.monotonic() - 30)
    runner.bounded_cleanup = AsyncMock(return_value={"verified_flat": True})
    await runner.reduce_cycle("normal_exit_deadline")
    assert runner.state == "COOLDOWN"
    assert runner.market.episode is None
    assert not runner.stop_requested
    assert runner.cooldown_until > time.monotonic()


def test_calibrated_survival_is_frozen_and_fees_are_bound(tmp_path, inst):
    path = tmp_path / "evidence.json"
    config = replace(Config(), evidence_mode="calibrated", evidence_file=str(path))
    data = {"model_version": MODEL_VERSION, "symbol": "SOLUSDT", "horizon_seconds": 60,
            "out_of_sample": True, "policy_sha256": config.policy_digest(), "maker_fee_rate": ".0002",
            "taker_fee_rate": ".00055", "cells": {"Buy:0:0": {"episodes": 200, "block_bootstrap": True,
                "net_pnl_lower_95": .1, "normal_exit_lower_95": .8, "break_even_probability": .6}},
            "survival": {"risk": {s: [100] * 5 for s in ("Buy", "Sell")},
                         "ends": {s: [5] * 5 for s in ("Buy", "Sell")}}}
    path.write_text(json.dumps(data))
    market = Market(inst)
    strategy = Strategy(config, market, D(".0002"), D(".00055"))
    assert strategy.evidence.gate("Buy", 0, 0) == (True, "calibrated")
    market.survival.observe("Buy", 0, True)
    assert market.survival.ends["Buy"][0] == 5
    with pytest.raises(ValueError, match="fees"):
        Strategy(config, Market(inst), D(".0003"), D(".00055"))
