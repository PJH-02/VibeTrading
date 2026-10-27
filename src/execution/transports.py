"""Bounded HTTP transport and Arcus transport composition.

The HTTP mutation callback is a synchronous, one-use permit at aiohttp's
``ClientRequest.send`` boundary. It runs only after connector acquisition and
immediately before aiohttp hands request headers to the connection. That is a
local dispatch commitment, not proof that a venue received the request. There
are no mutation retries, redirects, proxies, environment settings, cookies,
streaming bodies, middleware, or user supplied tracing hooks.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import ssl
from contextvars import ContextVar
from typing import Any, Callable, Iterable
from urllib.parse import urlencode, urlsplit

import aiohttp
from yarl import URL

from execution.perp_execution import HttpResponse


class TransportError(RuntimeError):
    """Base error for transport policy and bounded-resource failures."""


class TransportClosed(TransportError):
    pass


class OriginNotAllowed(ValueError):
    pass


class ResponseTooLarge(TransportError):
    pass


class TransportCapacityExceeded(TransportError):
    pass


class _DispatchPermit:
    """Shared across any aiohttp retry attempt, so authorization is one-use."""

    __slots__ = ("callback", "used")

    def __init__(self, callback: Callable[[], Any] | None) -> None:
        self.callback = callback
        self.used = False

    def commit(self) -> None:
        callback = self.callback
        if callback is None:
            return
        if self.used:
            raise TransportError("Mutation dispatch permit was already consumed")
        self.used = True
        result = callback()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("before_send callback must be synchronous")


_CURRENT_DISPATCH: ContextVar[_DispatchPermit | None] = ContextVar(
    "cap01_http_dispatch_permit", default=None
)


class _PermitClientRequest(aiohttp.ClientRequest):
    """Capture a per-call guard and consume it at the real send seam."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cap01_dispatch = _CURRENT_DISPATCH.get()

    async def send(self, conn):  # type: ignore[override]
        guard = self._cap01_dispatch
        if guard is not None:
            guard.commit()
        return await super().send(conn)


def _parts(url: str):
    try:
        parsed = urlsplit(url)
        # Accessing .port also validates malformed port strings.
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise OriginNotAllowed("Malformed URL or port") from exc
    if parsed.username is not None or parsed.password is not None:
        raise OriginNotAllowed("URL userinfo is not permitted")
    if parsed.fragment:
        raise OriginNotAllowed("URL fragments are not permitted")
    host = parsed.hostname
    if not host:
        raise OriginNotAllowed("URL requires an explicit host")
    return parsed, host.lower(), port


def _origin(value: str, *, config: bool) -> str:
    parsed, host, port = _parts(value)
    if parsed.scheme.lower() != "https":
        raise OriginNotAllowed("Only explicit HTTPS origins are permitted")
    if config and (parsed.path or parsed.query or "?" in value or "#" in value):
        raise OriginNotAllowed("Origin configuration cannot contain path or query")
    if "*" in host:
        raise OriginNotAllowed("Wildcard hosts are not permitted")
    effective_port = port or 443
    rendered_host = f"[{host}]" if ":" in host else host
    return f"https://{rendered_host}:{effective_port}"


def _allowlist(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("allowed_origins must be an explicit iterable of origins")
    try:
        origins = frozenset(_origin(value, config=True) for value in values)
    except TypeError as exc:
        raise ValueError("allowed_origins must be an explicit iterable") from exc
    if not origins:
        raise ValueError("An explicit nonempty origin allowlist is required")
    return origins


class AioHttpTransport:
    """Own a lazy, bounded aiohttp session for explicitly allowed HTTPS hosts."""

    mutation_permit_protocol = 1

    def __init__(
        self,
        allowed_origins: Iterable[str],
        *,
        timeout_seconds: float = 15,
        max_connections: int = 8,
        max_in_flight: int = 8,
        max_response_bytes: int = 1_048_576,
        max_request_bytes: int = 65_536,
        ssl_context=None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be bounded and positive")
        for name, value in (("max_connections", max_connections),
                            ("max_in_flight", max_in_flight),
                            ("max_response_bytes", max_response_bytes),
                            ("max_request_bytes", max_request_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(name + " must be a positive integer")
        if ssl_context is not None and not isinstance(ssl_context, ssl.SSLContext):
            raise TypeError("ssl_context must be an ssl.SSLContext or None")
        if ssl_context is not None and (ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname):
            raise ValueError("TLS context must verify the peer certificate and hostname")
        self.allowed_origins = _allowlist(allowed_origins)
        self.timeout_seconds = float(timeout_seconds)
        self.max_connections = max_connections
        self.max_in_flight = max_in_flight
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self.ssl_context = ssl_context
        self._session: aiohttp.ClientSession | None = None
        self._closed = False
        # Admission is fail-fast, so there is no unbounded task/semaphore queue.
        self._in_flight = 0

    async def start(self) -> "AioHttpTransport":
        if self._closed:
            raise TransportClosed("Transport was explicitly closed")
        if self._session is not None:
            return self
        connector = aiohttp.TCPConnector(
            limit=self.max_connections,
            limit_per_host=self.max_connections,
            force_close=True,
            enable_cleanup_closed=True,
            ssl=self.ssl_context,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            auto_decompress=False,
            skip_auto_headers=frozenset({"Accept-Encoding"}),
            request_class=_PermitClientRequest,
        )
        return self

    async def close(self) -> None:
        self._closed = True
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    aclose = close

    async def __aenter__(self) -> "AioHttpTransport":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise TransportClosed("Transport session is not started or has been closed")
        return self._session

    def _check_url(self, url: str) -> None:
        if _origin(url, config=False) not in self.allowed_origins:
            raise OriginNotAllowed("Exact HTTPS origin is not in the caller-provided allowlist")

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        try:
            return json.dumps(value, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("json_body must contain finite JSON values") from exc

    @staticmethod
    def _decode(payload: bytes) -> Any:
        if not payload:
            return {}
        try:
            return json.loads(
                payload.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return {"text": payload.decode("utf-8", errors="replace")}

    async def _read_bounded(self, response: aiohttp.ClientResponse) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await response.content.read(min(65536, self.max_response_bytes + 1 - total))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self.max_response_bytes:
                raise ResponseTooLarge("Response exceeds the configured byte bound")
            chunks.append(chunk)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | list | None = None,
        data: str | bytes | bytearray | None = None,
        headers: dict | None = None,
        before_send: Callable[[], Any] | None = None,
    ) -> HttpResponse:
        self._check_url(url)
        method = str(method).upper()
        if not method or any(char.isspace() for char in method):
            raise ValueError("An explicit HTTP method is required")
        if json_body is not None and data is not None:
            raise ValueError("Provide either json_body or data")
        if json_body is not None and not isinstance(json_body, (dict, list)):
            raise TypeError("json_body must be an in-memory dict or list")
        if data is not None and not isinstance(data, (str, bytes, bytearray)):
            raise TypeError("data must be an in-memory body")
        if params is not None and not isinstance(params, dict):
            raise TypeError("params must be an insertion-ordered dict")
        if headers is not None and not isinstance(headers, dict):
            raise TypeError("headers must be a dict")
        if headers and any(key.lower() in {"expect", "transfer-encoding"} for key in headers):
            raise ValueError("Expect and Transfer-Encoding headers are not supported")
        if before_send is not None:
            if not callable(before_send):
                raise TypeError("before_send must be callable")
            if inspect.iscoroutinefunction(before_send) or inspect.iscoroutinefunction(getattr(before_send, "__call__", None)):
                raise TypeError("before_send must be synchronous")

        # Use encoded=True after appending urllib's exact insertion-ordered query
        # bytes. yarl/aiohttp must not requote signed query parameters.
        query = urlencode(params, doseq=True) if params is not None else ""
        request_url = url
        if query:
            request_url += ("&" if urlsplit(url).query else "?") + query
        encoded_url = URL(request_url, encoded=True)
        body = self._json_bytes(json_body) if json_body is not None else data
        request_headers = dict(headers or {})
        if json_body is not None and not any(key.lower() == "content-type" for key in request_headers):
            request_headers["Content-Type"] = "application/json"
        if isinstance(body, bytearray):
            body = bytes(body)
        body_size = len(body.encode("utf-8")) if isinstance(body, str) else len(body or b"")
        if body_size > self.max_request_bytes:
            raise ValueError("Request body exceeds the configured byte bound")
        supplied_length = next((value for key, value in request_headers.items()
                                if key.lower() == "content-length"), None)
        if supplied_length is not None:
            try:
                if int(supplied_length) != body_size:
                    raise ValueError("Content-Length does not match the bounded in-memory body")
            except (TypeError, ValueError) as exc:
                raise ValueError("Content-Length does not match the bounded in-memory body") from exc
        if self._in_flight >= self.max_in_flight:
            raise TransportCapacityExceeded("Maximum in-flight request capacity is full")
        self._in_flight += 1
        guard = _DispatchPermit(before_send)
        token = _CURRENT_DISPATCH.set(guard)  # Set on every call, including read calls.
        try:
            await self.start()
            session = self._require_session()
            async with asyncio.timeout(self.timeout_seconds):
                async with session.request(
                    method,
                    encoded_url,
                    data=body,
                    headers=request_headers,
                    allow_redirects=False,
                    auto_decompress=False,
                    compress=False,
                    expect100=False,
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                ) as response:
                    payload = await self._read_bounded(response)
                    return HttpResponse(status=int(response.status), body=self._decode(payload),
                                        headers=dict(response.headers))
        finally:
            _CURRENT_DISPATCH.reset(token)
            self._in_flight -= 1


class ArcusRpcTransport:
    """Reuse an existing Arcus adapter's RPC connection and synchronization."""

    mutation_permit_protocol = 1

    def __init__(self, http_transport, broker, *, expected_ws_url: str) -> None:
        from execution.extended_brokers import ArcusBrokerAdapter

        if type(broker) is not ArcusBrokerAdapter:
            raise TypeError("Arcus transport requires the existing ArcusBrokerAdapter instance")
        if not isinstance(expected_ws_url, str) or not expected_ws_url.startswith("wss://"):
            raise ValueError("An exact registered wss:// URL is required")
        if broker.ws_url != expected_ws_url:
            raise ValueError("Arcus adapter ws_url differs from the registered route")
        if not callable(getattr(http_transport, "request", None)):
            raise TypeError("http_transport must provide the generic HTTP request interface")
        if getattr(http_transport, "mutation_permit_protocol", None) != 1:
            raise ValueError("HTTP transport must implement mutation_permit_protocol=1")
        self.http_transport = http_transport
        self.broker = broker
        self.ws_url = expected_ws_url
        self._bound_scope = self._scope_snapshot()

    def _scope_snapshot(self) -> tuple[Any, Any, Any, Any]:
        account_index = getattr(self.broker, "account_index", None)
        if account_index is None:
            # ArcusBrokerAdapter's public configured subaccount lives here;
            # inspect only this scope field, never any credential/cache entry.
            env = getattr(self.broker, "env", None)
            account_index = env.get("ARCUS_ACCOUNT_INDEX", "0") if isinstance(env, dict) else "0"
        return (
            getattr(self.broker, "ws_url", None),
            getattr(self.broker, "address", None),
            str(account_index),
            getattr(self.broker, "use_testnet", None),
        )

    def _check_scope(self) -> None:
        if self.ws_url != self._bound_scope[0] or self._scope_snapshot() != self._bound_scope:
            raise TransportError("Arcus adapter URL or public account/environment scope changed")

    async def start(self) -> "ArcusRpcTransport":
        start = getattr(self.http_transport, "start", None)
        if callable(start):
            await start()
        return self

    async def close(self) -> None:
        close_http = getattr(self.http_transport, "close", None)
        if callable(close_http):
            await close_http()
        await self.broker.close()

    aclose = close

    async def __aenter__(self) -> "ArcusRpcTransport":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        return await self.http_transport.request(method, url, **kwargs)

    async def rpc(self, url: str, message: dict, *, before_send: Callable[[], Any] | None = None) -> HttpResponse:
        self._check_scope()
        if url != self.ws_url:
            raise ValueError("WS URL is not the exact registered Arcus endpoint")
        if before_send is not None and not callable(before_send):
            raise TypeError("before_send must be callable")

        def dispatch_guard() -> None:
            self._check_scope()
            if before_send is not None:
                result = before_send()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("before_send callback must be synchronous")
            self._check_scope()

        envelope = await self.broker._rpc(
            message,
            mutation=before_send is not None,
            before_send=dispatch_guard,
            raw_response=True,
        )
        if not isinstance(envelope, dict):
            raise TransportError("Arcus adapter returned a malformed raw response")
        try:
            status = int(envelope.get("status", 200))
        except (TypeError, ValueError):
            status = 502
        return HttpResponse(status=status, body=envelope)
