from decimal import Decimal

from vibetrading.timefolio import TimefolioOrderIntent


def test_timefolio_intent_defaults_to_no_submit() -> None:
    intent = TimefolioOrderIntent(
        account_id="contest",
        ticker="005930",
        side="buy",
        weight_pct=Decimal("1.5"),
    )
    assert intent.submit is False
    assert intent.opp_tick == 1
