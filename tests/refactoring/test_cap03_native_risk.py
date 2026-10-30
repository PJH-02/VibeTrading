"""Credential-free fixtures for the read-only native account fact gate."""
from decimal import Decimal

import pytest

from execution.perp_execution import PerpScope
from execution.trading_contracts import validate_native_account_facts


NOW = 1_800_000_000_000_000_000
SCOPE = PerpScope("binance", "linear_perpetual", "sandbox", "local-alias", "USDT")


def product_row(scope=SCOPE, *, symbol="BTCUSDT", **changes):
    row = {
        "scope_key": scope.key, "venue": scope.venue, "product": scope.product,
        "environment": scope.environment, "symbol": symbol, "kind": "linear_perpetual",
        "multiplier": "1", "tick_size": "0.1", "lot_size": "0.001",
        "min_quantity": "0.001", "min_notional": "5", "base_currency": "BTC",
        "quote_currency": "USDT", "position_mode": None, "margin_mode": None,
        "settlement_currency": "USDT", "collateral_currency": "USDT",
        "raw": {"symbol": symbol, "contractType": "PERPETUAL"},
    }
    row.update(changes)
    return row


def account_facts(scope=SCOPE, **changes):
    row = {
        "scope_key": scope.key, "venue": scope.venue, "product": scope.product,
        "environment": scope.environment, "equity": "1000", "available": "700",
        "initial_margin": "200", "maintenance_margin": "100", "currency": "USDT",
        "raw": {"account": "native-account-fixture"},
    }
    envelope = {
        "normalized": row, "observed_ns": NOW - 10, "account_version": "v7",
        "reconciliation_state": "RECONCILED", "unknown": False,
        "position_mode": "net", "margin_mode": "cross",
        "native_identity": {
            "scope_key": scope.key, "native_account_id": "native-account-fixture",
            "authenticated_session_id": "session-fixture",
            "source": "authenticated_native_session",
            "permissions": ("account_read", "trade"),
        },
    }
    envelope.update(changes)
    return envelope


def validate(facts=None, products=None, scope=SCOPE, **kwargs):
    return validate_native_account_facts(
        facts if facts is not None else account_facts(scope),
        products if products is not None else {"BTCUSDT": product_row(scope)},
        scope, account_version="v7", expected_native_account_id="native-account-fixture",
        now_ns=NOW, max_age_ns=100,
        **kwargs,
    )


def test_explicit_reconciled_native_facts_validate_and_are_immutable():
    result = validate()
    assert result["quality"] == "VALID"
    assert result["risk_ready"] is True
    assert result["basis"] == "NATIVE_REPORTED_FACTS"
    assert result["facts"]["margin_mode"] == "cross"
    assert result["facts"]["reported"] == {
        "equity": Decimal("1000"), "available": Decimal("700"),
        "initial_margin": Decimal("200"), "maintenance_margin": Decimal("100"),
    }
    with pytest.raises(TypeError):
        result["facts"]["reported"]["equity"] = Decimal("0")
    with pytest.raises(TypeError):
        result["facts"]["products"]["BTCUSDT"]["tick_size"] = Decimal("1")


@pytest.mark.parametrize(("change", "reason"), [
    ({"account_version": "v6"}, "version mismatch"),
    ({"observed_ns": NOW + 1}, "future"),
    ({"observed_ns": NOW - 101}, "stale"),
    ({"reconciliation_state": "GAP"}, "unreconciled"),
    ({"unknown": True}, "unreconciled"),
    ({"position_mode": "hedge"}, "position mode"),
    ({"margin_mode": "portfolio"}, "margin mode"),
    ({"native_identity": {"account_identity_verified": True}}, "identity/session"),
])
def test_missing_stale_ambiguous_or_unsupported_native_envelope_fails_closed(change, reason):
    assert validate(account_facts(**change))["reason"].find(reason) >= 0


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("available", "1001", "exceeds"),
    ("initial_margin", "-1", "nonnegative"),
    ("maintenance_margin", True, "exact decimal"),
    ("equity", 1000.0, "exact decimal"),
    ("currency", "USD", "currency"),
    ("settlement_currency", "USD", "currency"),
    ("initial_margin", None, "unavailable"),
])
def test_unsafe_or_non_native_account_values_are_unavailable(field, value, reason):
    facts = account_facts()
    facts["normalized"][field] = value
    assert reason in validate(facts)["reason"]


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("quote_currency", "USD", "cross-currency"),
    ("settlement_currency", "USDC", "cross-currency"),
    ("collateral_currency", "USDG", "cross-currency"),
    ("kind", "inverse_perpetual", "scope/kind"),
    ("base_currency", "", "base currency"),
    ("tick_size", "0", "positive"),
    ("lot_size", 0.001, "exact decimal"),
])
def test_product_metadata_requires_supported_native_exact_contract(field, value, reason):
    assert reason in validate(products={"BTCUSDT": product_row(**{field: value})})["reason"]


def test_scope_binding_and_native_permissions_are_exact():
    bad_facts = account_facts()
    bad_facts["normalized"]["scope_key"] = "other-scope"
    assert "scope mismatch" in validate(bad_facts)["reason"]
    bad_facts = account_facts()
    bad_facts["native_identity"]["permissions"] = ("account_read",)
    assert "permission provenance" in validate(bad_facts)["reason"]
    bad_facts = account_facts()
    bad_facts["native_identity"]["native_account_id"] = "different-account"
    assert "independently bound physical account" in validate(bad_facts)["reason"]
    bad_products = {"BTCUSDT": product_row()}
    bad_products["BTCUSDT"]["scope_key"] = "other-scope"
    assert "scope/kind mismatch" in validate(products=bad_products)["reason"]


@pytest.mark.parametrize("field", ["account_address", "native_account_id"])
def test_known_normalized_native_identity_must_match_independent_expected_identity(field):
    facts = account_facts()
    facts["normalized"][field] = "0x222"
    assert "normalized native account identity" in validate(facts)["reason"]


def test_empty_raw_mappings_are_not_native_source_evidence():
    facts = account_facts()
    facts["normalized"]["raw"] = {}
    assert "raw account source evidence" in validate(facts)["reason"]
    products = {"BTCUSDT": product_row(raw={})}
    assert "product source metadata" in validate(products=products)["reason"]


@pytest.mark.parametrize(("field", "value"), [
    ("position_mode", "hedge"),
    ("margin_mode", "isolated"),
])
def test_explicit_product_modes_must_match_authenticated_account_mode(field, value):
    assert "contradicts authenticated account mode" in validate(
        products={"BTCUSDT": product_row(**{field: value})}
    )["reason"]


def test_existing_adapter_rows_without_observation_identity_or_mode_provenance_are_unavailable():
    # Binance account_state currently supplies normalized balances/raw only;
    # the adapters do not assert an observation/version/reconciliation or
    # complete permission/identity envelope.  This must not be inferred here.
    normalized = {
        "scope_key": SCOPE.key, "venue": "binance", "product": "linear_perpetual",
        "environment": "sandbox", "equity": "1000", "available": "700",
        "initial_margin": "200", "maintenance_margin": "100", "currency": "USDT",
        "raw": {"assets": []},
    }
    assert "observation timestamp" in validate({"normalized": normalized})["reason"]


def test_scope_must_be_selected_linear_perp_and_currency_cannot_be_inferred_from_product_label():
    spot = PerpScope("binance", "linear_perpetual", "sandbox", "local-alias", "USDT")
    # A selected venue/product scope can only pass when its exact scope key is
    # carried through. A product label alone never binds an account or currency.
    assert validate(scope=spot)["quality"] == "VALID"
    unsupported = PerpScope("bybit", "linear_perpetual", "sandbox", "another-alias", "USDC")
    assert "currency" in validate(scope=unsupported)["reason"]
