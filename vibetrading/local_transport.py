"""Bounded local market transport, independent of NATS.

Two persistent sockets separate control from market bytes. RECEIVED/APPLIED are
transport/application facts, never producer-spool or remote-durability proofs.
Callbacks are synchronous state-only operations; blocking IO belongs elsewhere.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time
import uuid

from .feed_recovery import EngineCursor, RecoveryFrame
from .process_lease import ProcessLease


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransportBudgets:
    max_frame_bytes: int
    max_header_bytes: int
    max_records: int
    max_bytes: int
    max_age_seconds: float
    max_control_records: int
    max_control_bytes: int
    max_sessions: int
    io_timeout_seconds: float

    def __post_init__(self):
        for value in (self.max_frame_bytes, self.max_header_bytes, self.max_records,
                      self.max_bytes, self.max_control_records, self.max_control_bytes, self.max_sessions):
            if type(value) is not int or value <= 0:
                raise ValueError("Transport budgets require positive explicit integers")
        if self.max_frame_bytes > self.max_bytes or self.max_header_bytes > self.max_control_bytes:
            raise ValueError("Frame/header exceed their lane byte budgets")
        for value in (self.max_age_seconds, self.io_timeout_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Transport ages/timeouts must be positive and finite")


@dataclass(frozen=True)
class AppliedReceipt:
    cursor: EngineCursor
    digest: str


@dataclass(frozen=True)
class TransportAck:
    phase: str
    cursor: EngineCursor
    digest: str
    reason: str = ""


@dataclass(frozen=True)
class _Envelope:
    kind: str
    session: str
    cursor: EngineCursor
    payload: bytes
    digest: str
    phase: str = ""
    reason: str = ""


_KINDS = {"HELLO", "HELLO_ACK", "DATA", "ACK", "RESUME", "PING", "GAP"}
_PHASES = {"", "RECEIVED", "APPLIED", "DURABLE", "RESUMED", "REJECTED", "PONG", "GAP"}
_FIELDS = {"v", "kind", "session", "producer", "incarnation", "sequence", "payload_size", "digest", "phase", "reason"}


def _cursor_valid(cursor):
    if (not isinstance(cursor, EngineCursor) or type(cursor.sequence) is not int
            or not 0 <= cursor.sequence <= 2**63 - 1
            or any(not isinstance(x, str) or not x or len(x.encode("utf-8")) > 128
                   for x in (cursor.producer, cursor.incarnation))):
        raise TransportError("invalid_cursor")


def _header(envelope: _Envelope, budgets: TransportBudgets) -> bytes:
    _cursor_valid(envelope.cursor)
    if (not isinstance(envelope.kind, str) or envelope.kind not in _KINDS
            or not isinstance(envelope.phase, str) or envelope.phase not in _PHASES
            or not isinstance(envelope.session, str) or len(envelope.session) != 32
            or any(x not in "0123456789abcdef" for x in envelope.session)
            or not isinstance(envelope.reason, str) or len(envelope.reason.encode("utf-8")) > 256
            or type(envelope.payload) is not bytes or len(envelope.payload) > budgets.max_frame_bytes
            or (envelope.kind == "DATA" and not envelope.payload)
            or (envelope.kind != "DATA" and envelope.payload)):
        raise TransportError("invalid_header_or_payload")
    if (not isinstance(envelope.digest, str) or len(envelope.digest) != 64
            or any(x not in "0123456789abcdef" for x in envelope.digest)):
        raise TransportError("invalid_digest")
    if (envelope.kind == "ACK" and not envelope.phase) or (envelope.kind != "ACK" and envelope.phase):
        raise TransportError("phase_only_valid_on_ack")
    raw = json.dumps({"v": 1, "kind": envelope.kind, "session": envelope.session,
        "producer": envelope.cursor.producer, "incarnation": envelope.cursor.incarnation,
        "sequence": envelope.cursor.sequence, "payload_size": len(envelope.payload),
        "digest": envelope.digest, "phase": envelope.phase, "reason": envelope.reason},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > budgets.max_header_bytes:
        raise TransportError("header_budget_exceeded")
    return raw


def encode_frame(cursor: EngineCursor, payload: bytes, *, session: str,
                 budgets: TransportBudgets, kind="DATA", phase="", reason="", digest=None) -> bytes:
    checksum = hashlib.sha256(payload).hexdigest() if digest is None else digest
    envelope = _Envelope(kind, session, cursor, payload, checksum, phase, reason)
    raw = _header(envelope, budgets)
    return struct.pack("!I", len(raw)) + raw + payload


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportError("duplicate_header_key")
        result[key] = value
    return result


async def read_frame(reader: asyncio.StreamReader, budgets: TransportBudgets, *, _reserve=None, _on_start=None,
                     _allowed_kinds=None) -> _Envelope:
    """Length checks precede payload allocation; partial reads are explicit."""
    first = await reader.readexactly(1)
    if _on_start is not None:
        _on_start()
    size = struct.unpack("!I", first + await reader.readexactly(3))[0]
    if size == 0 or size > budgets.max_header_bytes:
        raise TransportError("header_budget_exceeded")
    try:
        header = json.loads((await reader.readexactly(size)).decode("utf-8"), object_pairs_hook=_object_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(TransportError("nonfinite_header")))
    except (ValueError, UnicodeError) as error:
        raise TransportError("invalid_json_header") from error
    if (not isinstance(header, dict) or set(header) != _FIELDS or type(header["v"]) is not int
            or header["v"] != 1 or type(header["payload_size"]) is not int
            or not 0 <= header["payload_size"] <= budgets.max_frame_bytes):
        raise TransportError("invalid_header")
    try:
        cursor = EngineCursor(header["producer"], header["incarnation"], header["sequence"])
    except ValueError as error:
        raise TransportError("invalid_cursor") from error
    # Validate every bounded header field before reading a possibly large body.
    envelope = _Envelope(header["kind"], header["session"], cursor,
        b"x" if header["payload_size"] and header["kind"] == "DATA" else b"",
        header["digest"], header["phase"], header["reason"])
    _header(envelope, budgets)
    if _allowed_kinds is not None and header["kind"] not in _allowed_kinds:
        raise TransportError("message_kind_not_allowed_on_lane")
    if header["kind"] != "DATA" and header["payload_size"] != 0:
        raise TransportError("control_payload_forbidden")
    if header["kind"] == "DATA" and header["payload_size"] == 0:
        raise TransportError("empty_market_frame")
    if _reserve is not None and header["kind"] == "DATA":
        _reserve(4 + size + header["payload_size"])
    payload = await reader.readexactly(header["payload_size"])
    # ACK digest describes its referenced market payload, not the empty body.
    if header["kind"] != "ACK" and hashlib.sha256(payload).hexdigest() != header["digest"]:
        raise TransportError("checksum_mismatch")
    return _Envelope(header["kind"], header["session"], cursor, payload,
                     header["digest"], header["phase"], header["reason"])


def _call(callback, *args):
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TransportError("transport_callback_must_be_synchronous")
    return result


class _Outbox:
    def __init__(self, records, byte_limit):
        self.records, self.byte_limit = records, byte_limit
        self.items = deque()
        self.bytes = 0
        self.ready = asyncio.Event()

    def put(self, value: bytes):
        if len(self.items) >= self.records or self.bytes + len(value) > self.byte_limit:
            return False
        self.items.append(value)
        self.bytes += len(value)
        self.ready.set()
        return True

    async def take(self):
        while not self.items:
            self.ready.clear()
            await self.ready.wait()
        return self.items[0]

    def done(self):
        self.bytes -= len(self.items.popleft())


class _Connection:
    def __init__(self, reader, writer, budgets):
        self.reader, self.writer, self.budgets = reader, writer, budgets
        self.out = _Outbox(budgets.max_control_records, budgets.max_control_bytes)
        self.last_activity = time.monotonic()
        self.task = None
        self.writer_task = None
        self.session = None
        self.lane = None
        self.reserved = 0
        self.frame_started = None
        self.failure_reason = ""

    async def write_loop(self):
        while True:
            raw = await self.out.take()
            # Never hand a whole maximum market frame to the asyncio buffer.
            for start in range(0, len(raw), 16384):
                self.writer.write(raw[start:start + 16384])
                await self.writer.drain()
            self.out.done()

    def stop(self):
        self.writer.close()
        for task in (self.task, self.writer_task):
            if task and task is not asyncio.current_task():
                task.cancel()


@dataclass
class _Session:
    identity: str
    checkpoint: EngineCursor
    control: _Connection
    data: _Connection | None = None
    valid: bool = False
    busy: bool = False
    received: EngineCursor | None = None
    applied: EngineCursor | None = None
    history: OrderedDict | None = None


def _profile(profile, endpoint):
    if profile == "linux-uds":
        if not sys.platform.startswith("linux") or not hasattr(socket, "AF_UNIX"):
            raise TransportError("production_requires_linux_uds")
        return
    if profile != "offline-loopback":
        raise TransportError("unknown_transport_profile")
    addresses = endpoint.values() if isinstance(endpoint, dict) else (endpoint,)
    if any(not isinstance(address, tuple) or len(address) != 2 or address[0] != "127.0.0.1"
           or type(address[1]) is not int or not 0 <= address[1] <= 65535 for address in addresses):
        raise TransportError("offline_loopback_requires_explicit_local_address")


class LocalTransportServer:
    def __init__(self, endpoint, *, budgets: TransportBudgets, on_frame, on_gap, on_resume,
                 allowed_uid: int | None = None, profile="linux-uds"):
        _profile(profile, endpoint)
        if profile == "linux-uds" and (type(allowed_uid) is not int or allowed_uid < 0):
            raise TransportError("explicit_allowed_peer_uid_required")
        self.endpoint, self.budgets, self.profile = endpoint, budgets, profile
        self.on_frame, self.on_gap, self.on_resume = on_frame, on_gap, on_resume
        self.allowed_uid = allowed_uid
        self.addresses = {}
        self._servers, self._connections, self._sessions = [], set(), {}
        self._paths, self._lease, self._watchdog = {}, None, None
        self._closed = False
        self._in_flight_bytes = self._in_flight_records = 0

    async def start(self):
        if self._closed or self._servers:
            raise TransportError("server_not_startable")
        if self.profile == "linux-uds":
            base = Path(self.endpoint).absolute()
            parent = base.parent
            if not parent.exists():
                parent.mkdir(mode=0o700, parents=True)
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise TransportError("uds_directory_must_be_owned_private_directory")
            self._lease = ProcessLease(Path(str(base) + ".owner.lock"))
            self._lease.acquire()
            self.addresses = {lane: Path(str(base) + "." + lane) for lane in ("control", "data")}
            if any(path.exists() or path.is_symlink() for path in self.addresses.values()):
                self._lease.close()
                raise TransportError("existing_uds_endpoint_requires_explicit_operator_recovery")
        try:
            for lane in ("control", "data"):
                callback = lambda reader, writer, lane=lane: self._accept(lane, reader, writer)
                if self.profile == "linux-uds":
                    server = await asyncio.start_unix_server(callback, path=str(self.addresses[lane]),
                                                             limit=self.budgets.max_header_bytes + 4)
                    path = self.addresses[lane]
                    os.chmod(path, 0o600)
                    self._paths[path] = path.stat().st_ino
                else:
                    host, port = self.endpoint
                    if port and lane == "data":
                        raise TransportError("offline_server_requires_ephemeral_ports_for_two_lanes")
                    server = await asyncio.start_server(callback, host, port, limit=self.budgets.max_header_bytes + 4)
                    self.addresses[lane] = server.sockets[0].getsockname()[:2]
                self._servers.append(server)
            self._watchdog = asyncio.create_task(self._watch(), name="local-transport-server-watch")
        except BaseException:
            await self.close()
            raise
        return self

    def _accept(self, lane, reader, writer):
        if self._closed or len(self._connections) >= 2 * self.budgets.max_sessions:
            writer.close()
            return
        if self.profile == "linux-uds":
            try:
                _, uid, _ = struct.unpack("3i", writer.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != self.allowed_uid:
                    writer.close()
                    return
            except (OSError, AttributeError):
                writer.close()
                return
        connection = _Connection(reader, writer, self.budgets)
        connection.lane = lane
        self._connections.add(connection)
        connection.writer_task = asyncio.create_task(connection.write_loop(), name="local-server-" + lane + "-writer")
        connection.task = asyncio.create_task(self._serve(connection), name="local-server-" + lane + "-reader")
        connection.writer_task.add_done_callback(lambda task: connection.stop() if not task.cancelled() and task.exception() else None)

    def _send(self, session, phase, cursor, digest=None, reason=""):
        raw = encode_frame(cursor, b"", session=session.identity, budgets=self.budgets,
                           kind="ACK", phase=phase, reason=reason, digest=digest)
        if not session.control.out.put(raw):
            self._invalidate(session, "control_queue_overflow", notify=False)
            session.control.stop()
            return False
        return True

    def _invalidate(self, session, reason, *, notify=True):
        session.valid = False
        try:
            _call(self.on_gap, session.received or session.checkpoint, reason)
        except Exception:
            session.control.stop()
            if session.data:
                session.data.stop()
            return
        if notify:
            self._send(session, "GAP", session.received or session.checkpoint, reason=reason)

    def _reserve(self, connection, size):
        if (connection.lane != "data" or connection.reserved
                or self._in_flight_bytes + size > self.budgets.max_bytes
                or self._in_flight_records >= self.budgets.max_records):
            raise TransportError("server_market_admission_overflow")
        connection.reserved = size
        self._in_flight_bytes += size
        self._in_flight_records += 1

    def _release(self, connection):
        if connection.reserved:
            self._in_flight_bytes -= connection.reserved
            self._in_flight_records -= 1
            connection.reserved = 0

    async def _serve(self, connection):
        session = None
        reason = "disconnected"
        try:
            hello = await read_frame(connection.reader, self.budgets,
                _on_start=lambda: setattr(connection, "frame_started", time.monotonic()), _allowed_kinds={"HELLO"})
            connection.frame_started = None
            connection.last_activity = time.monotonic()
            if hello.kind != "HELLO":
                raise TransportError("hello_required")
            if connection.lane == "control":
                if (len(self._sessions) >= self.budgets.max_sessions or hello.session in self._sessions
                        or any(s.checkpoint.producer == hello.cursor.producer for s in self._sessions.values())):
                    raise TransportError("producer_or_session_already_connected")
                session = _Session(hello.session, hello.cursor, connection, history=OrderedDict())
                self._sessions[hello.session] = session
            else:
                session = self._sessions.get(hello.session)
                if session is None or session.data is not None or hello.cursor != session.checkpoint:
                    raise TransportError("data_session_pairing_failed")
                session.data = connection
            connection.session = session
            self._send(session, "PONG", hello.cursor, reason="hello_" + connection.lane)
            while True:
                try:
                    envelope = await read_frame(connection.reader, self.budgets,
                        _reserve=lambda size: self._reserve(connection, size),
                        _on_start=lambda: setattr(connection, "frame_started", time.monotonic()),
                        _allowed_kinds={"DATA"} if connection.lane == "data" else {"PING", "RESUME", "GAP"})
                    connection.last_activity = time.monotonic()
                    if (envelope.session != session.identity or
                        (envelope.cursor.producer, envelope.cursor.incarnation) !=
                        (session.checkpoint.producer, session.checkpoint.incarnation)):
                        raise TransportError("session_or_incarnation_changed")
                    if connection.lane == "control":
                        self._control(session, envelope)
                    elif envelope.kind == "DATA":
                        self._data(session, envelope)
                    else:
                        raise TransportError("market_lane_requires_data")
                finally:
                    self._release(connection)
                    connection.frame_started = None
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError, TransportError) as error:
            reason = str(error)[:128] if isinstance(error, TransportError) else type(error).__name__
        finally:
            if session:
                try:
                    self._invalidate(session, connection.failure_reason or reason, notify=connection.lane == "data")
                finally:
                    if connection.lane == "control":
                        self._sessions.pop(session.identity, None)
                        if session.data:
                            session.data.stop()
                    elif session.data is connection:
                        session.data = None
            connection.stop()
            self._connections.discard(connection)

    def _control(self, session, envelope):
        if envelope.kind == "PING":
            self._send(session, "PONG", envelope.cursor)
        elif envelope.kind == "GAP":
            self._invalidate(session, envelope.reason or "producer_gap")
        elif envelope.kind == "RESUME":
            if session.busy or session.data is None:
                self._send(session, "REJECTED", envelope.cursor, reason="resume_busy_or_missing_data_lane")
                return
            if _call(self.on_resume, envelope.cursor) is not True:
                self._send(session, "REJECTED", envelope.cursor, reason="resume_not_verified")
                return
            session.received = session.applied = envelope.cursor
            session.history.clear()
            session.valid = True
            self._send(session, "RESUMED", envelope.cursor)
        else:
            raise TransportError("invalid_control_message")

    def _data(self, session, envelope):
        if not session.valid or session.received is None:
            self._send(session, "REJECTED", envelope.cursor, envelope.digest, "explicit_resume_required")
            return
        if envelope.cursor.sequence != session.received.sequence + 1:
            self._invalidate(session, "sequence_gap_or_duplicate")
            self._send(session, "REJECTED", envelope.cursor, envelope.digest, "sequence_gap_or_duplicate")
            return
        frame = RecoveryFrame(envelope.cursor, envelope.payload)
        session.received = frame.cursor
        if not self._send(session, "RECEIVED", frame.cursor, frame.digest):
            return
        session.busy = True
        try:
            result = _call(self.on_frame, frame)
            if not isinstance(result, AppliedReceipt) or result.cursor != frame.cursor or result.digest != frame.digest:
                raise TransportError("application_receipt_mismatch")
            session.applied = frame.cursor
            session.history[frame.cursor.sequence] = frame.digest
            while len(session.history) > self.budgets.max_records:
                session.history.popitem(last=False)
            self._send(session, "APPLIED", frame.cursor, frame.digest)
        except Exception as error:
            self._invalidate(session, "application_failed")
            self._send(session, "REJECTED", frame.cursor, frame.digest, type(error).__name__)
        finally:
            session.busy = False

    def confirm_durable(self, cursor: EngineCursor, digest: str) -> bool:
        """Caller must hold actual durability proof; APPLIED never calls this."""
        for session in self._sessions.values():
            if ((cursor.producer, cursor.incarnation) == (session.checkpoint.producer, session.checkpoint.incarnation)
                    and session.history.get(cursor.sequence) == digest):
                return self._send(session, "DURABLE", cursor, digest)
        return False

    async def _watch(self):
        interval = min(1.0, self.budgets.io_timeout_seconds / 2, self.budgets.max_age_seconds / 2)
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            for connection in tuple(self._connections):
                # Control heartbeat renews both lanes; idle market is legitimate.
                witness = connection.session.control if connection.session else connection
                if (connection.frame_started is not None
                        and now - connection.frame_started > min(self.budgets.io_timeout_seconds, self.budgets.max_age_seconds)):
                    connection.failure_reason = "partial_frame_age_exceeded"
                    connection.stop()
                elif now - witness.last_activity > self.budgets.io_timeout_seconds:
                    connection.failure_reason = "control_lease_expired"
                    connection.stop()

    async def close(self):
        self._closed = True
        for server in self._servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self._servers))
        connections = tuple(self._connections)
        for connection in connections:
            connection.stop()
        tasks = [task for connection in connections for task in (connection.task, connection.writer_task) if task]
        if self._watchdog:
            self._watchdog.cancel()
            tasks.append(self._watchdog)
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(connection.writer.wait_closed() for connection in connections), return_exceptions=True)
        for path, inode in self._paths.items():
            try:
                info = path.lstat()
                if stat.S_ISSOCK(info.st_mode) and info.st_ino == inode:
                    path.unlink()
            except FileNotFoundError:
                pass
        if self._lease:
            self._lease.close()


class LocalTransportClient:
    def __init__(self, endpoint, *, producer: str, incarnation: str, budgets: TransportBudgets,
                 on_gap, allowed_uid: int | None = None, profile="linux-uds"):
        _profile(profile, endpoint)
        if profile == "linux-uds" and (type(allowed_uid) is not int or allowed_uid < 0):
            raise TransportError("explicit_allowed_peer_uid_required")
        self.endpoint, self.profile, self.budgets, self.on_gap = endpoint, profile, budgets, on_gap
        self.allowed_uid = allowed_uid
        self.cursor = EngineCursor(producer, incarnation, 0)
        _cursor_valid(self.cursor)
        self.session = uuid.uuid4().hex
        self.valid, self._closed = False, False
        self._connections = {}
        self._pending = OrderedDict()
        self._receipts = OrderedDict()
        self._data_ready = asyncio.Event()
        self._acks = asyncio.Queue(maxsize=budgets.max_control_records)
        self._ack_bytes = 0
        self._hello = None
        self._resume = None
        self._watchdog = None
        self._accepted = self._applied = self._failed = 0
        self._faulted = False

    async def connect(self):
        if self._closed or self._connections:
            raise TransportError("client_not_connectable")
        try:
            for lane in ("control", "data"):
                if self.profile == "linux-uds":
                    reader, writer = await asyncio.open_unix_connection(str(self.endpoint) + "." + lane,
                        limit=self.budgets.max_header_bytes + 4)
                else:
                    reader, writer = await asyncio.open_connection(*self.endpoint[lane], limit=self.budgets.max_header_bytes + 4)
                if self.profile == "linux-uds":
                    _, peer_uid, _ = struct.unpack("3i", writer.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if peer_uid != self.allowed_uid:
                        writer.close()
                        await writer.wait_closed()
                        raise TransportError("server_peer_uid_not_allowed")
                connection = _Connection(reader, writer, self.budgets)
                connection.lane = lane
                self._connections[lane] = connection
                connection.writer_task = asyncio.create_task(connection.write_loop() if lane == "control" else self._write_data(connection),
                                                             name="local-client-" + lane + "-writer")
                connection.task = asyncio.create_task(self._read_control(connection) if lane == "control" else self._read_data(connection),
                                                      name="local-client-" + lane + "-reader")
                self._hello = asyncio.get_running_loop().create_future()
                if lane == "control":
                    connection.out.put(encode_frame(self.cursor, b"", session=self.session, budgets=self.budgets, kind="HELLO"))
                else:
                    writer.write(encode_frame(self.cursor, b"", session=self.session, budgets=self.budgets, kind="HELLO"))
                    await writer.drain()
                await asyncio.wait_for(self._hello, self.budgets.io_timeout_seconds)
            self._watchdog = asyncio.create_task(self._watch(), name="local-client-age-watch")
        except BaseException:
            await self.close()
            raise
        return self

    def _invalidate(self, reason, *, notify=True):
        self.valid = False
        if self._faulted:
            return
        self._faulted = True
        self._failed += len(self._pending)
        self._pending.clear()
        try:
            _call(self.on_gap, self.cursor, reason)
        except Exception:
            notify = False
            if "control" in self._connections:
                self._connections["control"].stop()
        if "data" in self._connections:
            self._connections["data"].stop()
        if notify and "control" in self._connections:
            raw = encode_frame(self.cursor, b"", session=self.session, budgets=self.budgets, kind="GAP", reason=reason[:128])
            if not self._connections["control"].out.put(raw):
                self._connections["control"].stop()

    def try_send(self, frame: RecoveryFrame) -> bool:
        if self._closed or self._faulted or not self.valid or "data" not in self._connections:
            return False
        try:
            _cursor_valid(frame.cursor)
            if ((frame.cursor.producer, frame.cursor.incarnation) != (self.cursor.producer, self.cursor.incarnation)
                    or frame.cursor.sequence != self.cursor.sequence + 1):
                raise TransportError("producer_cursor_gap_or_restart")
            raw = encode_frame(frame.cursor, frame.payload, session=self.session, budgets=self.budgets)
            retained = sum(len(item[0]) for item in self._pending.values())
            if self._pending and time.monotonic() - next(iter(self._pending.values()))[2] > self.budgets.max_age_seconds:
                raise TransportError("market_oldest_age_exceeded")
            if len(self._pending) >= self.budgets.max_records or retained + len(raw) > self.budgets.max_bytes:
                raise TransportError("market_admission_overflow")
        except (TransportError, ValueError) as error:
            self._invalidate(str(error))
            return False
        self._pending[frame.cursor.sequence] = (raw, frame.digest, time.monotonic(), False)
        self.cursor = frame.cursor
        self._accepted += 1
        self._data_ready.set()
        return True

    async def publish(self, frame: RecoveryFrame) -> bool:
        return self.try_send(frame)

    async def _write_data(self, connection):
        try:
            while True:
                item = next(((seq, value) for seq, value in self._pending.items() if not value[3]), None)
                if item is None:
                    self._data_ready.clear()
                    await self._data_ready.wait()
                    continue
                seq, (raw, digest, stamp, _) = item
                self._pending[seq] = (raw, digest, stamp, True)
                for start in range(0, len(raw), 16384):
                    connection.writer.write(raw[start:start + 16384])
                    await connection.writer.drain()
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError):
            self._invalidate("data_write_failed")
            connection.stop()

    async def _read_data(self, connection):
        try:
            # The server never sends market/control data on this direction.
            if await connection.reader.read(1):
                raise TransportError("unexpected_data_lane_response")
        finally:
            if not self._closed:
                self._invalidate("data_disconnected")

    async def _read_control(self, connection):
        try:
            while True:
                envelope = await read_frame(connection.reader, self.budgets, _allowed_kinds={"ACK"})
                connection.last_activity = time.monotonic()
                if (envelope.kind != "ACK" or envelope.session != self.session
                        or (envelope.cursor.producer, envelope.cursor.incarnation) != (self.cursor.producer, self.cursor.incarnation)):
                    raise TransportError("invalid_ack_identity")
                ack = TransportAck(envelope.phase, envelope.cursor, envelope.digest, envelope.reason)
                if ack.phase == "PONG" and ack.reason.startswith("hello_"):
                    if self._hello is not None and not self._hello.done():
                        self._hello.set_result(ack)
                    continue
                if ack.phase in {"RESUMED", "REJECTED"} and self._resume is not None:
                    if not self._resume.done():
                        self._resume.set_result(ack)
                    continue
                if ack.phase == "GAP":
                    self._invalidate(ack.reason or "server_gap", notify=False)
                elif ack.phase in {"RECEIVED", "APPLIED"}:
                    item = self._pending.get(ack.cursor.sequence)
                    if item is None or item[1] != ack.digest:
                        raise TransportError("ack_cursor_or_digest_mismatch")
                    if ack.phase == "APPLIED":
                        self._pending.pop(ack.cursor.sequence)
                        self._receipts[ack.cursor.sequence] = ack.digest
                        self._applied += 1
                        while len(self._receipts) > self.budgets.max_records:
                            self._receipts.popitem(last=False)
                elif ack.phase == "DURABLE":
                    if self._receipts.get(ack.cursor.sequence) != ack.digest:
                        raise TransportError("durable_receipt_not_applied")
                elif ack.phase == "REJECTED":
                    self._invalidate(ack.reason or "rejected", notify=False)
                elif ack.phase != "PONG":
                    raise TransportError("invalid_ack_phase")
                size = len(_header(envelope, self.budgets)) + 4
                if self._acks.full() or self._ack_bytes + size > self.budgets.max_control_bytes:
                    raise TransportError("ack_consumer_overflow")
                self._acks.put_nowait((ack, size))
                self._ack_bytes += size
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError, TransportError) as error:
            self._invalidate(str(error)[:128] or type(error).__name__, notify=False)
        finally:
            if not self._closed:
                self.valid = False
                connection.stop()

    async def receive_ack(self) -> TransportAck:
        ack, size = await self._acks.get()
        self._ack_bytes -= size
        return ack

    async def request_resume(self, checkpoint: EngineCursor) -> TransportAck:
        _cursor_valid(checkpoint)
        if (self._closed or self._faulted or len(self._connections) != 2 or self._resume is not None or self._pending
                or (checkpoint.producer, checkpoint.incarnation) != (self.cursor.producer, self.cursor.incarnation)):
            raise TransportError("resume_requires_same_idle_producer_incarnation")
        self.valid = False
        self._resume = asyncio.get_running_loop().create_future()
        try:
            raw = encode_frame(checkpoint, b"", session=self.session, budgets=self.budgets, kind="RESUME")
            if not self._connections["control"].out.put(raw):
                raise TransportError("control_admission_overflow")
            ack = await asyncio.wait_for(self._resume, self.budgets.io_timeout_seconds)
            if ack.cursor != checkpoint:
                raise TransportError("resume_checkpoint_mismatch")
            if ack.phase == "RESUMED":
                if self._faulted or any(connection.writer.is_closing() for connection in self._connections.values()):
                    raise TransportError("resume_raced_with_disconnect_or_gap")
                self.cursor = checkpoint
                self._receipts.clear()
                self.valid = True
            return ack
        except BaseException:
            self._invalidate("resume_result_uncertain")
            raise
        finally:
            self._resume = None

    async def heartbeat(self) -> bool:
        if self._closed or "control" not in self._connections:
            return False
        raw = encode_frame(self.cursor, b"", session=self.session, budgets=self.budgets, kind="PING")
        if not self._connections["control"].out.put(raw):
            self._invalidate("control_admission_overflow", notify=False)
            return False
        return True

    async def _watch(self):
        interval = min(1.0, self.budgets.max_age_seconds / 2, self.budgets.io_timeout_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            if self._pending and now - next(iter(self._pending.values()))[2] > self.budgets.max_age_seconds:
                self._invalidate("market_oldest_age_exceeded")
            control = self._connections["control"]
            if now - control.last_activity > self.budgets.io_timeout_seconds:
                self._invalidate("control_lease_expired", notify=False)
                control.stop()
            else:
                await self.heartbeat()

    @property
    def stats(self):
        now = time.monotonic()
        return {"valid": self.valid, "accepted": self._accepted, "applied": self._applied,
            "failed": self._failed, "pending": len(self._pending),
            "retained_bytes": sum(len(value[0]) for value in self._pending.values()),
            "oldest_age_seconds": now - next(iter(self._pending.values()))[2] if self._pending else 0,
            "ack_records": self._acks.qsize(), "ack_bytes": self._ack_bytes}

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._invalidate("client_closed", notify=False)
        connections = tuple(self._connections.values())
        for connection in connections:
            connection.stop()
        tasks = [task for connection in connections for task in (connection.task, connection.writer_task) if task]
        if self._watchdog:
            self._watchdog.cancel()
            tasks.append(self._watchdog)
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(connection.writer.wait_closed() for connection in connections), return_exceptions=True)
