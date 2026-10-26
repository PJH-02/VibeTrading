"""Public Arcus/GRVT perp feeds, retaining native identifiers and book frames.

Protocol sources: docs.arcus.xyz/api-reference/websocket and
api-docs.grvt.io/market_data_streams. Only BTC/ETH/SOL are admitted.
"""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from .crypto_perp import CryptoPerpCollector, SYMBOLS, _instrument
from core.models import BookUpdateType, Side, TradeEvent, Venue


class ExtendedPerpCollector(CryptoPerpCollector):
    def __init__(self, venue: Venue, symbols: tuple[str, ...] = SYMBOLS) -> None:
        if venue not in {Venue.ARCUS, Venue.GRVT}:
            raise ValueError('extended venue must be Arcus or GRVT')
        super().__init__(venue, symbols)
        self.native_symbols = {_instrument(venue, s).raw_symbol: s for s in self.symbols}
        self._grvt_baselines: dict[str, int] = {}

    def _common(self, symbol: str, source: Any, native_id: str | None = None) -> dict[str, Any]:
        result = super()._common(symbol, None, native_id)
        result['source_ts_ns'] = int(source) * (1000 if self.venue == Venue.ARCUS else 1) if source is not None else None
        return result

    async def run(self, bus: Any, duration_sec: float | None = None) -> None:
        self.connection_id = __import__('uuid').uuid4().hex
        self.last_book.clear()
        self._first_delta_after_snapshot.clear()
        self._grvt_baselines.clear()
        if self.venue == Venue.ARCUS:
            await self._arcus(bus, duration_sec)
        else:
            await self._grvt(bus, duration_sec)

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
            await self._receive(ws, bus, duration_sec)

    async def _grvt(self, bus: Any, duration_sec: float | None) -> None:
        async with websockets.connect('wss://market-data.grvt.io/ws/full', ping_interval=20,
                ping_timeout=20, max_queue=8, max_size=2*1024*1024) as ws:
            for request_id, (stream, suffix) in enumerate((('v1.book.d','@100'),('v1.trade',''),('v1.ticker.s','@1000')),1):
                await ws.send(json.dumps({'jsonrpc':'2.0','method':'subscribe','params':
                    {'stream':stream,'selectors':[s+suffix for s in self.native_symbols]},'id':request_id}))
            await self._receive(ws, bus, duration_sec)

    async def _receive(self, ws: Any, bus: Any, duration: float | None) -> None:
        deadline = time.monotonic()+duration if duration is not None else None
        while deadline is None or time.monotonic()<deadline:
            timeout = min(45, max(.001, deadline-time.monotonic())) if deadline is not None else 45
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout)
            except asyncio.TimeoutError:
                if deadline is not None and time.monotonic()>=deadline:
                    return
                raise RuntimeError(f'{self.name} WebSocket received no frames for 45 seconds')
            if self.venue == Venue.ARCUS:
                await self._arcus_message(bus,json.loads(raw),raw)
            else:
                await self._grvt_message(bus,json.loads(raw),raw)

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

    async def _grvt_message(self, bus: Any, msg: dict[str, Any], raw: str) -> None:
        if msg.get('error'):
            raise RuntimeError('GRVT public subscription rejected: '+str(msg['error'].get('message','')))
        result = msg.get('result')
        if isinstance(result,dict) and result.get('stream')=='v1.book.d':
            for selector, first in zip(result.get('subs',[]),result.get('first_sequence_number',[])):
                symbol=self.native_symbols.get(selector.split('@')[0])
                if symbol:
                    # Wire snapshots use sequence 0, while live deltas start
                    # at the gateway's existing counter advertised in the ACK.
                    self._grvt_baselines[symbol]=int(first)-1
        data = msg.get('feed')
        if not isinstance(data,dict):
            return
        symbol = self.native_symbols.get(data.get('instrument') or str(msg.get('selector','')).split('@')[0])
        if symbol is None:
            return
        stream = msg.get('stream')
        if stream == 'v1.book.d':
            seq = int(msg['sequence_number'])
            snapshot = seq==0
            if snapshot:
                seq=self._grvt_baselines.get(symbol,0)
            last = self.last_book.get(symbol)
            bids = [[r['price'],r['size']] for r in data.get('bids',[])]
            asks = [[r['price'],r['size']] for r in data.get('asks',[])]
            if not snapshot and last is not None and seq<=last:
                await self._unsynced_book(bus,symbol,data.get('event_time'),bids,asks,seq,None,None,stream,raw)
                return
            previous = int(msg['prev_sequence_number']) if msg.get('prev_sequence_number') is not None else last
            if not snapshot and last is not None and (previous!=last or seq!=last+1):
                await self._unsynced_book(bus,symbol,data.get('event_time'),bids,asks,seq,previous,None,stream,raw)
                await self._gap(bus,symbol,f'GRVT book sequence gap last={last} current={seq}')
                raise RuntimeError('GRVT book sequence gap')
            await self._book(bus,symbol,data.get('event_time'),bids,asks,
                BookUpdateType.SNAPSHOT if snapshot else BookUpdateType.DELTA,seq,
                previous=previous if not snapshot else None,channel=stream,raw_payload=raw)
        elif stream == 'v1.trade':
            await bus.publish_market_event(TradeEvent(**self._common(symbol,data.get('event_time'),str(data['trade_id'])),
                price=Decimal(str(data['price'])),quantity=Decimal(str(data['size'])),
                aggressor_side=Side.BUY if data['is_taker_buyer'] else Side.SELL,trade_id=str(data['trade_id'])))
        elif stream == 'v1.ticker.s':
            await self._ticker(bus,symbol,data,data.get('event_time'),'open_interest','funding_rate','base_asset')
