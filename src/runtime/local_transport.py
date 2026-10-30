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
from operations.dashboard.latency import observe

from feeds.feed_recovery import EngineCursor, RecoveryFrame
from execution.process_lease import ProcessLease


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


_KINDS = {"HELLO", "HELLO_ACK", "DATA", "REPLAY", "ACK", "RESUME", "QUERY", "COMMIT", "PING", "GAP", "COMMAND", "RESULT"}
_PAYLOAD_KINDS = {"DATA", "REPLAY", "COMMAND", "RESULT"}
_PHASES = {"", "RECEIVED", "APPLIED", "DURABLE", "RESUMED", "REJECTED", "PONG", "GAP", "QUERY", "BLOCKED"}
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
            or (envelope.kind in _PAYLOAD_KINDS and not envelope.payload)
            or (envelope.kind not in _PAYLOAD_KINDS and envelope.payload)
            or (envelope.kind in {'COMMAND','RESULT'} and len(envelope.payload) + budgets.max_header_bytes + 4 > budgets.max_control_bytes)):
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
    if (not isinstance(header, dict) or set(header) != _FIELDS or type(header["kind"]) is not str
            or type(header["v"]) is not int
            or header["v"] != 1 or type(header["payload_size"]) is not int
            or not 0 <= header["payload_size"] <= budgets.max_frame_bytes):
        raise TransportError("invalid_header")
    try:
        cursor = EngineCursor(header["producer"], header["incarnation"], header["sequence"])
    except ValueError as error:
        raise TransportError("invalid_cursor") from error
    # Validate every bounded header field before reading a possibly large body.
    envelope = _Envelope(header["kind"], header["session"], cursor,
        b"x" if header["payload_size"] and header["kind"] in _PAYLOAD_KINDS else b"",
        header["digest"], header["phase"], header["reason"])
    _header(envelope, budgets)
    if _allowed_kinds is not None and header["kind"] not in _allowed_kinds:
        raise TransportError("message_kind_not_allowed_on_lane")
    if header["kind"] in {'COMMAND','RESULT'} and header['payload_size'] + size + 4 > budgets.max_control_bytes:
        raise TransportError('command_frame_budget_exceeded')
    if header["kind"] not in _PAYLOAD_KINDS and header["payload_size"] != 0:
        raise TransportError("control_payload_forbidden")
    if header["kind"] in _PAYLOAD_KINDS and header["payload_size"] == 0:
        raise TransportError("empty_market_frame")
    if _reserve is not None and header["kind"] in _PAYLOAD_KINDS:
        _reserve(4 + size + header["payload_size"])
    payload = await reader.readexactly(header["payload_size"])
    # ACK digest describes its referenced market payload, not the empty body.
    # COMMIT carries an empty body but references the digest at its high-water
    # cursor, just as ACK refers to the applied payload. Other frames prove
    # their own body digest here.
    if header["kind"] not in {"ACK", "COMMIT"} and hashlib.sha256(payload).hexdigest() != header["digest"]:
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


def validate_command_result(request, result):
    if type(result) is dict and result == {'status': 'REJECTED', 'reason': 'command_rejected'}:
        return result
    scope = request['scope']
    selected = result.get('scope') if type(result) is dict else None
    valid_scope = scope in {'global','account','strategy'} and (selected == 'global' if scope == 'global' else
                   selected == 'strategy:' + request['strategy'] if scope == 'strategy' else
                   isinstance(selected, str) and selected.startswith('account:') and 8 < len(selected) <= 208)
    if (type(result) is not dict or set(result) != {'command_id','status','action','scope','epoch','duplicate'}
            or result['status'] != 'ACCEPTED' or result['command_id'] != request['command_id']
            or result['action'] != request['action'] or not valid_scope
            or type(result['epoch']) is not int or result['epoch'] <= 0 or type(result['duplicate']) is not bool
            or (request['action'] == 'resume' and (type(request.get('expected_epochs')) is not dict
                or result['epoch'] != request['expected_epochs'].get(selected, -2) + 1))):
        raise TransportError('unverified_owner_command_receipt')
    return result


async def request_command(endpoint, request, *, budgets: TransportBudgets, allowed_uid: int):
    """One command on the existing control socket; retry the identical request after loss."""
    _profile('linux-uds', endpoint)
    if type(allowed_uid) is not int or allowed_uid < 0:
        raise TransportError('explicit_allowed_peer_uid_required')
    payload = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    session, cursor = uuid.uuid4().hex, EngineCursor('control', 'command', 0)
    raw = encode_frame(cursor, payload, session=session, budgets=budgets, kind='COMMAND')
    async with asyncio.timeout(min(budgets.io_timeout_seconds, budgets.max_age_seconds)):
        reader, writer = await asyncio.open_unix_connection(str(endpoint) + '.control', limit=budgets.max_header_bytes + 4)
        try:
            _, uid, _ = struct.unpack('3i', writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != allowed_uid:
                raise TransportError('server_peer_uid_not_allowed')
            writer.write(raw)
            await writer.drain()
            reply = await read_frame(reader, budgets, _allowed_kinds={'RESULT'})
            if reply.session != session or reply.cursor != cursor:
                raise TransportError('invalid_command_reply_identity')
            return validate_command_result(request, json.loads(reply.payload, object_pairs_hook=_object_pairs))
        except (asyncio.IncompleteReadError, ConnectionError) as error:
            raise TransportError('command_reply_lost') from error
        finally:
            writer.close()
            await writer.wait_closed()


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
        self.command_reservation = False
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
    recovery_high_water: EngineCursor | None = None


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
                 on_replay=None, on_recovery_query=None, on_recovery_commit=None,
                 on_command=None, allowed_uid: int | None = None, profile="linux-uds"):
        _profile(profile, endpoint)
        if profile == "linux-uds" and (type(allowed_uid) is not int or allowed_uid < 0):
            raise TransportError("explicit_allowed_peer_uid_required")
        self.endpoint, self.budgets, self.profile = endpoint, budgets, profile
        self.on_frame, self.on_gap, self.on_resume = on_frame, on_gap, on_resume
        self.on_replay,self.on_recovery_query,self.on_recovery_commit=on_replay,on_recovery_query,on_recovery_commit
        self.allowed_uid = allowed_uid
        self.on_command = on_command
        self.addresses = {}
        self._servers, self._connections, self._sessions = [], set(), {}
        self._paths, self._lease, self._watchdog = {}, None, None
        self._closed = False
        self._in_flight_bytes = self._in_flight_records = 0
        self._command_bytes = self._command_records = 0

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
        limit = self.budgets.max_sessions + (self.budgets.max_control_records if lane == 'control' and self.on_command is not None else 0)
        if self._closed or sum(c.lane == lane for c in self._connections) >= limit:
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

    def _reserve(self, connection, size, *, command=False):
        if command:
            if (connection.lane != 'control' or connection.reserved
                    or self._command_bytes + size > self.budgets.max_control_bytes
                    or self._command_records >= self.budgets.max_control_records):
                raise TransportError('server_command_admission_overflow')
            connection.reserved, connection.command_reservation = size, True
            self._command_bytes += size
            self._command_records += 1
            return
        if (connection.lane != ('control' if command else 'data') or connection.reserved
                or self._in_flight_bytes + size > self.budgets.max_bytes
                or self._in_flight_records >= self.budgets.max_records):
            raise TransportError("server_market_admission_overflow")
        connection.reserved = size
        self._in_flight_bytes += size
        self._in_flight_records += 1

    def _release(self, connection):
        if connection.reserved:
            if connection.command_reservation:
                self._command_bytes -= connection.reserved
                self._command_records -= 1
            else:
                self._in_flight_bytes -= connection.reserved
                self._in_flight_records -= 1
            connection.reserved = 0
            connection.command_reservation = False

    async def _serve(self, connection):
        session = None
        reason = "disconnected"
        try:
            hello = await read_frame(connection.reader, self.budgets,
                _reserve=lambda size: self._reserve(connection, size, command=True),
                _on_start=lambda: setattr(connection, "frame_started", time.monotonic()),
                _allowed_kinds={"HELLO", "COMMAND"} if connection.lane == 'control' else {'HELLO'})
            connection.frame_started = None
            connection.last_activity = time.monotonic()
            if hello.kind == 'COMMAND':
                await self._command(connection, hello)
                return
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
                        _allowed_kinds={"DATA", "REPLAY"} if connection.lane == "data" else {"PING", "RESUME", "QUERY", "COMMIT", "GAP"})
                    connection.last_activity = time.monotonic()
                    if (envelope.session != session.identity or
                        (envelope.cursor.producer, envelope.cursor.incarnation) !=
                        (session.checkpoint.producer, session.checkpoint.incarnation)):
                        raise TransportError("session_or_incarnation_changed")
                    if connection.lane == "control":
                        self._control(session, envelope)
                    elif envelope.kind in {"DATA", "REPLAY"}:
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
            self._release(connection)
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

    async def _command(self, connection, envelope):
        if self.on_command is None:
            raise TransportError('command_handler_unavailable')
        request = json.loads(envelope.payload, object_pairs_hook=_object_pairs)
        try:
            result = _call(self.on_command, request)
        except (ValueError, RuntimeError):
            result = {'status': 'REJECTED', 'reason': 'command_rejected'}
        except Exception as error:
            raise TransportError('owner_command_failed') from error
        payload = json.dumps(result, separators=(',', ':'), allow_nan=False).encode()
        raw = encode_frame(envelope.cursor, payload, session=envelope.session,
                           budgets=self.budgets, kind='RESULT')
        connection.writer.write(raw)
        await asyncio.wait_for(connection.writer.drain(), self.budgets.io_timeout_seconds)

    def _control(self, session, envelope):
        if envelope.kind == "PING":
            self._send(session, "PONG", envelope.cursor)
        elif envelope.kind == "GAP":
            self._invalidate(session, envelope.reason or "producer_gap")
        elif envelope.kind == "RESUME":
            if session.busy or session.data is None:
                self._send(session, "REJECTED", envelope.cursor, reason="resume_busy_or_missing_data_lane")
                return
            result=_call(self.on_resume,envelope.cursor)
            if isinstance(result,AppliedReceipt):
                if result.cursor!=envelope.cursor:
                    self._send(session,"REJECTED",envelope.cursor,reason="resume_checkpoint_mismatch")
                    return
                resume_digest=result.digest
            elif result is True:
                # Compatibility with the original boolean RESUME callback.
                resume_digest=hashlib.sha256(b"").hexdigest()
            else:
                self._send(session, "REJECTED", envelope.cursor, reason="resume_not_verified")
                return
            session.received = session.applied = envelope.cursor
            session.history.clear()
            session.valid = True
            self._send(session, "RESUMED", envelope.cursor,resume_digest)
        elif envelope.kind == "QUERY":
            high_water=envelope.cursor
            if session.busy or session.data is None or self.on_recovery_query is None:
                self._send(session,"REJECTED",high_water,reason="recovery_query_unavailable")
                return
            try:
                result=_call(self.on_recovery_query,high_water)
            except Exception:
                self._send(session,"REJECTED",high_water,reason="recovery_query_failed")
                return
            if (not isinstance(result,AppliedReceipt)
                    or (result.cursor.producer,result.cursor.incarnation)!=(high_water.producer,high_water.incarnation)
                    or result.cursor.sequence>high_water.sequence):
                self._send(session,"REJECTED",high_water,reason="recovery_checkpoint_unverified")
                return
            session.checkpoint=high_water
            session.recovery_high_water=high_water
            session.received=session.applied=result.cursor
            session.history.clear()
            session.history[result.cursor.sequence]=result.digest
            session.valid=False
            self._send(session,"QUERY",result.cursor,result.digest)
        elif envelope.kind == "COMMIT":
            high_water=envelope.cursor
            if (session.recovery_high_water!=high_water or session.applied!=high_water
                    or session.history.get(high_water.sequence)!=envelope.digest
                    or self.on_recovery_commit is None):
                self._send(session,"BLOCKED",high_water,envelope.digest,reason="recovery_not_ready")
                return
            try:
                committed=_call(self.on_recovery_commit,high_water,envelope.digest)
            except Exception:
                self._invalidate(session,"recovery_commit_failed")
                self._send(session,"BLOCKED",high_water,envelope.digest,reason="recovery_commit_failed")
                return
            if committed is not True:
                self._send(session,"BLOCKED",high_water,envelope.digest,reason="owner_readiness_blocked")
                return
            session.valid=True
            session.checkpoint=high_water
            self._send(session,"RESUMED",high_water,envelope.digest)
        else:
            raise TransportError("invalid_control_message")

    def _data(self, session, envelope):
        if envelope.kind == "REPLAY":
            if (session.valid or session.recovery_high_water is None or session.applied is None
                    or envelope.cursor.sequence != session.applied.sequence+1
                    or envelope.cursor.sequence > session.recovery_high_water.sequence
                    or self.on_replay is None):
                self._send(session,"REJECTED",envelope.cursor,envelope.digest,"replay_outside_recovery")
                return
            frame=RecoveryFrame(envelope.cursor,envelope.payload)
            session.received=frame.cursor;session.busy=True
            try:
                result=_call(self.on_replay,frame)
                if not isinstance(result,AppliedReceipt) or result.cursor!=frame.cursor or result.digest!=frame.digest:
                    raise TransportError("replay_application_receipt_mismatch")
                session.applied=frame.cursor
                session.history[frame.cursor.sequence]=frame.digest
                while len(session.history)>self.budgets.max_records:
                    session.history.popitem(last=False)
                self._send(session,"APPLIED",frame.cursor,frame.digest)
            except Exception as error:
                self._invalidate(session,"replay_application_failed")
                self._send(session,"REJECTED",frame.cursor,frame.digest,type(error).__name__)
            finally:
                session.busy=False
            return
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
        self._query = None
        self._commit = None
        self._watchdog = None
        self._accepted = self._applied = self._failed = 0
        self._faulted = False
        self._last_ack_phase = self._last_ack_reason = self._last_error_type = ""

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

    def try_send(self, frame: RecoveryFrame, *, replay=False) -> bool:
        if self._closed or self._faulted or "data" not in self._connections or (not replay and not self.valid):
            return False
        try:
            _cursor_valid(frame.cursor)
            if ((frame.cursor.producer, frame.cursor.incarnation) != (self.cursor.producer, self.cursor.incarnation)
                    or frame.cursor.sequence != self.cursor.sequence + 1):
                raise TransportError("producer_cursor_gap_or_restart")
            raw = encode_frame(frame.cursor, frame.payload, session=self.session, budgets=self.budgets,
                               kind="REPLAY" if replay else "DATA")
            retained = sum(len(item[0]) for item in self._pending.values())
            if self._pending and time.monotonic() - next(iter(self._pending.values()))[2] > self.budgets.max_age_seconds:
                raise TransportError("market_oldest_age_exceeded")
            if len(self._pending) >= self.budgets.max_records or retained + len(raw) > self.budgets.max_bytes:
                raise TransportError("market_admission_overflow")
        except (TransportError, ValueError) as error:
            self._invalidate(str(error))
            return False
        self._pending[frame.cursor.sequence] = (raw, frame.digest, time.monotonic(), False,None)
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
                seq, (raw, digest, stamp, _, _) = item
                send_started_ns=time.monotonic_ns()
                self._pending[seq] = (raw, digest, stamp, True,send_started_ns)
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
                self._last_ack_phase,self._last_ack_reason=ack.phase,ack.reason
                expected_control_rejection = False
                if ack.phase == "PONG" and ack.reason.startswith("hello_"):
                    if self._hello is not None and not self._hello.done():
                        self._hello.set_result(ack)
                    continue
                if ack.phase in {"QUERY","REJECTED"} and self._query is not None:
                    if not self._query.done():
                        self._query.set_result(ack)
                        expected_control_rejection = ack.phase == "REJECTED"
                    continue
                if ack.phase in {"RESUMED","BLOCKED","REJECTED"} and self._commit is not None:
                    if not self._commit.done():
                        self._commit.set_result(ack)
                        expected_control_rejection = ack.phase == "REJECTED"
                    continue
                if ack.phase in {"RESUMED", "REJECTED"} and self._resume is not None:
                    if not self._resume.done():
                        self._resume.set_result(ack)
                        expected_control_rejection = ack.phase == "REJECTED"
                    continue
                if ack.phase == "GAP":
                    self._invalidate(ack.reason or "server_gap", notify=False)
                elif ack.phase in {"RECEIVED", "APPLIED"}:
                    item = self._pending.get(ack.cursor.sequence)
                    if item is None or item[1] != ack.digest:
                        raise TransportError("ack_cursor_or_digest_mismatch")
                    if ack.phase == "APPLIED":
                        ack_ns=time.monotonic_ns()
                        observe("local_transport_applied_ack",started_ns=item[4],ended_ns=ack_ns,
                                source_id=self.cursor.producer,queue_depth=self._acks.qsize())
                        self._pending.pop(ack.cursor.sequence)
                        self._receipts[ack.cursor.sequence] = ack.digest
                        self._applied += 1
                        while len(self._receipts) > self.budgets.max_records:
                            self._receipts.popitem(last=False)
                elif ack.phase == "DURABLE":
                    if self._receipts.get(ack.cursor.sequence) != ack.digest:
                        raise TransportError("durable_receipt_not_applied")
                elif ack.phase == "REJECTED" and not expected_control_rejection:
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
            self._last_error_type=type(error).__name__
            self._invalidate(str(error)[:128] or type(error).__name__, notify=False)
        finally:
            if not self._closed:
                self.valid = False
                connection.stop()

    async def receive_ack(self) -> TransportAck:
        ack, size = await self._acks.get()
        self._ack_bytes -= size
        return ack

    def receive_ack_nowait(self) -> TransportAck:
        """Remove one already queued ACK without waiting for another frame."""
        ack, size = self._acks.get_nowait()
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

    async def query_cursor(self, high_water: EngineCursor) -> TransportAck:
        _cursor_valid(high_water)
        if (self._closed or self._faulted or len(self._connections)!=2 or self._query is not None
                or self._pending or (high_water.producer,high_water.incarnation)!=(self.cursor.producer,self.cursor.incarnation)):
            raise TransportError("query_requires_same_idle_producer_incarnation")
        self.valid=False
        self._query=asyncio.get_running_loop().create_future()
        try:
            raw=encode_frame(high_water,b"",session=self.session,budgets=self.budgets,
                             kind="QUERY",digest=hashlib.sha256(b"").hexdigest())
            if not self._connections["control"].out.put(raw):
                raise TransportError("control_admission_overflow")
            ack=await asyncio.wait_for(self._query,self.budgets.io_timeout_seconds)
            if ack.phase!="QUERY" or ack.cursor.sequence>high_water.sequence:
                raise TransportError("recovery_query_result_invalid")
            self.cursor=ack.cursor
            self._receipts.clear()
            return ack
        except BaseException:
            self._invalidate("recovery_query_result_uncertain")
            raise
        finally:
            self._query=None

    async def commit_recovery(self, high_water: EngineCursor, digest: str) -> bool:
        _cursor_valid(high_water)
        if (self._closed or self._faulted or len(self._connections)!=2 or self._commit is not None
                or self._pending or (high_water.producer,high_water.incarnation)!=(self.cursor.producer,self.cursor.incarnation)
                or high_water.sequence!=self.cursor.sequence):
            raise TransportError("recovery_commit_requires_exact_idle_high_water")
        self._commit=asyncio.get_running_loop().create_future()
        try:
            raw=encode_frame(high_water,b"",session=self.session,budgets=self.budgets,
                             kind="COMMIT",digest=digest)
            if not self._connections["control"].out.put(raw):
                raise TransportError("control_admission_overflow")
            ack=await asyncio.wait_for(self._commit,self.budgets.io_timeout_seconds)
            if ack.phase=="BLOCKED":return False
            if ack.phase!="RESUMED" or ack.cursor!=high_water or ack.digest!=digest:
                raise TransportError("recovery_commit_result_invalid")
            self.cursor=high_water
            self.valid=True
            return True
        except BaseException:
            self._invalidate("recovery_commit_result_uncertain")
            raise
        finally:
            self._commit=None

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
            "ack_records": self._acks.qsize(), "ack_bytes": self._ack_bytes,
            "last_ack_phase":self._last_ack_phase,"last_ack_reason":self._last_ack_reason,
            "last_error_type":self._last_error_type,"faulted":self._faulted}

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
