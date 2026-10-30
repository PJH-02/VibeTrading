"""Public Arcus perp feed retaining native identifiers and book frames."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from core.models import BookUpdateType, Side, TradeEvent, Venue

from .perp_common import PerpCollectorBase, SYMBOLS, _instrument


class ArcusPerpCollector(PerpCollectorBase):
    def __init__(self, symbols: tuple[str, ...] = SYMBOLS) -> None:
        super().__init__(Venue.ARCUS, symbols)
        self.native_symbols = {_instrument(Venue.ARCUS, s).raw_symbol: s for s in self.symbols}

    def _common(self, symbol: str, source: Any, native_id: str | None = None) -> dict[str, Any]:
        result = super()._common(symbol, None, native_id)
        result['source_ts_ns'] = int(source) * 1000 if source is not None else None
        return result

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        self.connection_id = uuid.uuid4().hex
        self.last_book.clear()
        self._first_delta_after_snapshot.clear()
        await self._arcus(bus, duration_sec)

    async def _arcus(self, bus: Any, duration_sec: float | None) -> None:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get('https://api.arcus.xyz/v1/markets') as response:
                response.raise_for_status()
                rows = (await response.json())['markets']
        listed = {r['marketDisplayName'] for r in rows if r.get('status') == 'ONLINE'}
        missing = set(self.native_symbols) - listed
        if missing:
            raise RuntimeError(f'Arcus requested markets are unavailable: {sorted(missing)}')
        async with websockets.connect('wss://api.arcus.xyz/v1/ws', ping_interval=20,
                ping_timeout=20, max_queue=8, max_size=2*1024*1024) as ws:
            for native in self.native_symbols:
                await ws.send(json.dumps({'type':'subscribe','channel':'l2OrderbookUpdates','id':native,'nLevels':100}))
                await ws.send(json.dumps({'type':'subscribe','channel':'trades','id':native,'snapshot':False}))
            await ws.send(json.dumps({'type':'subscribe','channel':'markets'}))
            await self._receive(ws, bus, duration_sec, self._arcus_message)

    async def _arcus_message(self, bus: Any, msg: dict[str, Any], raw: str) -> None:
        if msg.get('type') == 'error' or msg.get('error'):
            raise RuntimeError('Arcus public subscription rejected')
        channel = msg.get('channel')
        data = msg.get('contents')
        if channel == 'markets' and isinstance(data, dict):
            for row in (data.get('markets') or {}).values():
                symbol = self.native_symbols.get(row.get('marketDisplayName'))
                if symbol:
                    await self._ticker(bus,symbol,row,None,'openInterest','fundingRate','base_asset')
            return
        symbol = self.native_symbols.get(msg.get('id'))
        if symbol is None or data is None:
            return
        if channel == 'l2OrderbookUpdates' and isinstance(data,dict):
            seq = int(data['lastSequenceId'])
            snapshot = msg.get('type') == 'subscribed'
            last = self.last_book.get(symbol)
            if not snapshot and last is not None and seq<=last:
                await self._unsynced_book(bus,symbol,data.get('timestamp'),data.get('bids',[]),data.get('asks',[]),seq,None,None,channel,raw)
                return
            # Initial snapshot/delta boundary can skip IDs. Thereafter the
            # per-market counter must be contiguous; global IDs may jump.
            if not snapshot and last is not None and symbol not in self._first_delta_after_snapshot and seq!=last+1:
                await self._unsynced_book(bus,symbol,data.get('timestamp'),data.get('bids',[]),data.get('asks',[]),seq,last,None,channel,raw)
                await self._gap(bus,symbol,f'Arcus book sequence gap last={last} current={seq}')
                raise RuntimeError('Arcus mid-stream book sequence gap')
            await self._book(bus,symbol,data.get('timestamp'),data.get('bids',[]),data.get('asks',[]),
                BookUpdateType.SNAPSHOT if snapshot else BookUpdateType.DELTA,seq,channel=channel,raw_payload=raw)
        elif channel == 'trades' and isinstance(data,list):
            for row in data:
                if row.get('tradeId') is None:
                    raise RuntimeError('Arcus trade is missing native tradeId')
                await bus.publish_market_event(TradeEvent(**self._common(symbol,row.get('timestamp'),str(row['tradeId'])),
                    price=Decimal(str(row['price'])),quantity=Decimal(str(row['size'])),
                    aggressor_side=Side.UNKNOWN,trade_id=str(row['tradeId'])))
