"""Server-only read probe of metric labels after login; never prints credentials."""
import asyncio
import json
import os
import re
from vibetrading.timefolio.client import AsyncTimefolioBrowserManager, TimefolioCredentials


async def main():
    async with AsyncTimefolioBrowserManager(headless=True, live_submit_enabled=False) as manager:
        status = await manager.login("inspect", TimefolioCredentials(os.environ["TIMEFOLIO_ID"], os.environ["TIMEFOLIO_PW"]))
        if not status.logged_in:
            print(json.dumps({"logged_in": False})); return
        summary = await manager.scrape_summary(await manager.page_for("inspect"))
        text = summary.get("text", "")
        snippets = [text[max(0, match.start() - 30):match.end() + 90] for match in re.finditer("회전|손익|NAV|실현|평가|수익률", text)]
        print(json.dumps({"snippets": snippets, "table_headers": [table.get("rows", [])[:3] for table in summary.get("tables", [])]}, ensure_ascii=False))


asyncio.run(main())
