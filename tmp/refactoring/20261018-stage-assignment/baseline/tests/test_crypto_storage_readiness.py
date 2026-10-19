import asyncio
import json
import time
from types import SimpleNamespace as NS

import pytest

from vibetrading.market_policy import CollectionPaused, MarketDataPolicyBus, _CryptoPressureLatch, crypto_storage_blocker


def settings(tmp_path):
    return NS(data_root=tmp_path, env={'CRYPTO_REQUIRE_STORAGE_READY': 'true'},
              storage_guard=NS(resume_stable_seconds=0))


def write_health(tmp_path, **overrides):
    path = tmp_path / 'crypto_state/health/crypto-storage.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'state': 'running', 'ready': True, 'ts_ns': time.time_ns(), **overrides}))


def test_missing_initializing_and_stale_storage_fail_closed(tmp_path):
    config = settings(tmp_path)
    assert crypto_storage_blocker(config) == 'storage_health_unavailable'
    write_health(tmp_path, state='initializing', ready=False)
    assert crypto_storage_blocker(config) == 'storage_initializing'
    write_health(tmp_path, ts_ns=time.time_ns()-121*10**9)
    assert crypto_storage_blocker(config) == 'storage_heartbeat_stale'
    write_health(tmp_path)
    assert crypto_storage_blocker(config) is None


def test_crypto_waits_for_index_warmup_and_market_does_not(tmp_path):
    policy = MarketDataPolicyBus(NS())
    policy._guard = NS(settings=settings(tmp_path), evaluate=lambda: NS(free_bytes=100, reserve_bytes=4, resume_margin_bytes=1))
    policy._stream_probe = lambda crypto=False: asyncio.sleep(0, result={
        'status': 'fresh', 'ratio': .1, 'config_mismatch': False, 'consecutive_failures': 0})

    async def exercise():
        _CryptoPressureLatch.paused = False
        _CryptoPressureLatch.resume_eligible_since = None
        try:
            write_health(tmp_path, state='initializing', ready=False)
            with pytest.raises(CollectionPaused, match='storage_initializing'):
                await policy._check_pressure(crypto=True)
            # Market has its own latch and never waits on crypto readiness.
            policy._next_pressure_check = 0
            await policy._check_pressure(crypto=False)
            write_health(tmp_path)
            with pytest.raises(CollectionPaused):
                await policy._check_pressure(crypto=True)
            await policy._check_pressure(crypto=True)
            assert not _CryptoPressureLatch.paused
        finally:
            _CryptoPressureLatch.paused = False
            _CryptoPressureLatch.resume_eligible_since = None

    asyncio.run(exercise())
