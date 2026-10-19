"""Read-only terminal audit for a disposable production-size replay.

This proves exact replay/Parquet/delivery/ACK/archive agreement, not sustained
capacity, pruning throughput, crash ordering, or absence of Market impact.
The caller must stop test writers first. Never use the production data root.
"""
from __future__ import annotations

import argparse
from array import array
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from scripts.replay_crypto_nats import TEST_NATS_URL, load_trace


class ReplayCoverage:
    """Exact membership with bounded bitmaps, avoiding millions of Python IDs."""

    def __init__(self, result, trace, inspection):
        self.run_id = result['run_id']
        if not re.fullmatch('[0-9a-f]{32}', self.run_id):
            raise RuntimeError('invalid replay run identity')
        self.rows = len(trace)
        self.multiplier = float(result['multiplier'])
        self.duration_ns = int(float(result['duration_seconds']) * 10**9)
        if self.rows <= 0 or self.multiplier <= 0 or not 0 < self.duration_ns <= 1800 * 10**9:
            raise RuntimeError('invalid bounded replay duration/rate')
        self.cycle_ns = int(inspection['trace_duration_ns'] / self.multiplier)
        if self.cycle_ns <= 0:
            raise RuntimeError('invalid replay cycle')
        self.offsets = [None] * self.rows
        for offset, ordinal, _ in trace:
            if not 0 <= ordinal < self.rows or self.offsets[ordinal] is not None:
                raise RuntimeError('trace ordinal coverage mismatch')
            self.offsets[ordinal] = int(offset / self.multiplier)
        cycles = (self.duration_ns + self.cycle_ns - 1) // self.cycle_ns
        positions = cycles * self.rows
        if positions > 16_000_000:
            raise RuntimeError('replay coverage memory budget exceeded')
        self.expected = sum(max(0, (self.duration_ns - 1 - offset) // self.cycle_ns + 1)
                            for offset in self.offsets)
        if (result.get('kind') != 'complete' or result.get('publish_errors') != 0
                or result.get('scheduled') != self.expected or result.get('sent') != self.expected
                or result.get('last_stream_sequence') != self.expected
                or result.get('max_schedule_lag_ms', float('inf')) > 5000
                or result.get('max_publish_ms', float('inf')) > 5000
                or result.get('trace_sha256') != inspection['trace_sha256']):
            raise RuntimeError('feeder did not prove the planned replay was fully published')
        self.events = bytearray((positions + 7) // 8)
        self.sequences = array('I', [0]) * self.expected
        self.references = array('Q', [0]) * self.expected
        self.count = 0

    def add(self, event_id, stream, sequence, manifest_rowid):
        match = re.fullmatch(r'replay:([0-9a-f]{32}):(\d+):(\d+)', str(event_id))
        if match is None or match[1] != self.run_id or stream != 'CRYPTO_DATA':
            raise RuntimeError('unexpected canonical event/stream in replay output')
        cycle, ordinal = int(match[2]), int(match[3])
        if (ordinal >= self.rows or cycle * self.cycle_ns + self.offsets[ordinal] >= self.duration_ns
                or type(sequence) is not int or not 1 <= sequence <= self.expected
                or type(manifest_rowid) is not int or manifest_rowid <= 0):
            raise RuntimeError('event/sequence lies outside the planned replay')
        rank = cycle * self.rows + ordinal
        mask = 1 << (rank % 8)
        if self.events[rank // 8] & mask or self.sequences[sequence - 1]:
            raise RuntimeError('duplicate canonical event or NATS delivery sequence')
        self.events[rank // 8] |= mask
        self.sequences[sequence - 1] = rank + 1
        self.references[sequence - 1] = manifest_rowid
        self.count += 1

    def verify_ledger(self, rows, canonical_reference):
        count = 0
        for sequence, key, file_ref in rows:
            if not 1 <= sequence <= self.expected:
                raise RuntimeError('ledger sequence outside replay')
            code = self.sequences[sequence - 1]
            if not code:
                raise RuntimeError('delivery has no canonical Parquet record')
            cycle, ordinal = divmod(code - 1, self.rows)
            event_id = f'replay:{self.run_id}:{cycle}:{ordinal}'
            if bytes(key) != hashlib.sha256(event_id.encode()).digest():
                raise RuntimeError('delivery event key differs from canonical Parquet')
            if canonical_reference(file_ref) != self.references[sequence - 1]:
                raise RuntimeError('delivery manifest reference differs from canonical Parquet')
            count += 1
        if count != self.expected or self.count != self.expected:
            raise RuntimeError('missing canonical events or exact delivery records')


def file_identity(path):
    return {str(p): [p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino]
            for p in (path, Path(str(path) + '-wal')) if p.exists()}


def audit_catalogue(conn, coverage, baseline_manifest_rowid, receipt, backend, root, prefix, reserve_bytes):
    import pyarrow.parquet as pq

    streams = conn.execute('SELECT stream_id FROM delivery_stream WHERE stream_name=? AND stream_generation=?',
                           ('CRYPTO_DATA', receipt.generation)).fetchall()
    if len(streams) != 1:
        raise RuntimeError('replay stream generation missing/ambiguous in catalogue')
    files = remote_bytes = 0
    manifests = conn.execute('''SELECT rowid AS manifest_rowid,* FROM parquet_manifest WHERE rowid>? AND lifecycle_state='active'
                               ORDER BY rowid''', (baseline_manifest_rowid,))
    # One remote file at a time, beneath the disposable root. No local archive
    # reclamation or DB/checkpoint mutation; temporary download is cleaned up.
    with tempfile.TemporaryDirectory(prefix='integrity-', dir=root) as temporary:
        path = Path(temporary) / 'remote.parquet'
        sidecar = Path(temporary) / 'remote.manifest.json'
        for row in manifests:
            if (row['upload_status'] != 'remote_verified' or not row['verified_ts_ns']
                    or not str(row['remote_key'] or '').startswith(prefix + '/')
                    or not str(row['remote_manifest_key'] or '').startswith(prefix + '/')
                    or not re.fullmatch('[0-9a-f]{64}', str(row['file_sha256'] or ''))):
                raise RuntimeError('canonical Parquet lacks verified isolated remote archive')
            if shutil.disk_usage(root).free < reserve_bytes + row['byte_size'] + 1024**2:
                raise RuntimeError('verification scratch would violate protected reserve')
            backend.download_file(row['remote_manifest_key'], sidecar)
            if sidecar.stat().st_size > 1024**2:
                raise RuntimeError('remote manifest size exceeds bound')
            document = json.loads(sidecar.read_text())
            expected_document = {'file_id': row['file_id'], 'key': row['remote_key'], 'bytes': row['byte_size'],
                                 'sha256': row['file_sha256'], 'rows': row['rows']}
            if any(document.get(key) != value for key, value in expected_document.items()):
                raise RuntimeError('remote manifest differs from catalogue')
            sidecar.unlink()
            backend.download_file(row['remote_key'], path)
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if path.stat().st_size != row['byte_size'] or digest.hexdigest() != row['file_sha256']:
                raise RuntimeError('remote Parquet full checksum/length mismatch')
            parquet = pq.ParquetFile(path)
            count = 0
            try:
                for batch in parquet.iter_batches(batch_size=2048, columns=['event_id', 'nats_stream', 'nats_stream_sequence']):
                    columns = batch.to_pydict()
                    for event, stream, sequence in zip(columns['event_id'], columns['nats_stream'], columns['nats_stream_sequence']):
                        coverage.add(event, stream, sequence, row['manifest_rowid'])
                        count += 1
            finally:
                parquet.close()
            if count != row['rows']:
                raise RuntimeError('manifest row count differs from remote Parquet')
            files += 1
            remote_bytes += path.stat().st_size
            path.unlink()
            if files % 100 == 0:
                print(json.dumps({'phase': 'terminal_remote_exact_audit', 'verified_files': files,
                                  'verified_rows': coverage.count, 'remote_bytes': remote_bytes}), flush=True)
    # Unique PK(stream_id, sequence) plus exact event-key comparison: this is
    # actual row evidence, not an inferred ACK range or pruning authorization.
    references = {}

    def canonical_reference(reference):
        if reference in references:
            return references[reference]
        origin = reference
        seen = set()
        for _ in range(64):
            row = conn.execute('SELECT rowid,lifecycle_state,superseded_by FROM parquet_manifest WHERE rowid=?',
                               (reference,)).fetchone()
            if row is None or reference in seen:
                raise RuntimeError('delivery points to missing/cyclic canonical manifest')
            seen.add(reference)
            if row['lifecycle_state'] == 'active':
                references[origin] = reference
                return reference
            if row['lifecycle_state'] != 'superseded' or not row['superseded_by']:
                break
            replacement = conn.execute('SELECT rowid FROM parquet_manifest WHERE file_id=?', (row['superseded_by'],)).fetchone()
            if replacement is None:
                break
            reference = replacement[0]
        raise RuntimeError('unresolved delivery compaction chain')

    coverage.verify_ledger(conn.execute('''SELECT stream_sequence,event_key,file_rowid FROM parquet_delivery_compact
                                          WHERE stream_id=? ORDER BY stream_sequence''', (streams[0][0],)), canonical_reference)
    return {'canonical_files': files, 'canonical_rows': coverage.count,
            'exact_delivery_rows': coverage.expected, 'remote_full_sha_verified_bytes': remote_bytes}


async def main(args):
    from vibetrading.bus import NatsJetStreamBus
    from vibetrading.settings import RuntimeSettings
    from vibetrading.storage_backend import archive_backend

    root = args.root.resolve(strict=True)
    if 'r2-load-gate' not in root.parts or root.name == 'r2-load-gate':
        raise RuntimeError('explicit disposable r2-load-gate child required')
    settings = RuntimeSettings.from_env(args.env)
    prefix = settings.archive.prefix
    database = (settings.state_dir / 'vibetrading.sqlite3').resolve(strict=True)
    if (settings.nats.url != TEST_NATS_URL or not database.is_relative_to(root)
            or settings.data_root.resolve() != root or args.baseline_manifest_rowid < 0
            or not prefix.startswith('vibetrading/crypto/qualification/')
            or settings.archive.backend != 'ncloud_storage' or args.reserve_bytes < 4 * 1024**3):
        raise RuntimeError('isolated NATS/catalogue/Ncloud qualification configuration required')
    if args.result.exists():
        raise RuntimeError('audit receipt exists; inspect rather than rerun completed verification')
    feeder = json.loads(args.feeder_result.read_text())
    trace, inspection = load_trace(args.input, args.report)
    if not inspection['contains_observed_one_second_peak']:
        raise RuntimeError('replay capture lacks the observed peak burst')
    coverage = ReplayCoverage(feeder, trace, inspection)
    del trace
    bus = NatsJetStreamBus(settings)
    await bus.connect(initialize=False)
    try:
        receipt = await bus.confirmed_ack_floor('CRYPTO_DATA', 'crypto-parquet-storage', 'crypto.>')
        info = await bus._js.consumer_info('CRYPTO_DATA', 'crypto-parquet-storage')
        stream = await bus._js.stream_info('CRYPTO_DATA')
        if (receipt.sequence != coverage.expected or stream.state.last_seq != coverage.expected
                or info.num_pending or info.num_ack_pending or stream.state.messages):
            raise RuntimeError('actual NATS replay has not drained/ACKed exactly the planned input')
        before = file_identity(database)
        conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('PRAGMA cache_size=-65536')
            audit = await asyncio.to_thread(audit_catalogue, conn, coverage, args.baseline_manifest_rowid,
                                           receipt, archive_backend(settings), root, prefix, args.reserve_bytes)
        finally:
            conn.close()
        after_receipt = await bus.confirmed_ack_floor('CRYPTO_DATA', 'crypto-parquet-storage', 'crypto.>')
        after_stream = await bus._js.stream_info('CRYPTO_DATA')
        if (before != file_identity(database) or after_receipt.generation != receipt.generation
                or after_receipt.consumer_generation != receipt.consumer_generation
                or after_stream.state.last_seq != coverage.expected):
            raise RuntimeError('test writers/stream incarnation changed during terminal audit')
        result = {**audit, 'run_id': coverage.run_id, 'stream_generation': receipt.generation,
                  'consumer_generation': receipt.consumer_generation, 'server_ack_floor': receipt.sequence,
                  'storage_ack_archive_integrity_verified': True,
                  'two_times_30_minutes_input': feeder['multiplier'] == 2 and feeder['duration_seconds'] == 1800,
                  'whole_load_qualification_passed': False,
                  'not_proven_by_this_audit': ['in-run backlog trend', 'RSS/OOM and disk pressure',
                      'prune/retirement service throughput', 'Market impact', 'commit-before-ACK crash ordering']}
        with args.result.open('x') as handle:
            handle.write(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    finally:
        await bus.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('env', 'root', 'input', 'report', 'feeder-result', 'result'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--baseline-manifest-rowid', type=int, required=True)
    parser.add_argument('--reserve-bytes', type=int, default=4 * 1024**3)
    asyncio.run(main(parser.parse_args()))
