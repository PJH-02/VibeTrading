"""Public projection bounds preserve canonical state, identities and immutable views."""
import asyncio
from collections import UserDict
from decimal import Decimal
import hashlib
import json
from types import MappingProxyType
from typing import Mapping as OldMapping

import pytest

from core.models import (AssetClass, BaseMarketEvent, BookUpdateType, DataGapEvent,
    IndexQuoteEvent, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue)
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from market.hot_path import HotPathStore, _event_fingerprint, freeze_market_event
from market.local_engine import EngineBudgets, LocalMarketEngine, _observation_noop
import runtime.data_only_runtime as assembly
import runtime.local_runtime as runtime
from test_data_only_runtime import setup


class AssemblyCaptured(Exception):
    pass


@pytest.mark.parametrize('observer', [False, True])
def test_runtime_constructs_public_bounds_without_changing_ordinary_defaults(tmp_path, monkeypatch, observer):
    plan, settings, spool, unused = setup(tmp_path)
    captured = []
    def capture(**kwargs):
        captured.append(kwargs['hot_path'])
        raise AssemblyCaptured
    monkeypatch.setattr(runtime, 'LocalMarketEngine', capture)
    callback = assembly._readiness(assembly._ValidatedScope(plan, settings)) if observer else None
    try:
        with pytest.raises(AssemblyCaptured):
            asyncio.run(runtime.run_engine(plan, settings, observation_only=observer,
                recovery_readiness=callback))
        hot, = captured
        assert (hot.max_books, hot.max_trades, hot.max_index_quotes) == (
            (1, 40, 1) if observer else (512, 2048, 2048))
        assert hot._seen.max_entries == 16384
    finally:
        spool.close()
        asyncio.run(unused.close())


INSTRUMENT = InstrumentId(venue=Venue.ARCUS, asset_class=AssetClass.CRYPTO_PERP,
    raw_symbol='BTC-USD', std_symbol='BTCUSDT')
STAMP = 1_800_000_000_000_000_000


def fields(seq):
    return dict(instrument=INSTRUMENT, venue=Venue.ARCUS, provider='arcus-perp',
        source_ts_ns=STAMP+seq, receive_ts_ns=STAMP+seq, wallclock_ts_ns=STAMP+seq,
        event_id=f'precise-{seq}', sequence=seq)


def book(seq, generation, update, previous, final):
    return OrderBookEvent(**fields(seq), update_type=update, depth=1,
        sync_generation=generation, previous_update_id=previous, final_update_id=final,
        bids=[OrderBookLevel(price=Decimal('123.000000000000000001'),
            quantity=Decimal(f'2.{seq:018d}'))],
        asks=[OrderBookLevel(price=Decimal('124.000000000000000009'),
            quantity=Decimal('3.000000000000000007'))], fast=True,
        raw_payload='{"retained":"native full precision"}')


def public_store():
    return HotPathStore(max_books=1, max_trades=40, max_index_quotes=1)


def projection(hot):
    snapshot = hot.snapshot(STAMP, 'REPLAY')
    return (snapshot.apply_seq, snapshot.versions, snapshot.book_versions, snapshot.quality,
        snapshot.trades, snapshot.books, snapshot.index_quotes, hot._crypto_books,
        hot._crypto_first, tuple(hot._fingerprints.items()))


def test_varied_native_prefix_retains_exact_state_with_bounded_derived_rings():
    default, bounded = HotPathStore(), public_store()
    events = [book(1, 1, BookUpdateType.SNAPSHOT, None, 100)]
    for seq in range(2, 62):
        events.append(TradeEvent(**fields(seq), price=Decimal('123.000000000000000003'),
            quantity=Decimal('0.000000000000000001')))
    events += [book(62, 1, BookUpdateType.DELTA, 100, 101),
        IndexQuoteEvent(**fields(63), value=Decimal('123.999999999999999999')),
        IndexQuoteEvent(**fields(64), value=Decimal('124.999999999999999999')),
        events[1],  # Identical old event, at a new transport cursor: state dedup remains exact.
        DataGapEvent(**fields(65), reason='native reconnect'),
        DataGapEvent(**fields(66), reason='repeated reconnect'),
        book(67, 2, BookUpdateType.SNAPSHOT, None, 200),
        book(68, 2, BookUpdateType.DELTA, 200, 201)]
    budgets = EngineBudgets(1, 4, 8192, 16, 131072, 5_000_000_000, 128, 1,
        20_000_000_000, .01)
    engines = [LocalMarketEngine(producer_providers={'public':'arcus-perp'}, budgets=budgets,
        on_invalidate=_observation_noop, hot_path=hot, observation_only=True)
        for hot in (default, bounded)]
    high = EngineCursor('public', 'epoch', len(events))
    try:
        for engine in engines:
            engine.begin_recovery(EngineCursor('public', 'epoch', 0), high, verified_checkpoint=True)
        for number, event in enumerate(events, 1):
            frame = RecoveryFrame(EngineCursor('public', 'epoch', number), event.model_dump_json().encode())
            for engine in engines:
                receipt = engine.apply_replay(frame)
                assert receipt.cursor == frame.cursor and receipt.digest == frame.digest
                before = engine.hot_path.apply_seq
                engine.apply_replay(frame)  # Transport duplicate never reapplies state.
                assert engine.hot_path.apply_seq == before
            assert projection(default) == projection(bounded)
            if isinstance(event, DataGapEvent):
                snapshot = bounded.snapshot(STAMP)
                assert snapshot.quality[INSTRUMENT.key] == 'INVALID'
                assert snapshot.latest_book(INSTRUMENT.key) is None
        assert len(default._books[INSTRUMENT.key]) == 2  # Last gap clears earlier projections.
        assert len(bounded._books[INSTRUMENT.key]) == 1
        assert len(default._trades[INSTRUMENT.key]) == 60
        assert len(bounded._trades[INSTRUMENT.key]) == 40
        assert len(default._index_quotes[INSTRUMENT.key]) == 2
        assert len(bounded._index_quotes[INSTRUMENT.key]) == 1
        assert default.recent_trades(INSTRUMENT.key) == bounded.recent_trades(INSTRUMENT.key)
        assert len(bounded.recent_trades(INSTRUMENT.key)) == 40
        assert default._seen.max_entries == bounded._seen.max_entries == 16384
        assert bounded.latest_book(INSTRUMENT.key) == default.latest_book(INSTRUMENT.key)
        snapshot = bounded.snapshot(STAMP)
        assert snapshot.books[INSTRUMENT.key].bids[0].price == Decimal('123.000000000000000001')
        with pytest.raises(TypeError): snapshot.books['new'] = snapshot.books[INSTRUMENT.key]
        with pytest.raises(Exception): snapshot.books[INSTRUMENT.key].bids[0].quantity = Decimal(0)
        # Expiry is observational and has identical effects despite different ring lengths.
        assert default.snapshot(STAMP+2_000_000_000).quality == bounded.snapshot(STAMP+2_000_000_000).quality
        for engine in engines:
            assert engine.stats['applied'] == len(events)
            assert engine.stats['queued_records'] == engine.stats['queued_bytes'] == engine.stats['delivered'] == 0
            assert engine.query_cursor('public').cursor == high
            assert engine.commit_recovery(high, frame.digest, ready=True)
        # Event identity conflicts still latch both stores identically.
        conflict = events[1].model_copy(update={'price':Decimal('999.000000000000000001')})
        results = [hot.apply_versioned(conflict) for hot in (default,bounded)]
        assert [r.reason for r in results] == ['event_id_conflict']*2
        assert projection(default) == projection(bounded)
    finally:
        for engine in engines: asyncio.run(engine.close())


def old_reference_fingerprint(event):
    def canonical(value):
        if isinstance(value, BaseMarketEvent) or isinstance(value, (InstrumentId, OrderBookLevel)):
            return {name:canonical(getattr(value,name)) for name in type(value).model_fields}
        if isinstance(value, OldMapping):
            return {key:canonical(item) for key,item in value.items()}
        if isinstance(value,(tuple,list)):
            return [canonical(item) for item in value]
        if isinstance(value,Decimal): return str(value)
        return value
    return hashlib.sha256(json.dumps(canonical(event),sort_keys=True,separators=(',',':'),
        ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()


@pytest.mark.parametrize('frozen', [False, True])
def test_mapping_import_preserves_exact_recursive_fingerprint(frozen):
    metadata = {'nested':{'list':[Decimal('1.00000000000000000000001'),
        {'unicode':'한글','zero':Decimal('-0.000')}],'tuple':(True,None,3)},
        'custom':UserDict({'proxy':MappingProxyType({'precise':Decimal('1E-29')})}),'empty':{}}
    event = IndexQuoteEvent(**fields(1),value=Decimal('123.999999999999999999999'),metadata=metadata)
    if frozen: event = freeze_market_event(event)
    assert _event_fingerprint(event) == old_reference_fingerprint(event)
    if frozen:
        assert isinstance(event.metadata,MappingProxyType)
        with pytest.raises(TypeError): event.metadata['new'] = 1
    native = book(2,1,BookUpdateType.SNAPSHOT,None,100)
    if frozen: native = freeze_market_event(native)
    assert _event_fingerprint(native) == old_reference_fingerprint(native)
