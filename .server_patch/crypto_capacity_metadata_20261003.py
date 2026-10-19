import json
from pathlib import Path
import subprocess
import urllib.request

base='http://169.254.169.254/latest/'
headers={}
try:
    req=urllib.request.Request(base+'api/token',method='PUT',headers={'X-NCP-METADATA-TOKEN-TTL-SECONDS':'60'})
    with urllib.request.urlopen(req,timeout=2) as r:headers['X-NCP-METADATA-TOKEN']=r.read().decode()
except Exception:pass
result={'virtualization':subprocess.check_output(['systemd-detect-virt'],text=True).strip()}
for field in ('serverProductCode','serverInstanceType/code','cpuCount','memorySize','baseBlockStorageDiskDetailType/code'):
    try:
        with urllib.request.urlopen(urllib.request.Request(base+'meta-data/'+field,headers=headers),timeout=2) as r:result[field]=r.read().decode().strip()
    except Exception as e:result[field]={'unavailable':type(e).__name__}
Path('/opt/vibetrading/releases/binance-bridge-fix-20261003/capacity-metadata-20261003.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
