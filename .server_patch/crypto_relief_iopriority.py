"""Give the current maintenance worker bounded best-effort I/O priority."""
import json
from pathlib import Path
import subprocess

c = json.loads(subprocess.check_output(['docker', 'inspect', 'vibetrading-crypto-maintenance-1']))[0]
pid = c['State']['Pid']
assert pid > 0 and b'crypto-maintenance' in Path(f'/proc/{pid}/cmdline').read_bytes()
result = []
for p in Path(f'/proc/{pid}/task').iterdir():
    before = subprocess.check_output(['ionice', '-p', p.name], text=True).strip()
    subprocess.run(['ionice', '-c2', '-n7', '-p', p.name], check=True)
    after = subprocess.check_output(['ionice', '-p', p.name], text=True).strip()
    result.append({'tid': int(p.name), 'before': before, 'after': after})
root = Path('/opt/vibetrading/releases/crypto-relief-20261002')
(root / 'iopriority.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
