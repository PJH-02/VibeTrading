"""Keep OS-disk guard recovery pinned to the verified market deployment."""
import shutil
from pathlib import Path

path = Path('/usr/local/lib/vibetrading/os-disk-guard.sh')
backup = Path('/opt/vibetrading/releases/scope-20261013/os-disk-guard.sh.before')
if not backup.exists():
    shutil.copy2(path,backup)
source = path.read_text()
source = source.replace('docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env ',
    'docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env -f /opt/vibetrading/releases/capacity-20261006/market.yml ')
path.write_text(source)
print('Disk guard thresholds preserved; collector resume uses verified market.yml.')
