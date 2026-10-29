"""LIVE authorization remains closed by default and is scope/expiry checked."""
from types import SimpleNamespace

import pytest

from execution.trading_contracts import LiveExecutionAuthorization
from execution.trading_contracts import ActionKind
from runtime.runtime_control import GateBlocked
from cap05_source_fixture import NOW, build_native_group


def authorization(owner, *, strategy="cap05-alpha", config="cfg-cap05", expiry=NOW + 10):
    return LiveExecutionAuthorization(strategy, config, owner.ledger.scope.key,
        owner.ledger.native_account_id, "scripted-human-approval", NOW - 1, expiry)


def test_live_activation_requires_exact_expiring_injected_authorization(tmp_path):
    fixture = build_native_group(tmp_path)
    owner = fixture["binance_owner"]
    action = SimpleNamespace(strategy_id="cap05-alpha", config_version="cfg-cap05")
    route = SimpleNamespace(account_scope=owner.ledger.scope,
        native_account_id=owner.ledger.native_account_id)
    try:
        with pytest.raises(GateBlocked, match="authorization"):
            owner._check_live_activation(action, route)
        owner.live_authorization = authorization(owner)
        owner.live_activation_guard = lambda: True
        owner._check_live_activation(action, route)
        owner.live_activation_guard = lambda: False
        cancel = SimpleNamespace(strategy_id="cap05-alpha", config_version="cfg-cap05",
            action=ActionKind.CANCEL)
        owner._check_live_activation(cancel, route)
        owner.live_activation_guard = lambda: True
        owner.live_authorization = authorization(owner, strategy="foreign")
        with pytest.raises(GateBlocked, match="authorization"):
            owner._check_live_activation(action, route)
        owner.live_authorization = authorization(owner, expiry=NOW)
        with pytest.raises(GateBlocked, match="authorization"):
            owner._check_live_activation(action, route)
        owner.live_authorization = authorization(owner)
        owner.live_activation_guard = lambda: False
        with pytest.raises(GateBlocked, match="proof"):
            owner._check_live_activation(action, route)
    finally:
        # Fixture owner is sandbox-only and no activation guard is installed by default.
        owner.live_authorization = None
        owner.live_activation_guard = None
        import asyncio
        asyncio.run(owner.close())
        asyncio.run(fixture["bybit_owner"].close())


@pytest.mark.parametrize("values", [
    ("s", "c", "scope", "native", "ref", True, 2),
    ("s", "c", "scope", "native", "ref", 2, 2),
    ("s", "c", "scope", "native", "ref", 3, 2),
])
def test_live_authorization_rejects_bool_and_invalid_time_window(values):
    with pytest.raises(ValueError):
        LiveExecutionAuthorization(*values)
