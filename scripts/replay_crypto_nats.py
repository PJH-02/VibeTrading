"""Replay captured receive timing into a disposable real NATS only.

Reads raw lines into a bounded trace, retaining book message sizes. Each cycle
uses unique event IDs and current timestamps so deduplication cannot turn a
throughput test into a cheap replay of already committed events. This feeds the
storage path, not exchange network clients. Run --inspect-only before staging.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
import uuid


MAX_TRACE_BYTES = 160 * 1024 * 1024
TEST_NATS_URL = 'nats://r2-load-nats:4222'


def load_trace(path: Path, report_path: Path):
    report = json.loads(report_path.read_text())
    if path.stat().st_size > MAX_TRACE_BYTES:
        raise RuntimeError('trace exceeds bounded feeder memory budget')
    digest = hashlib.sha256()
    trace = []
    seconds = Counter()
    mix = Counter()
    first_ns = int(report['peak_receive_minute_start']) * 10**9
    segments = report.get('segments', [{'start_ns': first_ns, 'duration_ns': 60 * 10**9, 'offset_ns': 0}])
    with path.open('rb') as source:
        for ordinal, line in enumerate(source):
            digest.update(line)
            row = json.loads(line)
            ts = int(row['receive_ts_ns'])
            segment = next((item for item in segments
                            if item['start_ns'] <= ts < item['start_ns'] + item['duration_ns']), None)
            if segment is None:
                raise RuntimeError('capture timestamp outside documented segments')
            offset = ts - segment['start_ns'] + segment['offset_ns']
            instrument = row['instrument']
            if instrument['asset_class'] != 'crypto_perp':
                raise RuntimeError('non-perpetual record in capture')
            if row['provider'] not in {'binance-perp', 'bitget-perp', 'bybit-perp'}:
                raise RuntimeError('unknown capture provider')
            trace.append((offset, ordinal, line))
            seconds[offset // 10**9] += 1
            mix[(row['provider'], row['event_type'], instrument['raw_symbol'])] += 1
    if digest.hexdigest() != report['capture_sha256'] or len(trace) != report['capture_rows']:
        raise RuntimeError('capture checksum/count mismatch')
    trace.sort(key=lambda entry: (entry[0], entry[1]))
    for provider in ('binance-perp', 'bitget-perp', 'bybit-perp'):
        for symbol in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT'):
            for typ in ('trade', 'order_book'):
                if not mix[(provider, typ, symbol)]:
                    raise RuntimeError('capture misses a required trade/book partition')
    peak = max(seconds.values())
    inspection = {'trace_sha256': digest.hexdigest(), 'trace_rows': len(trace),
                  'trace_bytes': path.stat().st_size, 'trace_peak_one_second_events': peak,
                  'observed_peak_one_second_events': report['peak_one_second_events'],
                  'contains_observed_one_second_peak': peak >= report['peak_one_second_events'],
                  'trace_duration_ns': report.get('trace_duration_ns', 60 * 10**9),
                  'all_nine_trade_and_book_partitions': True}
    return trace, inspection


async def replay(args, trace, inspection):
    from runtime.bus import NatsJetStreamBus, dumps
    from runtime.settings import RuntimeSettings
    settings = RuntimeSettings.from_env(args.env)
    if settings.nats.url != TEST_NATS_URL:
        raise RuntimeError('only disposable r2-load-nats endpoint is allowed')
    if args.multiplier <= 0 or not 0 < args.duration_seconds <= 1800:
        raise RuntimeError('invalid multiplier or bounded duration')
    if not inspection['contains_observed_one_second_peak']:
        raise RuntimeError('capture misses observed burst; extend trace before qualification')
    bus = NatsJetStreamBus(settings)
    await bus.connect(initialize=True)
    initial = await bus._js.stream_info('CRYPTO_DATA')
    if initial.state.last_seq or initial.state.messages:
        await bus.close()
        raise RuntimeError('test stream is not pristine; do not reuse a prior test')
    run_id = uuid.uuid4().hex
    cycle_ns = int(inspection['trace_duration_ns'] / args.multiplier)
    # Give the concurrently launched storage process time to subscribe. Its
    # readiness must be checked by the harness before this feeder is invoked.
    start_ns = time.monotonic_ns() + 2 * 10**9
    wall_ns = time.time_ns() + 2 * 10**9
    end_ns = start_ns + int(args.duration_seconds * 10**9)
    queue = asyncio.Queue(maxsize=256)
    stats = {'sent': 0, 'bytes': 0, 'max_schedule_lag_ms': 0,
             'max_publish_ms': 0, 'publish_errors': 0}
    stop = asyncio.Event()

    async def publisher():
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                cycle, offset_ns, ordinal, line = item
                row = json.loads(line)
                event_id = f'replay:{run_id}:{cycle}:{ordinal}'
                due_wall = wall_ns + cycle * cycle_ns + offset_ns
                row.update(event_id=event_id, source_ts_ns=due_wall,
                           receive_ts_ns=due_wall, wallclock_ts_ns=due_wall,
                           collector_run_id=run_id, connection_id='isolated-replay')
                payload = dumps(row)
                instrument = row['instrument']
                subject = '.'.join(('crypto', row['event_type'], row['venue'],
                                    instrument['asset_class'], instrument['std_symbol']))
                before = time.monotonic_ns()
                await bus._js.publish(subject, payload, headers={'Nats-Msg-Id': event_id}, timeout=5)
                stats['max_publish_ms'] = max(stats['max_publish_ms'], (time.monotonic_ns()-before)/10**6)
                stats['sent'] += 1
                stats['bytes'] += len(payload)
            except Exception:
                stats['publish_errors'] += 1
                stop.set()
                raise
            finally:
                queue.task_done()

    workers = [asyncio.create_task(publisher()) for _ in range(32)]
    last_report = time.monotonic()
    scheduled = 0
    try:
        cycle = 0
        while start_ns + cycle * cycle_ns < end_ns:
            for offset, ordinal, line in trace:
                shifted = int(offset / args.multiplier)
                due_ns = start_ns + cycle * cycle_ns + shifted
                if due_ns >= end_ns:
                    break
                wait = (due_ns - time.monotonic_ns()) / 10**9
                if wait > 0:
                    await asyncio.sleep(wait)
                if stop.is_set():
                    raise RuntimeError('NATS publisher failed')
                lag = max(0, (time.monotonic_ns() - due_ns)/10**6)
                stats['max_schedule_lag_ms'] = max(stats['max_schedule_lag_ms'], lag)
                if lag > 5000:
                    raise RuntimeError('feeder cannot sustain requested timing; invalid throughput gate')
                await asyncio.wait_for(queue.put((cycle, shifted, ordinal, line)), timeout=5)
                scheduled += 1
                if time.monotonic() - last_report >= 10:
                    print(json.dumps({'kind': 'progress', 'run_id': run_id, 'scheduled': scheduled,
                                      'queued': queue.qsize(), **stats}), flush=True)
                    last_report = time.monotonic()
            cycle += 1
        await asyncio.wait_for(queue.join(), timeout=30)
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)
        await bus._nc.flush()
        if stats['sent'] != scheduled or stats['publish_errors']:
            raise RuntimeError('scheduled input was not fully published')
        stream = await bus._js.stream_info('CRYPTO_DATA')
        result = {'kind': 'complete', 'run_id': run_id, 'scheduled': scheduled, **stats,
                  'duration_seconds': args.duration_seconds, 'multiplier': args.multiplier,
                  'last_stream_sequence': stream.state.last_seq, **inspection,
                  'storage_ack_archive_gate_passed': False}
        print(json.dumps(result), flush=True)
        args.result.write_text(json.dumps(result, indent=2) + '\n')
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await bus.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--inspect-only', action='store_true')
    parser.add_argument('--env', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--duration-seconds', type=float, default=1800)
    parser.add_argument('--multiplier', type=float, default=2)
    args = parser.parse_args()
    trace, inspection = load_trace(args.input, args.report)
    if args.inspect_only:
        print(json.dumps(inspection), flush=True)
    else:
        if not args.env or not args.result or args.result.exists():
            raise RuntimeError('explicit test env and unused result path required')
        asyncio.run(replay(args, trace, inspection))


if __name__ == '__main__':
    main()
