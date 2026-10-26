"""Offline exact-index validation before narrowing ONLY a disposable test policy."""
import json
from pathlib import Path
import sqlite3
import time

from storage.event_index_runs import ExactEventRuns

ROOT = Path('/srv/vibetrading-data/r2-load-gate/fullscale-20261001')


def main():
    evidence = ROOT / 'evidence/service-20261001'
    destination = evidence / 'offline-test-policy.json'
    if destination.exists():
        raise RuntimeError('test policy already validated; do not repeat')
    parent = json.loads((ROOT / 'evidence/integrity.json').read_text())
    if parent.get('storage_ack_archive_integrity_verified') is not True:
        raise RuntimeError('parent exact audit required')
    database = ROOT / 'state/vibetrading.sqlite3'
    production = Path('/srv/vibetrading-data/runtime_data/crypto_state/vibetrading.sqlite3')
    if production.exists() and database.samefile(production):
        raise RuntimeError('production database forbidden')
    conn = sqlite3.connect(database, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA cache_size=-65536')
    try:
        prior = conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_live_replay_max_age_seconds'").fetchone()
        retired = conn.execute('SELECT retired_entries FROM event_index_retention_state WHERE singleton=1').fetchone()[0]
        if prior is None or prior[0] != '172800' or retired != 0:
            raise RuntimeError('unretired complete test index and original policy required')
        expected = conn.execute('SELECT SUM(entry_count) FROM event_index_runs').fetchone()[0]
        started = time.monotonic()
        index = ExactEventRuns(conn)
        try:
            index.prepare()
            if index.loaded_entries != expected or expected < 9240414:
                raise RuntimeError('full exact index validation failed')
        finally:
            index.close()
        conn.execute('BEGIN IMMEDIATE')
        changed = conn.execute("UPDATE runtime_metadata SET value='3600',updated_ts_ns=? "
                               "WHERE key='crypto_live_replay_max_age_seconds' AND value='172800'", (time.time_ns(),)).rowcount
        if changed != 1:
            raise RuntimeError('test policy changed during offline preparation')
        conn.execute('COMMIT')
        proof = {'fully_validated_exact_entries': expected, 'seconds': time.monotonic() - started,
                 'old_test_replay_seconds': 172800, 'new_test_replay_seconds': 3600,
                 'test_only_policy_narrowing': True, 'production_modified': False,
                 'event_timestamps_ack_and_archive_evidence_changed': False}
        with destination.open('x') as handle:
            json.dump(proof, handle, indent=2)
        print(json.dumps(proof), flush=True)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
