"""Bounded destructive retention probe ONLY in the completed disposable trial.

Production is never mounted. A one-second test retention makes real, verified
replay records eligible without falsifying timestamps or ACK/archive receipts.
It does not verify the production 24h scheduling policy or concurrent capacity.
"""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import statistics
import time


async def main(args):
    from vibetrading.bus import NatsJetStreamBus
    from vibetrading.settings import RuntimeSettings
    from storage.sqlite_store import RuntimeStore
    import psutil
    root = args.root.resolve(strict=True)
    proof = json.loads((root / 'evidence/integrity.json').read_text())
    settings = RuntimeSettings.from_env(root / 'evidence/trial.env')
    if ('r2-load-gate' not in root.parts or root.name != 'fullscale-20261001'
            or settings.data_root.resolve() != root or settings.state_dir.resolve() != root / 'state'
            or settings.nats.url != 'nats://r2-load-nats:4222'
            or proof.get('storage_ack_archive_integrity_verified') is not True):
        raise RuntimeError('completed disposable trial and exact integrity proof required')
    if not args.label.replace('-', '').isalnum():
        raise RuntimeError('invalid distinct probe evidence label')
    result_path = root / f'evidence/prune-capacity-{args.label}.json'
    if result_path.exists():
        raise RuntimeError('probe receipt exists; inspect it rather than repeat completed mutations')
    bus = NatsJetStreamBus(settings)
    await bus.connect(initialize=False)
    store = None
    try:
        receipt = await bus.confirmed_ack_floor('CRYPTO_DATA', 'crypto-parquet-storage', 'crypto.>')
        info = await bus._js.stream_info('CRYPTO_DATA')
        if (info.state.messages or info.state.last_seq != proof['server_ack_floor']
                or receipt.sequence != proof['server_ack_floor'] or receipt.generation != proof['stream_generation']):
            raise RuntimeError('trial was changed or no longer drained')
        store = RuntimeStore(settings)
        store.record_confirmed_ack_floor(receipt)
        process = psutil.Process()
        before = process.io_counters()
        started = time.monotonic()
        samples = []
        for _ in range(args.batches):
            if time.monotonic() - started > 300:
                break
            if shutil.disk_usage(root).free < 4 * 1024**3:
                raise RuntimeError('protected reserve reached')
            full = next(line for line in Path('/proc/pressure/io').read_text().splitlines() if line.startswith('full '))
            pressure = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
            last_report = time.monotonic()
            while pressure > 5:
                if time.monotonic() - started > 300 or shutil.disk_usage(root).free < 4 * 1024**3:
                    raise RuntimeError('prune probe pressure/reserve deadline exceeded')
                await asyncio.sleep(1)
                full = next(line for line in Path('/proc/pressure/io').read_text().splitlines() if line.startswith('full '))
                pressure = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
                if time.monotonic() - last_report > 20:
                    print(json.dumps({'phase': 'yield_to_io_pressure', 'io_full_avg10': pressure}), flush=True)
                    last_report = time.monotonic()
            begin = time.monotonic()
            io_before = process.io_counters()
            removed = store.prune_delivery_history(retain_seconds=args.retention_seconds, limit=args.limit)
            io_after = process.io_counters()
            sample = {'deleted': removed, 'seconds': time.monotonic() - begin, 'io_full_avg10_before': pressure,
                      'physical_read_bytes': io_after.read_bytes - io_before.read_bytes,
                      'logical_read_bytes': io_after.read_chars - io_before.read_chars}
            samples.append(sample)
            print(json.dumps(sample), flush=True)
        after = process.io_counters()
        successful = [s for s in samples if s['deleted']]
        result = {'test_only_retention_seconds': args.retention_seconds, 'production_retention_changed': False,
                  'trial_catalogue_mutated_after_completed_integrity_audit': True,
                  'batch_limit': args.limit, 'batches': samples, 'deleted': sum(s['deleted'] for s in samples),
                  'elapsed_seconds': time.monotonic() - started,
                  'process_physical_read_bytes': after.read_bytes - before.read_bytes,
                  'process_physical_write_bytes': after.write_bytes - before.write_bytes,
                  'successful_batch_seconds_median': statistics.median(s['seconds'] for s in successful) if successful else None,
                  'server_ack_floor': receipt.sequence, 'stream_generation': receipt.generation,
                  'whole_load_qualification_passed': False, 'concurrent_prune_capacity_verified': False}
        with result_path.open('x') as handle:
            json.dump(result, handle, indent=2)
        print(json.dumps(result), flush=True)
    finally:
        if store is not None:
            store.close()
        await bus.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--limit', type=int, choices=(8192, 32768, 65536), default=8192)
    parser.add_argument('--batches', type=int, choices=range(1, 49), default=16)
    parser.add_argument('--retention-seconds', type=int, choices=(1, 86400), default=1)
    asyncio.run(main(parser.parse_args()))
