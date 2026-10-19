"""Capture the busiest receive minute plus the observed one-second burst.

Selection is an offline manifest export, never a live SQLite query. Reads are
paced and yield to host I/O pressure. Output contains public market records,
not credentials; use only a disposable, isolated NATS instance for replay.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import pyarrow.parquet as pq


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('capture already exists')
    entries = json.loads(args.manifest.read_text())
    allowed = Path('/data/crypto_market_data').resolve()
    counts = Counter()
    provider_counts = Counter()
    file_windows = []
    missing, unknown_timestamps = [], 0
    started = time.monotonic()
    last_report = started

    def yield_io():
        nonlocal last_report
        if time.monotonic() - started > 1200:
            raise RuntimeError('bounded capture time exceeded')
        pressure_path = Path('/proc/pressure/io')
        while pressure_path.exists():
            line = next(s for s in pressure_path.read_text().splitlines() if s.startswith('full '))
            pressure = float(dict(x.split('=', 1) for x in line.split()[1:])['avg10'])
            if pressure <= 5:
                break
            if time.monotonic() - started > 1200:
                raise RuntimeError('I/O pressure did not recover')
            time.sleep(1)
        time.sleep(.02)
        if time.monotonic() - last_report >= 20:
            print(json.dumps({'phase': 'receive_timestamp_histogram', 'files': len(file_windows),
                              'rows': sum(provider_counts.values())}), flush=True)
            last_report = time.monotonic()

    for entry in entries:
        path = Path(entry['path']).resolve()
        if not path.is_relative_to(allowed):
            raise RuntimeError('unexpected manifest path')
        if not path.exists():
            missing.append(str(path))
            continue
        if path.stat().st_size != entry['byte_size']:
            raise RuntimeError('manifest file size mismatch')
        yield_io()
        seconds = set()
        for batch in pq.ParquetFile(path).iter_batches(columns=['receive_ts_ns'], batch_size=8192):
            for ts in batch.column(0).to_pylist():
                if not ts or ts <= 0:
                    unknown_timestamps += 1
                    continue
                second = ts // 1_000_000_000
                counts[second] += 1
                provider_counts[entry['provider']] += 1
                seconds.add(second)
        if seconds:
            file_windows.append((entry, min(seconds), max(seconds)))
    if not counts:
        raise RuntimeError('no valid receive timestamps')
    first, last = min(counts), max(counts)
    current = sum(counts[s] for s in range(first, first + 60))
    best_start, best_count = first, current
    for start in range(first + 1, last + 1):
        current += counts[start + 59] - counts[start - 1]
        if current > best_count:
            best_start, best_count = start, current
    peak_second = max(counts, key=counts.get)
    segments = [{'start_ns': best_start * 10**9, 'duration_ns': 60 * 10**9, 'offset_ns': 0}]
    if not best_start <= peak_second < best_start + 60:
        segments.append({'start_ns': peak_second * 10**9, 'duration_ns': 10**9, 'offset_ns': 60 * 10**9})
    wanted_seconds = {second for segment in segments
                      for second in range(segment['start_ns'] // 10**9,
                                          (segment['start_ns'] + segment['duration_ns']) // 10**9)}
    wanted_count = sum(counts[second] for second in wanted_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Persist the small histogram so future trace refinements never need to
    # repeat the timestamp column scan solely to locate another interval.
    args.output.with_suffix('.histogram.json').write_text(json.dumps(dict(sorted(counts.items()))) + '\n')
    mix = Counter()
    size = rows = 0
    digest = hashlib.sha256()
    selected = []
    with args.output.open('xb') as output:
        for entry, lo, hi in file_windows:
            if not any(lo <= second <= hi for second in wanted_seconds):
                continue
            yield_io()
            selected.append(entry['path'])
            for batch in pq.ParquetFile(entry['path']).iter_batches(batch_size=1024):
                for row in batch.to_pylist():
                    second = (row.get('receive_ts_ns') or 0) // 1_000_000_000
                    if second not in wanted_seconds:
                        continue
                    # Undo the storage-only flattening so replay is the original
                    # normalized bus format, with its original book payload.
                    row['instrument'] = {k[len('instrument_'):]: row.pop(k)
                                         for k in list(row) if k.startswith('instrument_')}
                    row.pop('nats_stream', None)
                    row.pop('nats_stream_sequence', None)
                    line = (json.dumps(row, separators=(',', ':'), default=str) + '\n').encode()
                    output.write(line)
                    digest.update(line)
                    rows += 1
                    size += len(line)
                    mix[(row['provider'], row['event_type'], row['instrument']['raw_symbol'])] += 1
    if rows != wanted_count:
        raise RuntimeError('capture row set changed between timestamp and payload passes')
    report = {'observed_files': len(file_windows), 'missing_files': len(missing),
              'unknown_receive_timestamps': unknown_timestamps,
              'observed_rows': sum(provider_counts.values()), 'provider_rows': dict(provider_counts),
              'observation_first_second': first, 'observation_last_second': last,
              'peak_one_second_events': max(counts.values()),
              'peak_one_second_start': peak_second,
              'peak_receive_minute_start': best_start, 'peak_receive_minute_events': best_count,
              'segments': segments, 'trace_duration_ns': sum(segment['duration_ns'] for segment in segments),
              'capture_rows': rows, 'capture_bytes': size, 'capture_sha256': digest.hexdigest(),
              'capture_mix': {':'.join(k): v for k, v in sorted(mix.items())},
              'selected_files': selected,
              'scope': 'available committed Parquet receive timestamps; not all exchange ingress history',
              'elapsed_seconds': round(time.monotonic() - started, 1)}
    args.output.with_suffix('.report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'selected_files'}), flush=True)


if __name__ == '__main__':
    main()
