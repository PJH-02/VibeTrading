import asyncio
import threading

from runtime.bus import InMemoryEventBus
from storage.parquet_store import ParquetBatchWriter
from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore
from storage.storage_worker import StorageWorker


def test_heartbeat_sqlite_lock_does_not_block_nats_event_loop(tmp_path):
    env = tmp_path / '.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n', encoding='utf-8')
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings)
    worker = StorageWorker(InMemoryEventBus(), ParquetBatchWriter(settings, store), domain='crypto')
    held = threading.Event()
    release = threading.Event()
    watchdog_fired = threading.Event()

    def hold_commit_lock():
        with store._lock:
            held.set()
            release.wait(5)

    def watchdog():
        watchdog_fired.set()
        release.set()

    holder = threading.Thread(target=hold_commit_lock)
    holder.start()
    assert held.wait(2)
    timer = threading.Timer(1.0, watchdog)
    timer.start()

    async def exercise():
        task = asyncio.create_task(worker.run(0.2))
        try:
            # This continuation represents NATS socket/heartbeat work. It
            # must run while SQLite is locked, before the watchdog releases it.
            await asyncio.sleep(0.05)
            assert not watchdog_fired.is_set()
        finally:
            release.set()
            timer.cancel()
            await task

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        timer.cancel()
        holder.join(2)
        store.close()
