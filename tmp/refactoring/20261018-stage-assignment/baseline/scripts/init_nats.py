"""
NATS JetStream Initialization Script
Creates streams and consumers for the trading system.

Usage:
    python scripts/init_nats.py
"""

import asyncio
import logging
from typing import List

import nats
from nats.js.api import StreamConfig, ConsumerConfig, AckPolicy, DeliverPolicy, RetentionPolicy

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Stream configurations
STREAMS: List[StreamConfig] = [
    StreamConfig(
        name="MARKET",
        subjects=["MARKET.CANDLES.*", "MARKET.TICKS.*"],
        retention=RetentionPolicy.LIMITS,
        max_msgs=1_000_000,
        max_bytes=1024 * 1024 * 1024,  # 1GB
        max_age=7 * 24 * 60 * 60 * 1_000_000_000,  # 7 days in nanoseconds
        storage="file",
        num_replicas=1,
        duplicate_window=60 * 1_000_000_000,  # 60 seconds
    ),
    StreamConfig(
        name="STRATEGY",
        subjects=["STRATEGY.SIGNALS.*"],
        retention=RetentionPolicy.LIMITS,
        max_msgs=100_000,
        max_bytes=256 * 1024 * 1024,  # 256MB
        max_age=7 * 24 * 60 * 60 * 1_000_000_000,
        storage="file",
        num_replicas=1,
        duplicate_window=60 * 1_000_000_000,
    ),
    StreamConfig(
        name="TRADE",
        subjects=["TRADE.ORDERS.*", "TRADE.FILLS.*"],
        retention=RetentionPolicy.LIMITS,
        max_msgs=100_000,
        max_bytes=256 * 1024 * 1024,
        max_age=30 * 24 * 60 * 60 * 1_000_000_000,  # 30 days
        storage="file",
        num_replicas=1,
        duplicate_window=60 * 1_000_000_000,
    ),
    StreamConfig(
        name="RISK",
        subjects=["RISK.ALERTS.*", "RISK.KILL_SWITCH"],
        retention=RetentionPolicy.LIMITS,
        max_msgs=10_000,
        max_bytes=64 * 1024 * 1024,
        max_age=90 * 24 * 60 * 60 * 1_000_000_000,  # 90 days
        storage="file",
        num_replicas=1,
        duplicate_window=60 * 1_000_000_000,
    ),
    StreamConfig(
        name="SYSTEM",
        subjects=["SYSTEM.HEALTH.*", "SYSTEM.COMMANDS.*"],
        retention=RetentionPolicy.LIMITS,
        max_msgs=50_000,
        max_bytes=128 * 1024 * 1024,
        max_age=7 * 24 * 60 * 60 * 1_000_000_000,
        storage="file",
        num_replicas=1,
        duplicate_window=60 * 1_000_000_000,
    ),
]

# Consumer configurations per stream
CONSUMERS = {
    "MARKET": [
        ConsumerConfig(
            durable_name="signal_gen_consumer",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.NEW,
            max_deliver=3,
            ack_wait=30 * 1_000_000_000,  # 30 seconds
        ),
    ],
    "STRATEGY": [
        ConsumerConfig(
            durable_name="execution_consumer",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.NEW,
            max_deliver=3,
            ack_wait=30 * 1_000_000_000,
        ),
    ],
    "TRADE": [
        ConsumerConfig(
            durable_name="risk_engine_consumer",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.NEW,
            max_deliver=3,
            ack_wait=30 * 1_000_000_000,
        ),
        ConsumerConfig(
            durable_name="position_tracker_consumer",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.NEW,
            max_deliver=3,
            ack_wait=30 * 1_000_000_000,
        ),
    ],
    "RISK": [
        ConsumerConfig(
            durable_name="all_services_risk_consumer",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.NEW,
            max_deliver=1,  # Kill switch should not retry
            ack_wait=5 * 1_000_000_000,
        ),
    ],
    "SYSTEM": [
        ConsumerConfig(
            durable_name="monitoring_consumer",
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.NEW,
            max_deliver=3,
            ack_wait=30 * 1_000_000_000,
        ),
    ],
}


async def init_nats(nats_url: str = "nats://localhost:4222") -> None:
    """
    Initialize NATS JetStream streams and consumers.
    Idempotent - safe to run multiple times.
    """
    logger.info(f"Connecting to NATS at {nats_url}")
    
    nc = await nats.connect(nats_url)
    js = nc.jetstream()
    
    try:
        # Create/update streams
        for stream_config in STREAMS:
            try:
                # Try to get existing stream
                stream_info = await js.stream_info(stream_config.name)
                logger.info(f"Stream '{stream_config.name}' already exists, updating...")
                await js.update_stream(config=stream_config)
            except nats.js.errors.NotFoundError:
                logger.info(f"Creating stream '{stream_config.name}'...")
                await js.add_stream(config=stream_config)
            
            logger.info(f"Stream '{stream_config.name}' configured with subjects: {stream_config.subjects}")
        
        # Create/update consumers
        for stream_name, consumers in CONSUMERS.items():
            for consumer_config in consumers:
                try:
                    await js.consumer_info(stream_name, consumer_config.durable_name)
                    logger.info(f"Consumer '{consumer_config.durable_name}' on '{stream_name}' already exists")
                except nats.js.errors.NotFoundError:
                    logger.info(f"Creating consumer '{consumer_config.durable_name}' on '{stream_name}'...")
                    await js.add_consumer(stream_name, config=consumer_config)
        
        logger.info("NATS JetStream initialization complete!")
        
        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("NATS JetStream Summary")
        logger.info("=" * 60)
        for stream_config in STREAMS:
            stream_info = await js.stream_info(stream_config.name)
            logger.info(f"Stream: {stream_config.name}")
            logger.info(f"  Subjects: {stream_config.subjects}")
            logger.info(f"  Messages: {stream_info.state.messages}")
            logger.info(f"  Bytes: {stream_info.state.bytes}")
        
    finally:
        await nc.close()


if __name__ == "__main__":
    asyncio.run(init_nats())
