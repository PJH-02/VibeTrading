from types import SimpleNamespace
from unittest.mock import patch
from vibetrading.runtime import _maintenance_lock_retry_delay

def test_lock_loser_can_retry_after_winner_releases_within_the_same_minute():
    # Market holds a shared lock for 4s. Crypto's first attempt loses at t=0.
    # A 300s cooldown misses every short quiet window during a busy cycle.
    settings=SimpleNamespace(env={})
    with patch('vibetrading.runtime.random.uniform',return_value=1.0):
        retry=_maintenance_lock_retry_delay(settings,{'ran':False,'reason':'heavy_maintenance_busy'},300)
    assert 4<retry<30

def test_resource_pressure_and_read_budget_deferrals_keep_their_cooldown():
    settings=SimpleNamespace(env={'ARCHIVE_LOCK_RETRY_SECONDS':'1'})
    for reason in ('storage_or_io_busy','storage_health_unavailable','maintenance_read_time_budget','storage_pending_above_prune_limit'):
        assert _maintenance_lock_retry_delay(settings,{'ran':False,'deferred':True,'reason':reason},300)==300

def test_retry_jitter_is_bounded_and_breaks_identical_worker_schedules():
    settings=SimpleNamespace(env={'ARCHIVE_LOCK_RETRY_SECONDS':'10'})
    result={'ran':False,'reason':'heavy_maintenance_busy'}
    with patch('vibetrading.runtime.random.uniform',side_effect=[0.7,1.3]):
        assert _maintenance_lock_retry_delay(settings,result,300)!=_maintenance_lock_retry_delay(settings,result,300)
    with patch('vibetrading.runtime.random.uniform',return_value=1.3):
        assert _maintenance_lock_retry_delay(SimpleNamespace(env={'ARCHIVE_LOCK_RETRY_SECONDS':'10000'}),result,300)<=30
