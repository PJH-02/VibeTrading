"""Read only the two storage deployment identities; never print credentials."""
import json
from pathlib import Path
import subprocess

from collect_korean_hotfix_metadata import REMOTE

code = REMOTE.replace("NAMES=('vibetrading-etf-mock-1','vibetrading-etf-csv-sync-1')",
                      "NAMES=('vibetrading-storage-1','vibetrading-crypto-storage-1')")
result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
                         '49.50.141.106','python3','-'], input=code,
                        text=True,encoding='utf-8',capture_output=True,timeout=90)
if result.returncode:
    raise RuntimeError('Metadata collection failed; remote output suppressed')
data=json.loads(result.stdout)
path=Path('docs/refactoring/runs/20261018-stage-assignment/p02-server-preflight.json')
path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'path':str(path),'containers':{name:{key:item[key] for key in
    ('service','image_tag','image_id','state','restart_count')} for name,item in data['containers'].items()}}))
