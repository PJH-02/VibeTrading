import asyncio
import json
from decimal import Decimal

import pytest

from runtime.bus import InMemoryEventBus
from market.hot_path import HotPathStore
from core.models import BookUpdateType, Venue
from feeds.providers import create_perp_collector
from feeds.providers.perp_common import _instrument


async def snapshot():
    bus = InMemoryEventBus()
    collector = create_perp_collector(Venue.BINANCE, ('SOLUSDT',))
    await collector._book(bus, 'SOLUSDT', None, [['100', '2']], [['101', '3']],
                          BookUpdateType.SNAPSHOT, 100, channel='depth1000_rest_snapshot')
    return collector, bus, {'SOLUSDT': 100}


def depth(first, last, previous):
    return {'e': 'depthUpdate', 's': 'SOLUSDT', 'E': 123, 'U': first, 'u': last,
            'pu': previous, 'b': [['100', '4']], 'a': []}


@pytest.mark.parametrize('bridge_last', [100, 101])
def test_snapshot_boundary_bridges_then_advances_without_false_gap(bridge_last):
    async def scenario():
        collector, bus, snapshots = await snapshot()
        old = depth(98, 99, 97)
        await collector._binance_message(bus, old, snapshots, json.dumps(old))
        assert 'SOLUSDT' not in collector.binance_ready
        bridge = depth(99, bridge_last, 98)
        raw = json.dumps(bridge)
        await collector._binance_message(bus, bridge, snapshots, raw)
        assert 'SOLUSDT' in collector.binance_ready
        assert any(m.data.get('raw_payload') == raw for m in bus.messages)
        next_frame = depth(bridge_last + 1, bridge_last + 1, bridge_last)
        await collector._binance_message(bus, next_frame, snapshots, json.dumps(next_frame))
        assert collector.last_book['SOLUSDT'] == bridge_last + 1
        assert collector.book_generation['SOLUSDT'] == 1
        assert all(m.data['event_type'] == 'order_book' for m in bus.messages)
        hot = HotPathStore()
        for m in bus.messages:
            hot.apply(m.data)
        assert hot.latest_book(_instrument(Venue.BINANCE, 'SOLUSDT').key).bids[0].quantity == Decimal('4')
    asyncio.run(scenario())


@pytest.mark.parametrize('bridge_last', [100, 101])
def test_real_previous_sequence_gap_after_bridge_invalidates_book(bridge_last):
    async def scenario():
        collector, bus, snapshots = await snapshot()
        await collector._binance_message(bus, depth(99, bridge_last, 98), snapshots)
        gap = depth(bridge_last + 1, bridge_last + 2, bridge_last - 1)
        raw = json.dumps(gap)
        with pytest.raises(RuntimeError, match='depth gap'):
            await collector._binance_message(bus, gap, snapshots, raw)
        assert bus.messages[-2].data['raw_payload'] == raw
        assert bus.messages[-2].data['sync_generation'] == 0
        assert bus.messages[-1].data['event_type'] == 'data_gap'
        assert 'SOLUSDT' not in collector.last_book
        hot = HotPathStore()
        for m in bus.messages:
            hot.apply(m.data)
        assert hot.latest_book(_instrument(Venue.BINANCE, 'SOLUSDT').key) is None
    asyncio.run(scenario())


def test_duplicate_depth_frame_is_preserved_without_regressing_live_book():
    async def scenario():
        collector, bus, snapshots = await snapshot()
        frame = depth(99, 101, 98)
        await collector._binance_message(bus, frame, snapshots, json.dumps(frame))
        raw = json.dumps(frame)
        await collector._binance_message(bus, frame, snapshots, raw)
        assert collector.last_book['SOLUSDT'] == 101
        assert collector.book_generation['SOLUSDT'] == 1
        assert bus.messages[-1].data['raw_payload'] == raw
        assert bus.messages[-1].data['sync_generation'] == 0
        assert all(m.data['event_type'] == 'order_book' for m in bus.messages)
    asyncio.run(scenario())
