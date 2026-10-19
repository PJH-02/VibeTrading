import asyncio

import nats


async def main() -> None:
    client = await nats.connect("nats://nats:4222")
    jetstream = client.jetstream()
    before = await jetstream.stream_info("MARKET_DATA")
    before_state = (int(before.state.messages), int(before.state.bytes))
    deleted = await jetstream.delete_consumer("MARKET_DATA", "parquet-storage")
    after = await jetstream.stream_info("MARKET_DATA")
    after_state = (int(after.state.messages), int(after.state.bytes))
    print({"deleted": deleted, "before": before_state, "after": after_state})
    if before_state != after_state:
        raise RuntimeError("Deleting the durable unexpectedly changed MARKET_DATA contents")
    await client.close()


asyncio.run(main())
