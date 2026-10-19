from __future__ import annotations

import asyncio
import json

import websockets


URLS = (
    "wss://fstream.binance.com/stream?streams=samsungusdt@depth@100ms",
    "wss://fstream.binance.com/public/stream?streams=samsungusdt@depth@100ms",
    "wss://fstream.binance.com/market/stream?streams=samsungusdt@kline_1m",
)


async def probe(url: str) -> None:
    try:
        async with websockets.connect(url, open_timeout=8, ping_interval=20) as socket:
            print(url, "CONNECTED")
            print((await asyncio.wait_for(socket.recv(), timeout=8))[:240])
    except Exception as exc:
        print(url, type(exc).__name__, str(exc))


async def main() -> None:
    for url in URLS:
        await probe(url)
    try:
        async with websockets.connect("wss://api.hyperliquid.xyz/ws", open_timeout=8, ping_interval=20) as socket:
            for coin in ("xyz:SMSN", "xyz:SKHX", "xyz:SKHY"):
                for subscription in ({"type": "l2Book", "coin": coin, "fast": True}, {"type": "activeAssetCtx", "coin": coin}):
                    await socket.send(json.dumps({"method": "subscribe", "subscription": subscription}))
            for _ in range(12):
                print("hyperliquid", (await asyncio.wait_for(socket.recv(), timeout=8))[:240])
    except Exception as exc:
        print("hyperliquid", type(exc).__name__, str(exc))


if __name__ == "__main__":
    asyncio.run(main())
