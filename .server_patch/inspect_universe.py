import json
import sqlite3


connection = sqlite3.connect("/data/state/vibetrading.sqlite3")
rows = connection.execute(
    """
    SELECT provider, payload, valid_from_ts_ns
    FROM universe_manifest
    ORDER BY valid_from_ts_ns DESC
    """
).fetchall()
latest = {}
for provider, payload, timestamp in rows:
    if provider not in latest:
        latest[provider] = {"timestamp": timestamp, "payload": json.loads(payload)}
print(json.dumps(latest, ensure_ascii=False, indent=2))
