from __future__ import annotations

import argparse
import json
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description="Print compact NATS crypto stream status")
    parser.add_argument("--url", default="http://127.0.0.1:8222/jsz?accounts=true&streams=true&consumers=true&config=true")
    parser.add_argument("--stream", default="CRYPTO_DATA")
    args = parser.parse_args()

    with urlopen(args.url, timeout=5) as response:
        payload = json.load(response)

    details = list(payload.get("stream_detail") or [])
    for account in payload.get("account_details") or []:
        details.extend(account.get("stream_detail") or [])
    stream = next(
        (
            item
            for item in details
            if (item.get("name") or (item.get("config") or {}).get("name"))
            == args.stream
        ),
        None,
    )
    if stream is None:
        raise SystemExit(f"stream {args.stream!r} not found")
    state = stream.get("state") or {}
    consumers = []
    for item in stream.get("consumer_detail") or []:
        delivered = item.get("delivered") or {}
        ack_floor = item.get("ack_floor") or {}
        consumers.append(
            {
                "name": item.get("name") or (item.get("config") or {}).get("durable_name"),
                "num_pending": item.get("num_pending"),
                "num_ack_pending": item.get("num_ack_pending"),
                "num_redelivered": item.get("num_redelivered"),
                "delivered_stream_seq": delivered.get("stream_seq"),
                "ack_floor_stream_seq": ack_floor.get("stream_seq"),
            }
        )
    print(
        json.dumps(
            {
                "stream": args.stream,
                "created": stream.get("created"),
                "messages": state.get("messages"),
                "bytes": state.get("bytes"),
                "first_seq": state.get("first_seq"),
                "last_seq": state.get("last_seq"),
                "consumers": consumers,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
