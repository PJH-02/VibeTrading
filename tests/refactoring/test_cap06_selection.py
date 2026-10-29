"""Strict configuration-only tests for selected strategy routing."""
from copy import deepcopy

import pytest

from runtime.selected_execution import parse_strategy_selections


def selection(mode="TESTNET", environment="sandbox"):
    return {"mode": mode, "venue": "binance", "account": "acct-a",
        "product": "linear_perpetual", "environment": environment,
        "queue": {"max_items": 4, "max_bytes": 262144, "max_age_ns": 1_000_000_000,
            "snapshot_charge_bytes": 65536},
        "registration": {"job_id": "cap06-job", "module": "strategies.cap06_source_fixture",
            "strategy_version": "source-v1", "config_version": "cfg-cap05",
            "config": {"config_version": "cfg-cap05"},
            "research_receipt": {}, "paper_permission": False,
            "required_feeds": ["feed"], "warmup_bars": 0,
            "accounts": ["binance:acct-a:linear_perpetual:sandbox"],
            "risk": {"binance:acct-a:linear_perpetual:sandbox": {
                "funding_buffer": "0", "account_notional_limit": "1000",
                "strategy_notional_limit": "1000", "account_capital_limit": "1000",
                "strategy_capital_limit": "1000", "margin_rate": "0.1"}}}}


def test_selection_parser_preserves_exact_native_mode_and_scope():
    parsed = parse_strategy_selections([selection()])
    assert len(parsed) == 1
    item = parsed[0]
    assert (item.mode, item.environment, item.account, item.registration["config_version"]) == (
        "TESTNET", "sandbox", "acct-a", "cfg-cap05")
    with pytest.raises(TypeError):
        item.registration["config_version"] = "changed"


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(mode="MOCK"),
    lambda row: row.update(environment="live"),
    lambda row: row.update(extra=True),
    lambda row: row["queue"].update(max_items=True),
    lambda row: row["queue"].update(max_bytes=2**40),
    lambda row: row["queue"].update(snapshot_charge_bytes=2**20),
    lambda row: row["registration"].update(module="../../not-a-strategy"),
    lambda row: row["registration"].update(extra=True),
])
def test_selection_parser_rejects_unknown_tokens_and_unbounded_fields(mutate):
    bad = selection()
    mutate(bad)
    with pytest.raises((TypeError, ValueError)):
        parse_strategy_selections([bad])


def test_duplicate_strategy_job_and_duplicate_document_modes_reject():
    one = selection()
    two = deepcopy(one)
    with pytest.raises(ValueError, match="duplicate"):
        parse_strategy_selections([one, two])
    paper = selection("PAPER", "paper")
    assert parse_strategy_selections([paper])[0].mode == "PAPER"
