from decimal import Decimal, localcontext

from operations.dashboard.pnl import paper_pnl_projection


def _pnl_document(*rows):
    return {
        "accounts": [],
        "pnl": list(rows),
        "positions": [],
        "orders": [],
        "fills": [],
    }


def _row(**overrides):
    result = {
        "strategy": "alpha",
        "account": "scope-a",
        "currency": "USD",
        "mode": "paper",
        "realized_pnl": "12345678901234567890.123456789",
        "fees": "0.000000000000000007",
        "funding": "-0.000000000000000003",
        "recovery": "UNVERIFIED",
    }
    result.update(overrides)
    return result


def test_paper_projection_keeps_exact_fee_and_funding_at_low_ambient_precision():
    with localcontext() as context:
        context.prec = 3
        rows = paper_pnl_projection(_pnl_document(_row()))

    assert len(rows) == 1
    row = rows[0]
    assert row["realized_pnl"] == "12345678901234567890.123456789"
    assert row["fees"] == "7E-18"
    assert row["funding"] == "-3E-18"
    assert row["net_pnl"] == "12345678901234567890.123456788999999990"
    with localcontext() as context:
        context.prec = 128
        assert Decimal(row["net_pnl"]) == Decimal(row["realized_pnl"]) - Decimal(row["fees"]) + Decimal(row["funding"])


def test_paper_projection_replay_duplicate_and_invalid_decimal_fail_closed():
    duplicate = _row()
    rows = paper_pnl_projection(_pnl_document(_row(), duplicate))
    assert len(rows) == 1
    assert rows[0]["net_pnl"] == "12345678901234567890.123456788999999990"

    invalid = paper_pnl_projection(_pnl_document(_row(fees="NaN")))
    assert len(invalid) == 1
    assert invalid[0]["fees"] is None
    assert invalid[0]["net_pnl"] is None
