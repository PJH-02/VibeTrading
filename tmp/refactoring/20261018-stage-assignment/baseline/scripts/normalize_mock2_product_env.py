"""Docker env_file treats inline comments as part of the account code."""
from pathlib import Path
import re
p=Path('/root/vibetrading/.env')
s=p.read_text()
s,n=re.subn(r'(?m)^(KIS_MOCK2_ACCOUNT_PRODUCT_CODE\s*=\s*01)\s+#(.*)$',r'#\2\n\1',s)
if n:p.write_text(s)
print({'server_product_inline_comment_normalized':bool(n)})
