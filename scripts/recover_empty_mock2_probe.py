import json
import subprocess
import time
from pathlib import Path
subprocess.run(['docker','stop','-t','1','vibetrading-mock2-validation'],capture_output=True)
p=Path('/srv/vibetrading-data/runtime_data/state/etf-mock/mock2-validation.json')
if p.exists():
    assert json.loads(p.read_text())['orders']==[], 'Orders exist: reconcile instead of rerunning'
    p.rename(p.with_name('mock2-validation-no-orders-'+str(time.time_ns())+'.json'))
print('Verified no submitted orders; empty probe record preserved')
