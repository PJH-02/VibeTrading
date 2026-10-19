"""Server-local private Site read probe. Never prints credentials."""
import json
from pathlib import Path
from urllib.request import Request, urlopen

configuration = {}
for line in Path("/etc/vibetrading/dashboard.env").read_text().splitlines():
    name, value = line.split("=", 1)
    configuration[name] = value
request = Request(configuration["DASHBOARD_SITE_URL"] + "/api/state", headers={
    "OAI-Sites-Authorization": "Bearer " + configuration["DASHBOARD_SITE_BYPASS_TOKEN"]})
try:
    with urlopen(request, timeout=20) as response:
        state = json.load(response)
    order_counts = {}
    for section in ("stocks", "crypto", "timefolio"):
        order_request = Request(configuration["DASHBOARD_SITE_URL"] + "/api/orders?section=" + section,
                                headers=request.headers)
        with urlopen(order_request, timeout=20) as response:
            records = json.load(response)["rows"]
        order_counts[section] = {"count": len(records), "modes": sorted(set(row["mode"] for row in records))}
    print(json.dumps({"connected": state.get("connected"), "generated_ms": state.get("generated_ms"),
        "markets": len(state.get("markets", [])), "books": sum(bool(row.get("book")) for row in state.get("markets", [])),
        "venues": sorted(set(row["venue"] for row in state.get("markets", []))),
        "strategies": len(state.get("strategies", [])), "order_history": order_counts,
        "timefolio": [{"label": row["label"], "status": row["status"], "nav_available": row.get("nav") is not None,
            "turnover_available": row.get("turnover_pct") is not None, "pnl_available": row.get("realized_pnl") is not None} for row in state.get("timefolio", [])]}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"error_type": type(exc).__name__, "status": getattr(exc, "code", None)}))
    raise SystemExit(1)
