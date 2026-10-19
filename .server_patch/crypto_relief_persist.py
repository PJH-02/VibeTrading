"""Persist the split storage release without recreating Market services."""
from pathlib import Path
import shutil
import subprocess

root = Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
target = Path('/etc/systemd/system/vibetrading.service')
shutil.copy2(target, root / 'vibetrading.service.before')
unit = '''[Unit]
Description=VibeTrading collectors with split crypto state storage
Requires=docker.service
After=docker.service network-online.target
RequiresMountsFor=/srv/vibetrading-data /srv/vibetrading-crypto-state
ConditionPathIsMountPoint=/srv/vibetrading-data

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=300
TimeoutStopSec=300
WorkingDirectory=/root/vibetrading
ExecStartPre=/usr/bin/test -d /srv/vibetrading-data/nats/jetstream
ExecStartPre=/usr/bin/test -f /srv/vibetrading-crypto-state/vibetrading.sqlite3
ExecStart=/usr/bin/docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env -f /opt/vibetrading/releases/crypto-relief-final-20261002/deploy/nats-relief.yml up -d --no-deps --no-build nats
ExecStart=/usr/bin/docker start vibetrading-storage-1 vibetrading-maintenance-1 vibetrading-collectors-1 vibetrading-telegram-1
ExecStart=/usr/bin/docker compose --project-name vibetrading --env-file /etc/vibetrading/compose.env --env-file /opt/vibetrading/releases/crypto-relief-final-20261002/deploy/release.env -f /opt/vibetrading/releases/crypto-relief-final-20261002/deploy/crypto-r2.yml up -d --no-deps --no-build
ExecStop=/usr/bin/docker stop --time 90 vibetrading-crypto-collector-binance-1 vibetrading-crypto-collector-bitget-1 vibetrading-crypto-collector-bybit-1 vibetrading-collectors-1 vibetrading-crypto-maintenance-1 vibetrading-maintenance-1 vibetrading-telegram-1
ExecStop=/usr/bin/docker stop --time 90 vibetrading-crypto-storage-1 vibetrading-storage-1
ExecStop=/usr/bin/docker stop --time 30 vibetrading-nats-1

[Install]
WantedBy=multi-user.target
'''
target.write_text(unit)
subprocess.run(['systemd-analyze', 'verify', str(target)], check=True)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', 'vibetrading.service'], check=True)
subprocess.run(['systemctl', 'start', 'vibetrading.service'], check=True)
print(subprocess.check_output(['systemctl', 'is-active', 'vibetrading.service'], text=True).strip())
