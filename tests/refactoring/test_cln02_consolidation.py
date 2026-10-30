"""CLN-02 canonical KIS owner consolidation; fixtures remain offline."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import execution.brokers as brokers
from execution.kis_execution import KisCashBroker
from execution.venues import registry as venue_registry


def _settings(mock_value=None):
    env = {} if mock_value is None else {"KIS_USE_MOCK": mock_value}
    return SimpleNamespace(env=env)


def _capture_constructor(monkeypatch):
    calls = []

    class Canonical:
        def __init__(self, settings, *, mode):
            calls.append((settings, mode))

    monkeypatch.setattr(brokers, "KisCashBroker", Canonical)
    return calls


@pytest.mark.parametrize("mock_value,expected_mode", [
    (None, "mock"),
    ("true", "mock"),
    ("false", "real"),
])
def test_kis_factory_preserves_mode_default_and_explicit_settings(monkeypatch, mock_value, expected_mode):
    calls = _capture_constructor(monkeypatch)
    settings = _settings(mock_value)

    result = brokers.broker_for_name(" KIS ", settings)

    assert isinstance(result, brokers.KisCashBroker)
    assert calls == [(settings, expected_mode)]


def test_kis_factory_uses_get_settings_when_settings_are_absent(monkeypatch):
    calls = _capture_constructor(monkeypatch)
    settings = _settings(None)
    monkeypatch.setattr(brokers, "get_settings", lambda: settings)

    result = brokers.broker_for_name("kis")

    assert isinstance(result, brokers.KisCashBroker)
    assert calls == [(settings, "mock")]


def test_kis_factory_rejects_unsupported_names_without_constructing(monkeypatch):
    calls = _capture_constructor(monkeypatch)

    with pytest.raises(ValueError, match="Unsupported broker"):
        brokers.broker_for_name("not-a-broker", _settings(None))

    assert calls == []


def test_registry_has_one_canonical_kis_mapping_identity():
    assert not hasattr(brokers, "KisBrokerAdapter")
    assert "execution.brokers.KisBrokerAdapter" not in venue_registry.EXISTING_ADAPTER_MAPS
    assert [
        owner for owner in venue_registry.EXISTING_ADAPTER_MAPS
        if owner.endswith("KisCashBroker")
    ] == ["execution.kis_execution.KisCashBroker"]
    assert "execution.brokers.KisBrokerAdapter" not in venue_registry._CASH_INTENT_OWNERS
    assert "execution.kis_execution.KisCashBroker" in venue_registry._CASH_INTENT_OWNERS

    owner, operations, gaps = venue_registry.existing_adapter_contract(
        KisCashBroker.__new__(KisCashBroker)
    )

    assert owner == "execution.kis_execution.KisCashBroker"
    assert "submit" in operations and "cancel" in operations
    assert gaps
    assert any("unsupported" in gap for gap in gaps)


def test_registry_rejects_the_retired_wrapper_identity():
    class RetiredKisBrokerAdapter:
        pass

    RetiredKisBrokerAdapter.__module__ = "execution.brokers"
    RetiredKisBrokerAdapter.__qualname__ = "KisBrokerAdapter"

    with pytest.raises(ValueError, match="No proven existing adapter mapping"):
        venue_registry.existing_adapter_contract(RetiredKisBrokerAdapter())

    with pytest.raises(ValueError, match="No proven existing adapter mapping"):
        venue_registry.VenueRegistry(max_routes=1, max_aliases=1, max_fact_bytes=1024).bind_existing_adapter(
            venue_registry.RouteSpec(
                "retired-kis", "kis", "krx_cash", "cash", "mock",
                venue_registry.AccountScope("kis", "fixture", "cash", "mock"),
            ),
            RetiredKisBrokerAdapter(),
            reference_provider=lambda: {},
            capabilities=venue_registry.Capabilities({"cash"}, {"mock"}, {"GTC"}, False, False, False),
        )


@pytest.mark.parametrize("mode,prefix", [
    ("mock", "VTTC"),
    ("mock2", "VTTC"),
    ("real", "TTTC"),
])
def test_canonical_kis_tr_ids_are_unchanged(mode, prefix):
    broker = KisCashBroker.__new__(KisCashBroker)
    broker.mode = mode

    for suffix in ("8434R", "8908R", "0012U", "0011U", "0013U", "0081R"):
        assert broker.tr(suffix) == prefix + suffix


def test_retired_s1_s4_are_archive_only_byte_exact_and_research_state_imports_without_archive():
    import hashlib
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tempfile

    repo_root = Path(__file__).resolve().parents[2]
    archive = repo_root / "docs/archive/CLN-02-legacy-oracles/fair_value"
    expected = {
        "s1_net_liquidity.py": "c7ee51d6cd395d8bc984ffbe037bfac171d5234c17cf4c3ca6a37090bc96d7cb",
        "s2_external_anchor.py": "f3d3be3cd2a5b52cfc37d1e9eb8f5a55053dc108c5db50c3512745207faa9f2e",
        "s3_aggressive_flow.py": "8077305484e14fa2df7cf8c55f51550a3f6278ec94c6501c48ef7b14c13f70a4",
        "s4_obi.py": "a25d36bfc22f87838d5e34f06d14a6c007ea2dcf7b35cb1038b80a41c2c7b919",
    }
    for name, baseline_sha256 in expected.items():
        archived = archive / name
        assert archived.is_file()
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == baseline_sha256
        assert not (repo_root / "src/strategies/legacy/fair_value" / name).exists()

    src = repo_root / "src"
    script = """
import importlib.util
import sys
sys.path.insert(0, sys.argv[1])
for module in (
    'strategies.legacy.fair_value.s1_net_liquidity',
    'strategies.legacy.fair_value.s2_external_anchor',
    'strategies.legacy.fair_value.s3_aggressive_flow',
    'strategies.legacy.fair_value.s4_obi',
):
    assert importlib.util.find_spec(module) is None, module
import strategies.research.state
assert strategies.research.state.__file__.endswith('strategies/research/state/__init__.py')
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    with tempfile.TemporaryDirectory() as temporary_directory:
        subprocess.run(
            [sys.executable, "-c", script, str(src)],
            cwd=temporary_directory,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
