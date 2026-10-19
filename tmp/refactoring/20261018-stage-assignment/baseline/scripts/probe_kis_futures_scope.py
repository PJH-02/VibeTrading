"""Resolve current KIS collection subscriptions without placing orders."""
import asyncio
import json

from vibetrading.providers.kis import KisCollector
from vibetrading.providers.kis_master import KisMasterDownloader
from collections import Counter


async def main():
    collector = KisCollector(auto_commodity=True,auto_index_derivatives=True)
    index = await collector._resolve_index_derivative_subscriptions()
    commodity = await collector._resolve_commodity_subscriptions()
    rows = sorted([{'tr_id':s.tr_id,'tr_key':s.tr_key} for s in index|commodity],key=lambda r:(r['tr_id'],r['tr_key']))
    assert not any(r['tr_id'].startswith('H0IO') for r in rows)
    master = await KisMasterDownloader().index_derivatives()
    available = Counter(c.underlying for c in master if c.asset_class.value=='future' and c.tradable)
    print(json.dumps({'subscriptions':rows,'count':len(rows),'option_subscriptions':0,'current_index_master_futures':dict(available)}))


asyncio.run(main())
