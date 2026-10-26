"""TX04 joint gate for the existing continuous Paper path.

Market input goes through the real LocalMarketEngine.on_frame -> shared HotPath
-> PaperStrategyRuntime.process_applied -> PaperExecution/PortfolioLedger path.
The companion TX04B regression and the coupled-checkpoint integration suite are
run alongside this gate; a bare ledger/matcher reopen is deliberately not
treated as verified plugin-state or continuous-engine-cursor recovery.
"""
import asyncio
from dataclasses import replace
from decimal import Decimal as D

import pytest

from test_strategy_runtime import (T, BTC, book, deliver, ledger_at, matcher_for,
                                   registration, runtime, trade)
from vibetrading.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.local_engine import EngineBudgets, LocalMarketEngine
from core.models import Side

PRODUCER = "btc-source"
INCARNATION = "verified-virgin"
TICK_SECONDS = .01


def engine_budgets():
    # Existing bounded engine period: one explicit 10 ms watchdog tick.
    return EngineBudgets(1, 8, 8192, 16, 65536, 10_000_000_000, 8, 2,
                         20_000_000_000, TICK_SECONDS)


async def wait_until(predicate, *, timeout=3):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), timeout=timeout)


def quantity_two(reg):
    return replace(reg, config={"quantity": "2"})


@pytest.mark.asyncio
async def test_joint_existing_continuous_paper_paths_and_bare_restart_limits(tmp_path):
    observed, now, monotonic = [], [T], [0]
    ledgers = [ledger_at(tmp_path)]
    ledger = ledgers[0]
    engine = None
    try:
        ledger.seed_balance("USDT", D(100000), {"source": "synthetic"})
        matcher = matcher_for(ledger)
        host = runtime(ledger, matcher, now)
        alpha = quantity_two(registration(ledger, "alpha", observed, side=Side.BUY))
        beta = quantity_two(registration(ledger, "beta", observed, side=Side.SELL))
        host.register(alpha)
        host.register(beta)
        product = host.products[BTC.key]
        assert (product.maker_fee_rate, product.taker_fee_rate, product.multiplier) == (
            D(".001"), D(".002"), D(10))

        # Explicit verified virgin construction, not real startup authority.
        assert host.hot_path.apply_seq == 0
        engine = LocalMarketEngine(producer_providers={PRODUCER: "scripted"},
            budgets=engine_budgets(), on_invalidate=lambda *args: None,
            hot_path=host.hot_path, post_apply_callbacks=(host.process_applied,),
            on_tick=host.advance, snapshot_clock=lambda: now[0],
            clock=lambda: monotonic[0], fresh_starts={PRODUCER: INCARNATION},
            verified_fresh_start=True)
        assert engine.on_tick == host.advance
        assert engine.budgets.tick_seconds == TICK_SECONDS
        assert engine._checkpoint_origin_verified is True
        assert engine.stats["feeds"][PRODUCER]["mode"] == "LIVE"
        assert engine.stats["feeds"][PRODUCER]["applied_sequence"] == 0
        assert engine.stats["applied"] == 0 and host.hot_path.apply_seq == 0

        def send(sequence, event):
            now[0] = event.receive_ts_ns
            monotonic[0] += 1
            return engine.on_frame(RecoveryFrame(
                EngineCursor(PRODUCER, INCARNATION, sequence),
                event.model_dump_json().encode()))

        send(1, book())
        assert host.hot_path.apply_seq == 1
        assert [sid for sid, _ in observed] == ["alpha", "beta"]
        open_orders = matcher.report()["open_orders"]
        alpha_id = next(identity for identity, order in open_orders.items()
                        if order["strategy"] == "alpha")
        beta_id = next(identity for identity, order in open_orders.items()
                       if order["strategy"] == "beta")

        sell_trade = trade(T + 11, side=Side.SELL, price=99).model_copy(
            update={"quantity": D(6)})
        send(2, sell_trade)
        assert D(ledger.get(alpha_id)["filled"]) == D(1)
        assert D(ledger.get(beta_id)["filled"]) == D(0)
        assert matcher.report()["fills"] == 1

        buy_trade = trade(T + 12, side=Side.BUY, price=101).model_copy(
            update={"quantity": D(6)})
        send(3, buy_trade)
        expected = ledger.report()
        assert D(expected["balances"]["USDT"]) == D("100018")
        assert D(expected["reserved"]["USDT"]) == D("204")
        assert D(expected["simulated_margin"]) == D("200")
        assert D(expected["virtual_positions"]["alpha"][BTC.key]) == D(1)
        assert D(expected["virtual_positions"]["beta"][BTC.key]) == D(-1)
        assert D(expected["physical_positions"][BTC.key]) == 0
        assert D(ledger.get(alpha_id)["reservation"]) == D("100.98")
        assert D(ledger.get(beta_id)["reservation"]) == D("103.02")
        assert matcher.report()["fills"] == 2
        assert host.hot_path.apply_seq == 3 and len(observed) == 2

        # Duplicate transport cursor/payload is ignored before the hot path.
        engine.on_frame(RecoveryFrame(EngineCursor(PRODUCER, INCARNATION, 3),
                                      buy_trade.model_dump_json().encode()))
        assert engine.stats["duplicates"] == 1
        assert host.hot_path.apply_seq == 3 and matcher.report()["fills"] == 2
        # A repeated market event under a new transport cursor is still a hot-path
        # duplicate: no callback, matcher state, receipt, clock, or cash movement.
        send(4, buy_trade)
        assert host.hot_path.apply_seq == 3
        assert len(observed) == 2 and matcher.report()["fills"] == 2
        assert ledger.report() == expected

        # Cancel is requested at T+13; only the configured watchdog at T+18 may
        # make it effective. No new market event is delivered for this step.
        host.pause("alpha", T + 13)
        assert ledger.get(alpha_id)["state"] == "CANCEL_PENDING"
        assert D(ledger.get(alpha_id)["reservation"]) == D("100.98")
        applied_before_tick = engine.stats["applied"]
        now[0] = T + 18
        await engine.start()
        await wait_until(lambda: ledger.get(alpha_id)["state"] == "CANCELED")
        assert engine.stats["applied"] == applied_before_tick
        assert host.hot_path.apply_seq == 3
        assert matcher.report()["clock_ns"] == T + 18
        assert ledger.get(alpha_id)["state"] == "CANCELED"
        assert D(ledger.get(alpha_id)["reservation"]) == 0
        assert alpha_id not in matcher.report()["open_orders"]
        assert ledger.get(beta_id)["state"] == "OPEN"
        assert D(ledger.get(beta_id)["reservation"]) == D("103.02")
        assert matcher.report()["open_orders"][beta_id]["leaves"] == "1"
        assert D(ledger.report()["reserved"]["USDT"]) == D("103.02")
        # The injected monotonic clock stayed inside the live feed lease.
        assert engine.stats["feeds"][PRODUCER]["mode"] == "LIVE"
        assert not engine._watchdog.done()

        await engine.close()
        assert engine._watchdog.done()
        engine = None
        before_restart = ledger.report()
        assert D(before_restart["balances"]["USDT"]) == D("100018")
        assert D(before_restart["simulated_margin"]) == D("200")
        assert D(before_restart["reserved"]["USDT"]) == D("103.02")
        ledger.close()

        # A bare latest-ledger reopen restores matcher facts only. Active beta is
        # conservatively UNKNOWN until a verified coupled checkpoint restores the
        # strategy/engine cursor; positions and reservation remain unchanged.
        ledgers.append(ledger_at(tmp_path))
        ledger = ledgers[-1]
        matcher = matcher_for(ledger)
        beta_row = ledger.get(beta_id)
        assert beta_row["state"] == "UNKNOWN"
        assert D(beta_row["reservation"]) == D("103.02")
        assert matcher.report()["open_orders"][beta_id]["leaves"] == "1"
        assert alpha_id not in matcher.report()["open_orders"]
        assert ledger.get(alpha_id)["state"] == "CANCELED"
        assert D(ledger.get(alpha_id)["reservation"]) == 0
        restored = ledger.report()
        assert D(restored["balances"]["USDT"]) == D("100018")
        assert D(restored["reserved"]["USDT"]) == D("103.02")
        assert D(restored["simulated_margin"]) == D("200")
        assert restored["virtual_positions"] == before_restart["virtual_positions"]
        assert restored["physical_positions"] == before_restart["physical_positions"]

        # Existing replay phase may warm callbacks but must not submit actions or
        # mutate restored matcher ledger balances/receipts/clock.
        replay_observed = []
        before_matcher = matcher.report()
        before_ledger = ledger.report()
        replay = runtime(ledger, matcher, now, phase="replay")
        replay.register(quantity_two(registration(ledger, "alpha", replay_observed,
                                                   side=Side.BUY)))
        replay.register(quantity_two(registration(ledger, "beta", replay_observed,
                                                  side=Side.SELL)))
        deliver(replay, now, book(T + 21, sequence=5))
        deliver(replay, now, trade(T + 22, side=Side.SELL, price=99))
        replay.advance(T + 30)
        assert matcher.report() == before_matcher
        assert ledger.report() == before_ledger
        assert len(replay_observed) == 2
        for sid in ("alpha", "beta"):
            strategy = replay.report()["strategies"][sid]
            assert strategy["state"] == "REPLAY"
            assert strategy["callbacks"] == 1
            assert strategy["actions"] == 0
        assert replay.report()["input_phase"] == "REPLAY"
        evidence = replay.report()["restart_evidence"]
        assert evidence["strategy_state_recovery"] == "UNVERIFIED"
        assert evidence["continuous_input_cursor"] == "MISSING_EVIDENCE"
        assert evidence["full_restart_gate"] == "BLOCKED"
    finally:
        if engine is not None:
            await engine.close()
        for opened in ledgers:
            opened.close()
