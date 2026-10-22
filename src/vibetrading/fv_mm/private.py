"""Native private order/fill events, with REST reconciliation after gaps."""
from __future__ import annotations
import asyncio
import json
import websockets
from .venues import ASSETS


async def bybit_private(client, on_order, on_fill, ready, journal):
    while True:
        ping=None
        try:
            async with await client.client.connect_private() as ws:
                ready.set()
                async def heartbeat():
                    while True:
                        await asyncio.sleep(15); await ws.send('{"op":"ping"}')
                ping=asyncio.create_task(heartbeat())
                async for raw in ws:
                    msg=json.loads(raw)
                    if msg.get("topic")=="order":
                        for row in msg["data"]: on_order("bybit",row,"private")
                    elif msg.get("topic")=="execution":
                        for row in msg["data"]: on_fill("bybit",row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ready.clear(); journal.event("private_gap","bybit",reason=type(exc).__name__)
            await asyncio.sleep(2)
        finally:
            if ping:
                ping.cancel(); await asyncio.gather(ping,return_exceptions=True)


async def grvt_private(client, on_order, on_fill, ready, journal):
    while True:
        try:
            await client.login()
            async with websockets.connect("wss://trades.testnet.grvt.io/ws/full",additional_headers=client.headers,ping_interval=15,max_queue=2048) as ws:
                for n,stream in enumerate(("v1.order","v1.fill"),1):
                    await ws.send(json.dumps({"jsonrpc":"2.0","method":"subscribe","params":{"stream":stream,"selectors":[str(client.subaccount)+"-"+a+"_USDT_Perp" for a in ASSETS]},"id":n}))
                subscribed=set()
                async for raw in ws:
                    msg=json.loads(raw)
                    if msg.get("error"):
                        raise RuntimeError("GRVT private subscription rejected")
                    if msg.get("id") in (1,2) and "result" in msg:
                        subscribed.add(msg["id"])
                        if len(subscribed)==2: ready.set()
                    feed=msg.get("feed")
                    if isinstance(feed,dict):
                        if msg.get("stream")=="v1.order": on_order("grvt",feed,"private")
                        elif msg.get("stream")=="v1.fill": on_fill("grvt",feed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ready.clear(); journal.event("private_gap","grvt",reason=type(exc).__name__)
            await asyncio.sleep(2)
