"""Record exact remaining subjects/files and remove retired KIS option caches."""
import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import nats


async def main():
    root = Path('/data')
    receipt = {'ts_ns':time.time_ns()}
    files = {}
    for domain,providers in [('market_data',['kis','binance','hyperliquid']), ('crypto_market_data',['bitget-perp','bybit-perp'])]:
        for provider in providers:
            found = [str(p) for directory in (root/domain).glob(f'event_type=*/trading_date=*/provider={provider}/venue=*') for p in directory.rglob('*') if p.is_file()]
            files[provider] = found
    receipt['local_files'] = files
    receipt['legacy_exports'] = [p.name for p in (root/'exports').glob('*.tar.gz')]
    db = sqlite3.connect(root/'state/vibetrading.sqlite3',timeout=20)
    db.execute('PRAGMA busy_timeout=20000')
    targets = db.execute("SELECT universe_id,payload FROM universe_manifest WHERE provider IN ('kr_derivatives_dynamic','kis_index_derivatives_kospi200','kis_index_derivatives_kosdaq150')").fetchall()
    retired = [(key,hashlib.sha256(payload.encode()).hexdigest()) for key,payload in targets if '"option"' in payload]
    with db:
        db.executemany('DELETE FROM universe_manifest WHERE universe_id=?',[(key,) for key,_ in retired])
    receipt['removed_option_universe_caches'] = len(retired)
    receipt['removed_cache_hashes'] = retired
    db.close()
    client = await nats.connect('nats://nats:4222')
    js = client.jetstream()
    for stream in ('MARKET_DATA','CRYPTO_DATA','OPERATIONS'):
        response = await js._api_request(f'$JS.API.STREAM.INFO.{stream}',json.dumps({'subjects_filter':'>'}).encode())
        state = response['state']
        subjects = state.get('subjects',{})
        option_subjects = {k:v for k,v in subjects.items() if '.kis.option.' in k}
        for subject in option_subjects:
            await js.purge_stream(stream,subject=subject)
        receipt[stream] = {'messages':state.get('messages'),'bytes':state.get('bytes'),'option_subjects_removed':option_subjects,
            'retired_crypto_subjects':{k:v for k,v in subjects.items() if '.bitget.' in k or '.bybit.' in k},
            'subjects':subjects}
    await client.close()
    (root/'scope-migration-20261013/scope-verification.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt),flush=True)


asyncio.run(main())
