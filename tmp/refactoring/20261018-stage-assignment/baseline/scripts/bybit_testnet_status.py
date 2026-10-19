"""Read-only Bybit testnet position and open-order check for probe cleanup."""
from __future__ import annotations

import asyncio
import json

import aiohttp

from vibetrading.bybit_execution import BybitCredentials, BybitOrderClient
from vibetrading.settings import get_settings


async def main() -> None:
    credentials = BybitCredentials.from_settings(get_settings(), testnet=True)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        client = BybitOrderClient(credentials, session)
        await client.sync_clock()
        positions = await client.request("GET", "/v5/position/list", {"category":"linear","symbol":"SOLUSDT"})
        orders = await client.request("GET", "/v5/order/realtime",
                                      {"category":"linear","symbol":"SOLUSDT","openOnly":0,"limit":50})
    nonzero = [{"side":p.get("side"),"size":p.get("size")}
               for p in positions.get("result",{}).get("list",[])
               if float(p.get("size") or 0) != 0]
    open_rows = [{"order_id":row.get("orderId"),"status":row.get("orderStatus")}
                 for row in orders.get("result",{}).get("list",[])
                 if row.get("orderStatus") in {"New","PartiallyFilled","Untriggered"}]
    print(json.dumps({"symbol":"SOLUSDT","nonzero_positions":nonzero,"open_orders":open_rows,
                      "clock_offset_ms":client.server_clock_offset_ms,
                      "clock_rtt_ms":round(client.clock_rtt_ms,3)},sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
