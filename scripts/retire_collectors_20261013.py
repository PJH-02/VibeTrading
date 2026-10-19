"""Persist the user-requested retired crypto venues without changing storage."""
import json
import shutil
import subprocess
from pathlib import Path

path = Path('/opt/vibetrading/releases/capacity-20261006/crypto.yml')
backup = path.with_name('crypto.yml.before-scope-20261013')
if not backup.exists():
    shutil.copy2(path, backup)
config = json.loads(path.read_text())
for name in ['crypto-collector-bitget', 'crypto-collector-bybit']:
    config['services'][name]['profiles'] = ['retired']
    config['services'][name]['restart'] = 'no'
for service in config['services'].values():
    service.get('environment', {})['CRYPTO_COLLECTOR_PROVIDERS'] = 'binance-perp,arcus-perp,grvt-perp'
path.write_text(json.dumps(config, indent=2))
print(json.dumps({'retired': ['bitget', 'bybit'], 'persisted': str(path)}))
