import asyncio
from types import SimpleNamespace

import pytest

from vibetrading import runtime


@pytest.mark.asyncio
async def test_configured_jobs_share_one_runtime_consumer(monkeypatch):
    jobs = [("first","one"),("second","two")]
    created = []
    running = asyncio.Event()
    class Engine:
        def __init__(self, definitions, **kwargs):
            created.append((definitions,kwargs))
        async def run(self):
            running.set()
            await asyncio.Event().wait()
    settings=SimpleNamespace(archive=SimpleNamespace(backend="ncloud_storage",endpoint="fake",bucket="fake",enabled=True),
                             env={"ARCHIVE_WORKER_ENABLED":"true"})
    monkeypatch.setattr(runtime,"get_settings",lambda:settings)
    monkeypatch.setattr(runtime,"_load_config",lambda path:([],jobs))
    monkeypatch.setattr(runtime,"SharedStrategyRunner",Engine)
    task=asyncio.create_task(runtime.run_runtime(role="crypto-strategies"))
    await asyncio.wait_for(running.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created == [(jobs,{"mode":"shadow"})]
