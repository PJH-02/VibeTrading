"""Start and enable the approved KIS mock-only ETF loop after preflight."""
import json
import subprocess
from pathlib import Path

root = Path('/opt/vibetrading/releases/scope-20261013')
preflight = json.loads((root/'preflight.json').read_text())
assert preflight['exit_code']==0
result = json.loads(preflight['output'])
assert result['mock'] is True and set(result['books'])=={'C3','KP','KD'}
unit = Path('/etc/systemd/system/vibetrading-etf-mock.service')
unit.write_text('''[Unit]
Description=VibeTrading C3 KP KD KIS mock ETF loop
Requires=docker.service vibetrading.service
After=docker.service vibetrading.service network-online.target
RequiresMountsFor=/srv/vibetrading-data /srv/vibetrading-capacity

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/root/vibetrading
ExecStart=/usr/bin/docker compose --project-name vibetrading -f /opt/vibetrading/releases/scope-20261013/etf-mock.yml up -d --no-deps --no-build etf-mock
ExecStop=/usr/bin/docker compose --project-name vibetrading -f /opt/vibetrading/releases/scope-20261013/etf-mock.yml stop etf-mock
TimeoutStartSec=180
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
''')
subprocess.check_call(['systemctl','daemon-reload'])
csv_unit = Path('/etc/systemd/system/vibetrading-etf-csv-sync.service')
csv_unit.write_text(unit.read_text().replace('C3 KP KD KIS mock ETF loop','ticker CSV closed-session synchronizer').replace('etf-mock\n','etf-csv-sync\n'))
subprocess.check_call(['systemctl','daemon-reload'])
subprocess.check_call(['systemctl','enable','--now','vibetrading-etf-csv-sync.service'])
subprocess.check_call(['systemctl','enable','--now','vibetrading-etf-mock.service'])
print(json.dumps({'service':unit.stem,'mock':True,'enabled':True}))
