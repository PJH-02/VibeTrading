from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from vibetrading.dashboard.bridge import write_json
from vibetrading.dashboard.projection import timefolio_metrics
from vibetrading.timefolio.client import AsyncTimefolioBrowserManager, TimefolioCredentials


def decrypt(envelope: dict, key) -> dict:
    decode = base64.b64decode
    secret = key.decrypt(decode(envelope["wrapped_key"]), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return json.loads(AESGCM(secret).decrypt(decode(envelope["iv"]), decode(envelope["ciphertext"]), None))


async def run() -> None:
    root = Path(os.environ.get("DASHBOARD_MONITOR_ROOT", "/monitor"))
    key = serialization.load_pem_private_key((root / "private.pem").read_bytes(), password=None)
    rows = {}
    while True:
        accounts = []
        configured_ids = set()
        if os.environ.get("TIMEFOLIO_ID") and os.environ.get("TIMEFOLIO_PW"):
            accounts.append({"id": "default", "label": "기본 계정", "credentials": {
                "username": os.environ["TIMEFOLIO_ID"], "password": os.environ["TIMEFOLIO_PW"]}})
        try:
            for account in json.loads((root / "accounts.json").read_text(encoding="utf-8")):
                configured_ids.add(account["id"])
                try:
                    accounts.append({**account, "credentials": decrypt(json.loads(account["envelope"]), key)})
                except Exception:
                    rows[account["id"]] = {"id": account["id"], "label": account["label"], "status": "unavailable",
                        "updated_ms": int(time.time() * 1000), "realized_pnl": None, "turnover_pct": None, "nav": None, "positions": []}
        except (OSError, ValueError, KeyError):
            pass
        active = {account["id"] for account in accounts} | configured_ids
        rows = {k: v for k, v in rows.items() if k in active}
        write_json(root / "timefolio.json", list(rows.values()))
        # Each browser is released after one account. Login/order submission state is isolated.
        for account in accounts:
            row = {"id": account["id"], "label": account["label"], "currency": "KRW", "updated_ms": int(time.time() * 1000),
                "turnover_pct": None, "realized_pnl": None, "nav": None, "positions": [], "status": "loading"}
            rows[account["id"]] = row
            write_json(root / "timefolio.json", list(rows.values()))
            try:
                async with AsyncTimefolioBrowserManager(headless=True, live_submit_enabled=False) as manager:
                    credentials = account["credentials"]
                    status = await manager.login(account["id"], TimefolioCredentials(credentials["username"], credentials["password"]))
                    if not status.logged_in:
                        row["status"] = "login_failed"
                    else:
                        page = await manager.page_for(account["id"])
                        try:
                            await page.wait_for_function("() => !(document.body.innerText || '').includes('Loading')", timeout=10000)
                        except Exception:
                            pass
                        summary = await manager.scrape_summary(page)
                        row.update(timefolio_metrics(summary))
                        row["positions"] = summary.get("positions", [])
                        row["status"] = "connected"
                        row["metrics_basis"] = "타임폴리오 계정 화면의 명시된 지표"
                        row["data_note"] = "대회 종료" if "대회종료" in summary.get("text", "") else None
                        # A screenshot/DOM is never sent to hosted storage; only allowlisted metrics.
            except Exception as exc:
                row["status"] = "unavailable"
                row["error_type"] = type(exc).__name__
            row["updated_ms"] = int(time.time() * 1000)
            write_json(root / "timefolio.json", list(rows.values()))
        await asyncio.sleep(max(60, int(os.environ.get("DASHBOARD_TIMEFOLIO_POLL_SECONDS", "120"))))


if __name__ == "__main__":
    asyncio.run(run())
