"""No exchange/NATS access. Linux UDS and explicit local fixture profiles."""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys

import pytest

from feeds.feed_recovery import EngineCursor, RecoveryFrame
from vibetrading.local_transport import (
    AppliedReceipt, LocalTransportClient, LocalTransportServer, TransportBudgets,
    TransportError, encode_frame, read_frame,
)


SESSION = "a" * 32


def budgets(**updates):
    return replace(TransportBudgets(1024, 1024, 4, 8192, 2.0, 32, 65536, 2, 2.0), **updates)


def cursor(seq=0, incarnation="boot-1"):
    return EngineCursor("synthetic-feed", incarnation, seq)


def frame(seq=1, payload=b'{"price":"123.000000000000000001"}'):
    return RecoveryFrame(cursor(seq), payload)


def mutate(raw, **changes):
    size = struct.unpack("!I", raw[:4])[0]
    header = json.loads(raw[4:4 + size])
    header.update(changes)
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("!I", len(encoded)) + encoded + raw[4 + size:]


async def read_bytes(raw, limits=None):
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    return await read_frame(reader, limits or budgets())


@pytest.mark.asyncio
async def test_exact_bytes_digest_cursor_survive_every_one_byte_read():
    event = frame()
    raw = encode_frame(event.cursor, event.payload, session=SESSION, budgets=budgets())
    reader = asyncio.StreamReader()
    task = asyncio.create_task(read_frame(reader, budgets()))
    for value in raw:
        reader.feed_data(bytes((value,)))
        await asyncio.sleep(0)
    parsed = await task
    assert parsed.cursor == event.cursor and parsed.payload == event.payload
    assert parsed.digest == event.digest


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"sequence": True}, {"sequence": -1}, {"sequence": 2**63},
    {"producer": ""}, {"incarnation": "x" * 129}, {"payload_size": -1},
    {"payload_size": True}, {"payload_size": 1025}, {"kind": []},
    {"phase": []}, {"digest": "broken"}, {"v": True}, {"extra": "forbidden"},
])
async def test_header_cursor_types_sizes_and_unknown_fields_are_rejected(changes):
    raw = encode_frame(cursor(1), b"{}", session=SESSION, budgets=budgets())
    with pytest.raises((TransportError, ValueError)):
        await read_bytes(mutate(raw, **changes))


@pytest.mark.asyncio
async def test_header_length_duplicate_key_checksum_and_truncation_are_explicit():
    raw = encode_frame(cursor(1), b"{}", session=SESSION, budgets=budgets())
    with pytest.raises(TransportError, match="header_budget"):
        await read_bytes(struct.pack("!I", 1025))
    size = struct.unpack("!I", raw[:4])[0]
    duplicate = raw[4:4 + size - 1] + b',"v":1}'
    with pytest.raises(TransportError, match="duplicate_header_key"):
        await read_bytes(struct.pack("!I", len(duplicate)) + duplicate + b"{}")
    with pytest.raises(TransportError, match="checksum"):
        await read_bytes(raw[:-1] + b"x")
    for truncated in (raw[:1], raw[:8], raw[:-1]):
        with pytest.raises(asyncio.IncompleteReadError):
            await read_bytes(truncated)


@pytest.mark.asyncio
async def test_replay_uses_market_admission_reservation_and_rejects_empty_payload():
    raw=encode_frame(cursor(1),b"replay",session=SESSION,budgets=budgets(),kind="REPLAY")
    reserved=[]
    reader=asyncio.StreamReader();reader.feed_data(raw);reader.feed_eof()
    parsed=await read_frame(reader,budgets(),_reserve=reserved.append,_allowed_kinds={"REPLAY"})
    assert parsed.kind=="REPLAY" and reserved==[len(raw)]

    empty_digest=hashlib.sha256(b"").hexdigest()
    empty=mutate(raw,payload_size=0,digest=empty_digest)
    size=struct.unpack("!I",empty[:4])[0]
    empty=empty[:4+size]
    with pytest.raises(TransportError,match="invalid_header_or_payload"):
        await read_bytes(empty,budgets())


@pytest.mark.parametrize("changes", [{"max_sessions": True}, {"max_records": 0},
    {"max_age_seconds": float("nan")}, {"max_frame_bytes": 8193}])
def test_explicit_finite_budgets(changes):
    with pytest.raises(ValueError):
        budgets(**changes)


def test_no_implicit_network_fallback_or_nonlocal_offline_endpoint(tmp_path):
    kwargs = dict(budgets=budgets(), on_frame=lambda item: None, on_gap=lambda *args: None, on_resume=lambda value: False)
    with pytest.raises(TransportError):
        LocalTransportServer(("0.0.0.0", 0), profile="offline-loopback", **kwargs)
    with pytest.raises(TransportError):
        LocalTransportServer(("127.0.0.1", 0), profile="automatic", **kwargs)
    if not sys.platform.startswith("linux"):
        with pytest.raises(TransportError, match="linux_uds"):
            LocalTransportServer(tmp_path / "transport", allowed_uid=0, **kwargs)


async def start_pair(*, limits=None, apply=None, resume=None, profile="offline-loopback", endpoint=None, uid=None,
                     on_replay=None, on_recovery_query=None, on_recovery_commit=None):
    gaps, applied = [], []
    def on_frame(item):
        applied.append(item)
        return apply(item) if apply else AppliedReceipt(item.cursor, item.digest)
    limits = limits or budgets()
    server = LocalTransportServer(endpoint if endpoint is not None else ("127.0.0.1", 0), budgets=limits,
        on_frame=on_frame, on_gap=lambda cur, reason: gaps.append(("server", cur, reason)),
        on_resume=resume or (lambda cur: True), on_replay=on_replay,
        on_recovery_query=on_recovery_query,on_recovery_commit=on_recovery_commit,
        profile=profile, allowed_uid=uid)
    await server.start()
    target = server.endpoint if profile == "linux-uds" else server.addresses
    client = LocalTransportClient(target, producer=cursor().producer, incarnation=cursor().incarnation,
        budgets=limits, on_gap=lambda cur, reason: gaps.append(("client", cur, reason)), profile=profile, allowed_uid=uid)
    try:
        await client.connect()
    except BaseException:
        await server.close()
        raise
    return server, client, gaps, applied


async def acks(client, count):
    return [await asyncio.wait_for(client.receive_ack(), 2) for _ in range(count)]


@pytest.mark.asyncio
async def test_explicit_resume_received_applied_and_separate_durable_receipts():
    server, client, gaps, applied = await start_pair()
    try:
        assert not client.try_send(frame())
        receipt = await client.request_resume(cursor())
        assert receipt.phase == "RESUMED"
        assert await client.publish(frame())
        received, done = await acks(client, 2)
        assert (received.phase, done.phase) == ("RECEIVED", "APPLIED")
        assert received.cursor == done.cursor == cursor(1)
        assert done.digest == frame().digest and applied == [frame()]
        assert client.stats["pending"] == client.stats["retained_bytes"] == 0
        assert client.stats["applied"] == 1 and not gaps
        assert not server.confirm_durable(cursor(1), "0" * 64)
        assert server.confirm_durable(cursor(1), frame().digest)
        assert (await acks(client, 1))[0].phase == "DURABLE"
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_query_fixed_high_water_replay_and_server_owned_commit_gate():
    empty=hashlib.sha256(b"").hexdigest()
    high=cursor(2)
    readiness=[False]
    replayed=[]
    server,client,_,_=await start_pair(
        on_replay=lambda item:(replayed.append(item) or AppliedReceipt(item.cursor,item.digest)),
        on_recovery_query=lambda target:AppliedReceipt(cursor(0),empty),
        on_recovery_commit=lambda target,digest:readiness[0])
    try:
        proof=await client.query_cursor(high)
        assert (proof.cursor,proof.digest)==(cursor(0),empty)
        for item in (frame(1),frame(2)):
            assert client.try_send(item,replay=True)
            ack=await asyncio.wait_for(client.receive_ack(),2)
            assert ack.phase=="APPLIED" and ack.cursor==item.cursor
        assert [item.cursor for item in replayed]==[cursor(1),cursor(2)]
        try:
            blocked=await client.commit_recovery(high,frame(2).digest)
        except asyncio.TimeoutError as error:
            sessions={key:{"applied":value.applied,"high_water":value.recovery_high_water,
                           "history":dict(value.history)} for key,value in server._sessions.items()}
            raise AssertionError(f"commit blocked ACK missing; client={client.stats}; sessions={sessions}") from error
        assert not blocked, f"client={client.stats}; applied={applied}"
        assert not client.valid
        readiness[0]=True
        assert await client.commit_recovery(high,frame(2).digest)
        assert client.valid
        assert client.try_send(frame(3))
        assert [ack.phase for ack in await acks(client,2)]==["RECEIVED","APPLIED"]
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not sys.platform.startswith("linux"),reason="production UDS is Linux-only")
async def test_query_fixed_high_water_replay_and_commit_gate_over_linux_uds(tmp_path):
    empty=hashlib.sha256(b"").hexdigest()
    high=cursor(1)
    replayed=[]
    server,client,_,_=await start_pair(profile="linux-uds",endpoint=tmp_path/"delivery",
        uid=os.geteuid(),
        on_replay=lambda item:(replayed.append(item) or AppliedReceipt(item.cursor,item.digest)),
        on_recovery_query=lambda target:AppliedReceipt(cursor(0),empty),
        on_recovery_commit=lambda target,digest:target==high and digest==frame(1).digest)
    try:
        assert (await client.query_cursor(high)).cursor==cursor(0)
        assert client.try_send(frame(1),replay=True)
        replay_ack=await asyncio.wait_for(client.receive_ack(),2)
        assert replay_ack.phase=="APPLIED" and replay_ack.digest==frame(1).digest
        assert await client.commit_recovery(high,frame(1).digest)
        assert replayed==[frame(1)]
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_resume_rejection_and_application_receipt_error_fail_closed():
    server, client, gaps, applied = await start_pair(resume=lambda cur: False)
    try:
        assert (await client.request_resume(cursor())).phase == "REJECTED"
        assert not client.try_send(frame()) and not applied
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_rejected_resume_keeps_control_lane_available_for_query_recovery():
    empty=hashlib.sha256(b"").hexdigest()
    server,client,_,_=await start_pair(resume=lambda cur:False,
        on_recovery_query=lambda target:AppliedReceipt(cursor(0),empty))
    try:
        assert (await client.request_resume(cursor(1))).phase=="REJECTED"
        proof=await client.query_cursor(cursor(1))
        assert (proof.phase,proof.cursor,proof.digest)==("QUERY",cursor(0),empty)
    finally:
        await client.close()
        await server.close()
    server, client, gaps, applied = await start_pair(apply=lambda item: AppliedReceipt(item.cursor, "0" * 64))
    try:
        await client.request_resume(cursor())
        assert client.try_send(frame())
        events = await acks(client, 2)
        assert events[0].phase == "RECEIVED" and events[1].phase == "GAP"
        assert not client.valid and client.stats["failed"] == 1
        assert any(reason == "application_failed" for _, _, reason in gaps)
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", ["records", "bytes", "frame", "sequence", "restart"])
async def test_nonblocking_admission_rejects_and_latches_gap(overflow):
    limits = budgets(max_records=1) if overflow == "records" else budgets(max_bytes=1024)
    server, client, gaps, applied = await start_pair(limits=limits)
    try:
        await client.request_resume(cursor())
        if overflow == "records":
            assert client.try_send(frame())
            candidate = frame(2)
        elif overflow == "bytes":
            candidate = frame(payload=b"x" * 900)
        elif overflow == "frame":
            candidate = frame(payload=b"x" * 1025)
        elif overflow == "sequence":
            candidate = frame(2)
        else:
            candidate = RecoveryFrame(cursor(1, "boot-2"), b"{}")
        assert not client.try_send(candidate)
        assert not client.valid and gaps
        assert client.stats["pending"] == client.stats["retained_bytes"] == 0
        assert client.stats["accepted"] == client.stats["applied"] + client.stats["failed"]
        with pytest.raises(TransportError):
            await client.request_resume(cursor())
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_control_heartbeat_bypasses_a_full_blocked_data_lane():
    server, client, _, applied = await start_pair(limits=budgets(max_records=1))
    try:
        await client.request_resume(cursor())
        writer = client._connections["data"].writer_task
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        assert client.try_send(frame())
        assert client.stats["pending"] == 1
        assert await client.heartbeat()
        assert (await acks(client, 1))[0].phase == "PONG"
        assert client.valid and not applied and client.stats["pending"] == 1
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_oldest_age_invalidates_inflight_records_without_leaking_reservations():
    server, client, gaps, _ = await start_pair(limits=budgets(max_age_seconds=0.06))
    try:
        await client.request_resume(cursor())
        client._connections["data"].writer_task.cancel()
        await asyncio.gather(client._connections["data"].writer_task, return_exceptions=True)
        assert client.try_send(frame())
        await asyncio.sleep(0.15)
        assert not client.valid and client.stats["pending"] == 0
        assert client.stats["failed"] == 1
        assert any(reason == "market_oldest_age_exceeded" for _, _, reason in gaps)
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_disconnect_invalidates_and_new_incarnation_requires_explicit_resume():
    server, client, gaps, _ = await start_pair()
    second = None
    try:
        await client.request_resume(cursor())
        client._connections["data"].writer.close()
        await asyncio.sleep(0.05)
        assert not client.valid and gaps
        await client.close()
        await asyncio.sleep(0.05)
        second = LocalTransportClient(server.addresses, producer=cursor().producer, incarnation="boot-2",
            budgets=budgets(), on_gap=lambda *args: None, profile="offline-loopback")
        await second.connect()
        assert not second.try_send(RecoveryFrame(cursor(1, "boot-2"), b"{}"))
        assert (await second.request_resume(cursor(0, "boot-2"))).phase == "RESUMED"
    finally:
        if second:
            await second.close()
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_unconsumed_ack_budget_invalidates_instead_of_growing():
    server, client, gaps, _ = await start_pair(limits=budgets(max_control_records=2))
    try:
        await client.request_resume(cursor())
        assert client.try_send(frame())
        await asyncio.sleep(0.05)
        assert client.stats["ack_records"] == 2
        assert await client.heartbeat()
        await asyncio.sleep(0.05)
        assert not client.valid and client.stats["ack_records"] == 2
        assert any(reason == "ack_consumer_overflow" for _, _, reason in gaps)
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_gap_after_resume_receipt_cannot_reopen_invalid_session(monkeypatch):
    server, client, _, _ = await start_pair()
    original_wait = asyncio.wait_for
    async def gap_at_receipt(awaitable, timeout):
        receipt = await original_wait(awaitable, timeout)
        # Inject the real peer-disconnect notification at the boundary between
        # receipt completion and the public resume method consuming it.
        client._invalidate("peer_disconnected_at_resume", notify=False)
        return receipt
    try:
        monkeypatch.setattr(asyncio, "wait_for", gap_at_receipt)
        with pytest.raises(TransportError, match="resume_raced"):
            await client.request_resume(cursor())
        assert not client.valid and not client.try_send(frame())
        assert client.stats["pending"] == 0
    finally:
        monkeypatch.setattr(asyncio, "wait_for", original_wait)
        await client.close()
        await server.close()


linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux AF_UNIX/SO_PEERCRED contract; no Windows equivalence claim")


@linux
@pytest.mark.asyncio
async def test_linux_uds_permissions_peer_uid_owner_and_socket_cleanup(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "feed"
    server, client, _, applied = await start_pair(profile="linux-uds", endpoint=endpoint, uid=os.geteuid())
    try:
        for path in server.addresses.values():
            assert stat.S_ISSOCK(path.stat().st_mode)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        duplicate = LocalTransportServer(endpoint, budgets=budgets(), on_frame=lambda item: None,
            on_gap=lambda *args: None, on_resume=lambda value: True, allowed_uid=os.geteuid())
        with pytest.raises(RuntimeError, match="another process"):
            await duplicate.start()
        await client.request_resume(cursor())
        assert client.try_send(frame())
        assert [a.phase for a in await acks(client, 2)] == ["RECEIVED", "APPLIED"]
        assert applied == [frame()]
    finally:
        await client.close()
        await server.close()
    assert all(not Path(str(endpoint) + "." + lane).exists() for lane in ("data", "control"))


@linux
@pytest.mark.asyncio
async def test_linux_refuses_foreign_endpoint_insecure_directory_and_peer_uid(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "feed"
    existing = Path(str(endpoint) + ".data")
    existing.write_bytes(b"do not remove")
    kwargs = dict(budgets=budgets(), on_frame=lambda item: None, on_gap=lambda *args: None,
                  on_resume=lambda value: True, allowed_uid=os.geteuid())
    server = LocalTransportServer(endpoint, **kwargs)
    with pytest.raises(TransportError, match="existing_uds"):
        await server.start()
    assert existing.read_bytes() == b"do not remove"
    # Explicitly remove this test-created regular file, never a live socket.
    existing.unlink()
    private.chmod(0o755)
    with pytest.raises(TransportError, match="private_directory"):
        await LocalTransportServer(endpoint, **kwargs).start()
    private.chmod(0o700)
    server = LocalTransportServer(endpoint, **kwargs)
    await server.start()
    rejected = LocalTransportClient(endpoint, producer="synthetic", incarnation="boot", budgets=budgets(),
        on_gap=lambda *args: None, allowed_uid=os.geteuid() + 1)
    try:
        with pytest.raises(TransportError, match="server_peer_uid"):
            await rejected.connect()
    finally:
        await rejected.close()
        await server.close()


@linux
@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["header", "checksum", "sequence", "partial"])
async def test_linux_uds_faults_invalidate_without_applying_market_frame(tmp_path, fault):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    server, client, gaps, applied = await start_pair(profile="linux-uds", endpoint=private / "feed",
        uid=os.geteuid(), limits=budgets(max_age_seconds=0.06, io_timeout_seconds=0.3))
    try:
        await client.request_resume(cursor())
        if fault == "header":
            raw = struct.pack("!I", 1025)
        elif fault == "partial":
            raw = b"\x00\x00"
        else:
            raw = encode_frame(cursor(2 if fault == "sequence" else 1), b"{}", session=client.session, budgets=budgets())
            if fault == "checksum":
                raw = raw[:-1] + b"x"
        writer = client._connections["data"].writer
        writer.write(raw)
        await writer.drain()
        await asyncio.sleep(0.16)
        assert not client.valid and not applied
        assert any(side == "server" for side, _, _ in gaps)
    finally:
        await client.close()
        await server.close()
