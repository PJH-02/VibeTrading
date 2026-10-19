"""Regression for upload starvation after a prune-only pressure deferral."""
import asyncio
from types import SimpleNamespace

import pytest

from vibetrading import runtime


@pytest.mark.parametrize('reason,stage,expected_cycles,uploads,scan_progressed,retired', [
    ('storage_pending_above_prune_limit', 'delivery_prune', 2, 32, False, 0),
    ('storage_pending_above_prune_limit', 'crypto_event_run_retirement', 2, 32, False, 0),
    ('storage_io_above_prune_limit', 'delivery_prune', 2, 32, False, 0),
    ('storage_io_above_prune_limit', 'crypto_event_run_retirement', 2, 32, False, 0),
    ('storage_or_io_busy', 'delivery_prune', 1, 32, False, 0),
    ('storage_health_unavailable', 'upload', 1, 32, False, 0),
    ('storage_pending_above_prune_limit', 'delivery_prune', 2, 0, True, 0),
    ('storage_pending_above_prune_limit', 'delivery_prune', 1, 0, False, 0),
    ('', '', 2, 0, False, 64),
])
@pytest.mark.parametrize('backlog_idle', [5, 30])
def test_prune_deferral_preserves_archive_progress_but_io_deferral_cools_down(monkeypatch, reason, stage, expected_cycles, uploads, scan_progressed, retired, backlog_idle):
    cycles, sleeps = [], []
    settings = SimpleNamespace(env={'ARCHIVE_UPLOAD_BATCH': '512', 'ARCHIVE_IDLE_SECONDS': '60',
                                    'ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT': '8192', 'CRYPTO_EVENT_RUN_RETIRE_BATCH': '64',
                                    'ARCHIVE_BACKLOG_IDLE_SECONDS': str(backlog_idle)})

    class Maintenance:
        def __init__(self, *_):
            pass

        def run_once(self):
            cycles.append(True)
            return {'ran': True, 'uploaded': 100, 'upload_candidates': 100,
                    'recovery_blocks': {'candidates': 32, 'uploaded': uploads, 'batch_limit': 32,
                                        'scan_progressed': scan_progressed},
                    'event_run_retirement': {'retired_runs': retired},
                    'deferred': bool(reason), 'reason': reason, 'deferred_stage': stage}

        def close(self):
            pass

    async def heartbeat(_settings, _stage, operation):
        return await operation

    async def compact():
        pass

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime, 'get_settings', lambda: settings)
    monkeypatch.setattr(runtime, 'ArchiveMaintenance', Maintenance)
    monkeypatch.setattr(runtime, 'health_touch', lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, '_with_maintenance_heartbeat', heartbeat)
    monkeypatch.setattr(runtime, '_compactor_loop_once', compact)
    monkeypatch.setattr(runtime.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintenance_loop())
    assert len(cycles) == expected_cycles
    expected_idle = backlog_idle if expected_cycles == 2 else 30.0
    assert sleeps == [expected_idle, expected_idle]


@pytest.mark.parametrize('checks', [[False, True, True], [True, False, True, True]])
def test_io_deferral_rechecks_resources_and_requires_two_quiet_samples(monkeypatch, checks):
    cycles, sleeps, observed = [], [], []
    settings = SimpleNamespace(env={'ARCHIVE_IDLE_SECONDS': '60'})

    class Maintenance:
        def __init__(self, *_): pass
        def close(self): pass
        def run_once(self):
            cycles.append(True)
            if len(cycles) == 2:
                raise asyncio.CancelledError
            return {'ran': True, 'deferred': True, 'reason': 'storage_or_io_busy', 'deferred_stage': 'reclaim_before_upload'}
        def _resource_maintenance_allowed(self):
            value = checks[len(observed)]
            observed.append(value)
            return value, {'reason': 'quiet' if value else 'storage_or_io_busy'}

    async def heartbeat(_settings, _stage, operation): return await operation
    async def compact(): pass
    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > len(checks):
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime, 'get_settings', lambda: settings)
    monkeypatch.setattr(runtime, 'ArchiveMaintenance', Maintenance)
    monkeypatch.setattr(runtime, 'health_touch', lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, '_with_maintenance_heartbeat', heartbeat)
    monkeypatch.setattr(runtime, '_compactor_loop_once', compact)
    monkeypatch.setattr(runtime.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintenance_loop())
    assert len(cycles) == 2
    assert observed == checks
    assert sleeps == [30.0] * len(checks)
