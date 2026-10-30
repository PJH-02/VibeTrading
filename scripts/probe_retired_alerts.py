"""Inspect alert scope without revealing Telegram credentials."""
import json
from runtime.settings import get_settings
from operations.health import check
from operations.telegram import TelegramController

settings=get_settings()
group=check(settings,'crypto-collectors',90)
print(json.dumps({'providers':group.get('providers'),'ok':group.get('ok'),
    'workers':{k:{f:v.get(f) for f in ['state','phase','ok','reason','age_seconds']}
        for k,v in group.get('providers_health',{}).items()}},ensure_ascii=False))
print(TelegramController(settings)._compact_status())
