"""Run once in a network-isolated root container; export only display fields."""
import json
import os
from pathlib import Path
from operations.dashboard.projection import probe_orders

target = Path("/exports/pilot_orders.json")
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(probe_orders(Path("/audits")), ensure_ascii=False), encoding="utf-8")
os.chown(temporary, 10001, 10001)
os.chmod(temporary, 0o600)
temporary.replace(target)
print(json.dumps({"exported_orders": len(json.loads(target.read_text(encoding="utf-8")))}))
