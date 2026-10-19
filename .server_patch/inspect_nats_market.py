import asyncio
import json
from collections import Counter

import nats


async def main() -> None:
    client = await nats.connect("nats://nats:4222")
    jetstream = client.jetstream()
    info = await jetstream.stream_info("MARKET_DATA")
    first = int(info.state.first_seq)
    last = int(info.state.last_seq)
    subjects = Counter()
    event_types = Counter()
    providers = Counter()
    hyperliquid_types = Counter()
    hyperliquid_symbols = Counter()
    hyperliquid_samples = []
    missing = 0
    for sequence in range(first, last + 1):
        try:
            message = await jetstream.get_msg("MARKET_DATA", seq=sequence)
        except Exception:
            missing += 1
            continue
        body = json.loads(message.data)
        subjects[message.subject] += 1
        event_types[str(body.get("event_type"))] += 1
        provider = str(body.get("provider"))
        providers[provider] += 1
        if provider == "hyperliquid":
            event_type = str(body.get("event_type"))
            symbol = str((body.get("instrument") or {}).get("std_symbol"))
            hyperliquid_types[event_type] += 1
            hyperliquid_symbols[symbol] += 1
            if len(hyperliquid_samples) < 8:
                hyperliquid_samples.append({
                    "sequence": sequence,
                    "subject": message.subject,
                    "event_type": event_type,
                    "symbol": symbol,
                    "source_ts_ns": body.get("source_ts_ns"),
                    "funding_rate": body.get("funding_rate"),
                })
    print({
        "range": [first, last],
        "missing_or_deleted": missing,
        "subjects": dict(subjects),
        "event_types": dict(event_types),
        "providers": dict(providers),
        "hyperliquid_types": dict(hyperliquid_types),
        "hyperliquid_symbols": dict(hyperliquid_symbols),
        "hyperliquid_samples": hyperliquid_samples,
    })
    await client.close()


asyncio.run(main())
