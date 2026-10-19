"""Host guard: preserve thresholds, expose pause state, resume verified service."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

MARKER = Path('/run/vibetrading-os-disk-paused')
STATUS = Path('/srv/vibetrading-data/runtime_data/state/health/os-disk-guard.json')
COMPOSE = '/opt/vibetrading/releases/telegram-recovery-20261014/runtime.yml'


def main():
    pause = int(os.environ.get('VIBETRADING_OS_PAUSE_FREE_BYTES', 1024**3))
    resume = int(os.environ.get('VIBETRADING_OS_RESUME_FREE_BYTES', 2*1024**3))
    if not 0 < pause < resume:
        raise ValueError('Invalid OS disk guard thresholds')
    free = shutil.disk_usage('/').free
    action = 'pause' if free <= pause else 'resume' if MARKER.exists() and free >= resume else 'none'
    result = {'component':'os-disk-guard', 'ts_ns':time.time_ns(), 'free_bytes':free,
              'pause_bytes':pause, 'resume_bytes':resume, 'action':action,
              'paused':MARKER.exists() or action == 'pause'}
    if os.environ.get('VIBETRADING_OS_GUARD_DRY_RUN') == '1':
        print(json.dumps(result)); return
    if action == 'pause':
        MARKER.touch()
        check = subprocess.run(['docker','inspect','--format','{{.State.Running}}',
                                'vibetrading-collectors-1'],capture_output=True,text=True)
        if check.returncode == 0 and check.stdout.strip() == 'true':
            subprocess.run(['docker','stop','--timeout','30','vibetrading-collectors-1'],check=True)
    elif action == 'resume':
        subprocess.run(['docker','compose','--project-name','vibetrading','-f',COMPOSE,
                        'up','-d','--no-deps','--no-build','collectors'],check=True)
        MARKER.unlink(missing_ok=True)
        result['paused'] = False
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    temp = STATUS.with_suffix('.tmp')
    temp.write_text(json.dumps(result),encoding='utf-8')
    temp.chmod(0o644)
    temp.replace(STATUS)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
