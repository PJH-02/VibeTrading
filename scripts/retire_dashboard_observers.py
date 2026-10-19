"""Apply the same retired-venue scope to dashboard public/account observers."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

root = Path('/opt/vibetrading/dashboard')
path = root/'vibetrading/dashboard/bridge.py'
backup = path.with_name('bridge.py.before-scope-20261013')
if not backup.exists():
    shutil.copy2(path,backup)
source = path.read_text()
old = '    for venue in os.environ.get("DASHBOARD_CRYPTO_VENUES", "binance,bitget,bybit,grvt,arcus").split(","):'
if old in source:
    source = source.replace(old, '    venues = os.environ.get("DASHBOARD_CRYPTO_VENUES", "binance,grvt,arcus").split(",")\n    for venue in venues:')
    source = source.replace('        tasks.append(asyncio.create_task(observe_accounts()))', '        if "bybit" in venues:\n            tasks.append(asyncio.create_task(observe_accounts()))')
assert '    venues = os.environ.get("DASHBOARD_CRYPTO_VENUES", "binance,grvt,arcus")' in source
compile(source,str(path),'exec')
path.write_text(source)
compose = root/'deploy/dashboard-compose.yml'
backup = compose.with_name('dashboard-compose.yml.before-scope-20261013')
if not backup.exists():
    shutil.copy2(compose,backup)
text = compose.read_text()
if 'DASHBOARD_CRYPTO_VENUES:' in text:
    text = re.sub(r'DASHBOARD_CRYPTO_VENUES:.*', 'DASHBOARD_CRYPTO_VENUES: binance,grvt,arcus',text)
else:
    text = text.replace('      NATS_URL: nats://nats:4222', '      NATS_URL: nats://nats:4222\n      DASHBOARD_CRYPTO_VENUES: binance,grvt,arcus')
compose.write_text(text)
image = subprocess.check_output(['docker','inspect','--format','{{.Config.Image}}','vibetrading-monitor-bridge-1'],text=True).strip()
environment = dict(os.environ,DASHBOARD_APP_IMAGE=image)
subprocess.check_call(['docker','compose','--project-name','vibetrading-monitor','-f',str(compose),'up','-d','--no-deps','--no-build','bridge'],env=environment)
print(json.dumps({'observers':['binance','grvt','arcus'],'bybit_account_polling':False,'image':image}))
