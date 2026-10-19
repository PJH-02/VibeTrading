from collections import namedtuple

import pytest

from vibetrading.settings import RuntimeSettings
from vibetrading.storage_guard import StorageGuard


@pytest.mark.parametrize('state_free,paused,resume', [
    (512 * 1024**2, True, False),
    (1100 * 1024**2, False, False),
    (2 * 1024**3, False, True),
])
def test_os_reserve_is_protected_even_when_data_disk_has_space(tmp_path, monkeypatch, state_free, paused, resume):
    env = tmp_path / '.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nSTORAGE_STATE_DISK_ROOT=/state-disk\n', encoding='utf-8')
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr('shutil.disk_usage', lambda p: usage(20 * 1024**3, 10 * 1024**3,
                         state_free if str(p) == '/state-disk' else 10 * 1024**3))
    guard = StorageGuard(RuntimeSettings.from_env(env))
    assert guard.should_pause_collection() is paused
    assert guard.can_resume_collection() is resume
    assert guard.evaluate().state_free_bytes == state_free


def test_missing_state_volume_fails_closed(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nSTORAGE_STATE_DISK_ROOT=/state-disk\n', encoding='utf-8')
    usage = namedtuple('usage', 'total used free')
    def probe(p):
        if str(p) == '/state-disk':
            raise FileNotFoundError('missing state mount')
        return usage(20 * 1024**3, 10 * 1024**3, 10 * 1024**3)
    monkeypatch.setattr('shutil.disk_usage', probe)
    decision = StorageGuard(RuntimeSettings.from_env(env)).evaluate()
    assert decision.fail_closed and decision.probe_error == 'state_FileNotFoundError'
