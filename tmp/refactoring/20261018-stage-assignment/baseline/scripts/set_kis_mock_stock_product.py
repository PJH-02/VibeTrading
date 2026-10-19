"""Apply the user's approved switch to the KIS mock stock product (01)."""
import os
import re
import shutil
from pathlib import Path

path = Path('/root/vibetrading/.env')
backup = path.with_name('.env.before-etf-scope-20261013')
if not backup.exists():
    shutil.copy2(path,backup)
    os.chmod(backup,0o600)
source = path.read_text()
assert re.search(r'^KIS_MOCK_ACCOUNT_PRODUCT_CODE\s*=',source,re.M)
source = re.sub(r'^KIS_MOCK_ACCOUNT_PRODUCT_CODE\s*=.*$', 'KIS_MOCK_ACCOUNT_PRODUCT_CODE=01',source,flags=re.M)
path.write_text(source)
os.chmod(path,0o600)
print('KIS mock stock product is now 01; other credential fields preserved.')
