"""Archive and purge exact retired-venue operational data-gap subjects."""
import asyncio
import hashlib
import json
import time
from pathlib import Path

import nats

from vibetrading.settings import get_settings
from storage.storage_backend import archive_backend


async def main():
    settings = get_settings()
    backend = archive_backend(settings)
    client = await nats.connect('nats://nats:4222')
    js = client.jetstream()
    response = await js._api_request('$JS.API.STREAM.INFO.OPERATIONS',json.dumps({'subjects_filter':'crypto.data_gap.>'}).encode())
    state = response['state']
    last = state['last_seq']
    subjects = {k:v for k,v in state.get('subjects',{}).items() if '.bitget.' in k or '.bybit.' in k}
    lines = []
    for subject,count in subjects.items():
        sequence = state['first_seq']
        found = 0
        while sequence <= last and found < count:
            message = await js.get_msg('OPERATIONS',seq=sequence,subject=subject,next=True)
            if message.seq > last:
                break
            assert message.subject == subject
            lines.append(json.dumps({'stream':'OPERATIONS','sequence':message.seq,'subject':subject,'event':json.loads(message.data)},separators=(',',':')))
            found += 1
            sequence = message.seq+1
        assert found==count
    payload = ('\n'.join(lines)+'\n').encode()
    sha = hashlib.sha256(payload).hexdigest()
    key = f'vibetrading/retired-20261013/nats/OPERATIONS-{sha}.jsonl'
    if lines:
        backend.put_bytes(payload,key,sha256=sha)
        for subject in subjects:
            await js.purge_stream('OPERATIONS',subject=subject)
    receipt = {'remote_key':key,'sha256':sha,'events':len(lines),'bytes':len(payload),'subjects':subjects,'verified_ns':time.time_ns()}
    (settings.data_root/'scope-migration-20261013/retired-nats-gaps.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt),flush=True)
    await client.close()
    backend.close()


asyncio.run(main())
