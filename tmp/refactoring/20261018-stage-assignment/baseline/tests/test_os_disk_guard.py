from types import SimpleNamespace
import json
import subprocess

import pytest

from scripts import os_disk_guard as guard


def setup_guard(tmp_path, monkeypatch, free, paused=False):
    marker = tmp_path/'paused'; status = tmp_path/'health/guard.json'; calls = []
    if paused: marker.touch()
    monkeypatch.setattr(guard, 'MARKER', marker)
    monkeypatch.setattr(guard, 'STATUS', status)
    monkeypatch.setattr(guard.shutil, 'disk_usage', lambda _:SimpleNamespace(free=free))
    monkeypatch.setenv('VIBETRADING_OS_PAUSE_FREE_BYTES', str(1024**3))
    monkeypatch.setenv('VIBETRADING_OS_RESUME_FREE_BYTES', str(2*1024**3))
    monkeypatch.delenv('VIBETRADING_OS_GUARD_DRY_RUN', raising=False)
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='false\n')
    monkeypatch.setattr(guard.subprocess, 'run', run)
    return marker, status, calls


def test_disk_pause_is_visible_when_container_is_already_stopped(tmp_path, monkeypatch):
    marker, status, calls = setup_guard(tmp_path, monkeypatch, 900*1024**2)
    guard.main()
    assert marker.exists() and json.loads(status.read_text())['paused']
    assert all('stop' not in cmd for cmd in calls)


def test_resume_uses_current_pinned_compose_and_only_collectors(tmp_path, monkeypatch):
    marker, status, calls = setup_guard(tmp_path, monkeypatch, 3*1024**3, paused=True)
    guard.main()
    assert not marker.exists() and not json.loads(status.read_text())['paused']
    assert len(calls) == 1 and calls[0][-1] == 'collectors'
    assert guard.COMPOSE in calls[0] and '--no-deps' in calls[0] and '--no-build' in calls[0]


def test_hysteresis_does_not_restart_before_two_gib(tmp_path, monkeypatch):
    marker, status, calls = setup_guard(tmp_path, monkeypatch, 1536*1024**2, paused=True)
    guard.main()
    assert marker.exists() and json.loads(status.read_text())['paused'] and not calls


def test_failed_resume_preserves_pause_marker_for_next_retry(tmp_path, monkeypatch):
    marker, status, calls = setup_guard(tmp_path, monkeypatch, 3*1024**3, paused=True)
    def fail(cmd, **kwargs): raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(guard.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError): guard.main()
    assert marker.exists()
