"""Summarize observer evidence without claiming a qualification pass."""
import argparse
import json
import math
from pathlib import Path
import statistics


def summarize(path):
    if path.stat().st_size > 16 * 1024**2:
        raise RuntimeError('observer evidence exceeds bounded size')
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not 2 <= len(rows) <= 1000:
        raise RuntimeError('insufficient/excessive observer samples')
    times = [r['elapsed_seconds'] for r in rows]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise RuntimeError('observer timestamps are not strictly increasing')
    streams = [next(s for s in r['trial_streams'] if s['name'] == 'CRYPTO_DATA') for r in rows]
    pending = [s['state']['messages'] for s in streams]
    latest = [s['state']['last_seq'] for s in streams]
    accepted_acks = [seq - count for seq, count in zip(latest, pending)]
    duration = times[-1] - times[0]
    half = [i for i, t in enumerate(times) if t >= (times[0] + times[-1]) / 2]
    mean_t = statistics.mean(times[i] for i in half)
    mean_n = statistics.mean(pending[i] for i in half)
    denom = sum((times[i] - mean_t)**2 for i in half)
    slope = sum((times[i] - mean_t) * (pending[i] - mean_n) for i in half) / denom if denom else None
    names = rows[0]['trial'].keys()
    return {'whole_load_qualification_passed': False, 'sampled_seconds': duration, 'samples': len(rows),
            'input_events_per_second': (latest[-1] - latest[0]) / duration,
            'server_removed_after_ack_events_per_second': (accepted_acks[-1] - accepted_acks[0]) / duration,
            'stream_messages_first_last_max': [pending[0], pending[-1], max(pending)],
            'late_half_backlog_slope_events_per_second': slope,
            'storage_rss_max_bytes': max(r['storage']['rss_bytes'] for r in rows),
            'storage_working_set_max_bytes': max(r['storage']['cgroup_used_bytes'] for r in rows),
            'disk_free_min_bytes': min(r['disk_free_bytes'] for r in rows),
            'host_mem_available_min_bytes': min(r['mem_available_bytes'] for r in rows),
            'io_full_avg10_max': max(r['io_full_avg10'] for r in rows),
            'trial_cgroup_oom_kills_max': {name: max(r['trial'][name].get('memory_events', {}).get('oom_kill', 0) for r in rows)
                                          for name in names},
            'trial_cgroup_memory_max_bytes': {name: max(r['trial'][name].get('memory_current', 0) for r in rows) for name in names},
            'production_pid_changed': any(r['market'][name]['Pid'] != rows[0]['market'][name]['Pid']
                                          for r in rows for name in r['market']),
            'not_proven': ['terminal exact/remote agreement', 'retention service sustainable throughput',
                           'long-term stability', 'production restart/collection normalization']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('pressure', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.pressure), indent=2))
