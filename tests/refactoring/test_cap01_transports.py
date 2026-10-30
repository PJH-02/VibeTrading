"""Focused tests for CAP-01 HTTP dispatch and Arcus composition."""
from __future__ import annotations

import asyncio
import ssl

import pytest
from aiohttp import web

from execution.extended_brokers import ArcusBrokerAdapter
from execution.transports import (
    AioHttpTransport,
    ArcusRpcTransport,
    OriginNotAllowed,
    ResponseTooLarge,
    TransportCapacityExceeded,
    TransportClosed,
)


def _cert(tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    # Fixed synthetic loopback-only CA fixture keeps the isolated runner
    # independent of subprocess/OpenSSL access and never represents a secret.
    cert.write_text('''-----BEGIN CERTIFICATE-----
MIIDJTCCAg2gAwIBAgIUczZc/yXTvhsV5XJUmlhYCzQcugowDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MTAyNzEzNDkyOVoXDTM2MTAy
NDEzNDkyOVowFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEAttq23XDskj79i6cHjZkwzu21xmOKHrLayPZ/bM4o91I/
SYhVfal+vqYPhzMDy77NU801wc6Shwrg6BLGA/wujCbftoNxu1lZ87lvSWmQCFST
zDjGQWku0jSnB1Fv3Wjg/GnbFaUq8SZS3HobqMy2rw7wMBL+7i+ZNl89trecJb0M
Ux18bgSbn9HPz9rM6dXPpxiodZg0n+TI6LQW9a3ti1mEl44fe+pfh+QpBgAmiObi
JDyu5y24Ot1+kVKS40lWLrZc2H3FmnbZUFuZJj55PY679D4+oINdpmbGTgHdQ6oM
+m0zEyL3/4Q2lyKtvTeCU1WdVfxwkVYw5O4jzvZPCQIDAQABo28wbTAdBgNVHQ4E
FgQU3M+u8sDGH9go2Zdxuk9/lSiRjpwwHwYDVR0jBBgwFoAU3M+u8sDGH9go2Zdx
uk9/lSiRjpwwDwYDVR0TAQH/BAUwAwEB/zAaBgNVHREEEzARgglsb2NhbGhvc3SH
BH8AAAEwDQYJKoZIhvcNAQELBQADggEBAHASO7fh60PD05txCpVQm+/wrbFrUjiT
EnRz+pgx0DOGS8i46dE3+2WjDVmYtrQbc+rSZAgn3bO3dv8+pNCph0PnkU3WJaqm
sYNtRADqBmZRraBjSeYLXiBQ6YWl7Gc8mg3meRKGyUP5y+5VCJa+xGY39jUtmG92
AKTGH4TDKcBUw0t7pe/WDrHHL7rpE9XKD56Cij4whzWF3SIZz/+Mj0rr/83urdjT
EiosaJFtPNDgiFDQ6i+Sk778NsMGt+zc2eeBZYt/qrNkAlniN508f1N7p9TIM2Aa
+OWlk4heIAn1C94pGURLOFJtVO8lm9BZioh/rXp2ELOeUx9riwDjlMM=
-----END CERTIFICATE-----
''')
    key.write_text('''-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC22rbdcOySPv2L
pweNmTDO7bXGY4oestrI9n9szij3Uj9JiFV9qX6+pg+HMwPLvs1TzTXBzpKHCuDo
EsYD/C6MJt+2g3G7WVnzuW9JaZAIVJPMOMZBaS7SNKcHUW/daOD8adsVpSrxJlLc
ehuozLavDvAwEv7uL5k2Xz22t5wlvQxTHXxuBJuf0c/P2szp1c+nGKh1mDSf5Mjo
tBb1re2LWYSXjh976l+H5CkGACaI5uIkPK7nLbg63X6RUpLjSVYutlzYfcWadtlQ
W5kmPnk9jrv0Pj6gg12mZsZOAd1Dqgz6bTMTIvf/hDaXIq29N4JTVZ1V/HCRVjDk
7iPO9k8JAgMBAAECggEAWUXyBq4Vyhb32cFMJj02/6g92aSKcA95UqlKMBh9Fraw
kFmAB7CDYVgY97SX81btzy6+wqb0kfrlMFjOFW7/WMy351dIZSQsDxqnMyvXNsH4
QLotTQ0p31X2gFOprmsAgSu7W1d+3iqMRPlW3LRJsrEz4fy8OxTBFDMuobEGCsPs
q0ray9k48UEqeM7lA1HCM4PV0JBLLDke5kQ8kWLlrIJ6hdrOOjwR3eIco9NOdaYm
OqsAjfK+Tj7C0XwsoZl/7qq2BU5O2zp8n3+WWpsg+1n1EyvHMsWnq9zK89g5U2ez
KWbbVXZORRkySrKE779UZevwN3TGY1frDKXqx86T+wKBgQDizkauBRy7fCxYVqRk
LqiTUgjRrSXR7n0hEFg898ou2wh01PHqJsTO2prJ2SLG5ONYh51E1wr+ZMjWGm1J
9fxIZSxI0c97AIE8g7NHNZJDLeuyp5RX7MIxdBF5Cx7lOcD1EI/rSu3PstD31hJ0
TOqWYnJrHYVSYDly2v7Wyfl9xwKBgQDOZCW21mvtextn4UboiRGtLrON5E8akKGH
bClweKKlMNFGe8eh5nC0XXxt3o59nca5T3wDPXx5RO+wg+3bXFwu6d6ocTOsNje/
uS+JOodnBs7yGplwv7WFEv57VlPKuIFismf6SXsNHAHSllDzS/+Vgm7GOscSaeJQ
FAg6aFwMrwKBgQC4V7Zk7KzMygF1D37wGmhTzUJzwcrSlYfInvNJIpGHgemepBG4
IiimhFF5K+4zQIh0RU2TTUGXQS1nr/USP0f9Q5Y53aZIsEFagSTvBODLj4BaJhDR
YhL4hQFKo7pJz6ZAyWRgdvdRcGKefbVkJ7wn3P5oh4b8TE+FRfAJfgI1swKBgDPo
pj+lEEILW2o2E6J6jvKSs2A1ktuokqe/wXBjNuz7KTdj/GKw1ZnXhWsQtZ/MZeXH
ovYhqcPKXcp0dAde5GR8N57/0bI3dLAIwHbwPIvh/3yOyha8cMgGbfDRVoO/AfOz
0Uj24Db820x+t99pNuAAjSHYOKdd8CiZ0TP3eWdLAoGAcjTqZtjE6VJYlk4oe1h2
zX45/O7BYAW6t8k6vL/ZctPnF185o85SWggMrE16LK8J41PXhQrvus6utETDrAyU
y0jhW73dXNVSJv2XmlPoiTiyI7txIcI9XH5IsLlrf4gIwOLv8BIzES7rlvpIsnFz
Jba31n+K+utTFePDXIClzf0=
-----END PRIVATE KEY-----
''')
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=str(cert))
    return server_context, client_context


async def _https(tmp_path, handler):
    server_context, client_context = _cert(tmp_path)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"https://localhost:{port}", client_context


def _transport(base, context, **kwargs):
    return AioHttpTransport([base], ssl_context=context, **kwargs)


def test_https_dispatch_permit_exact_signed_octets_status_and_headers(tmp_path):
    async def scenario():
        seen = []

        async def handler(request):
            seen.append((request.raw_path, await request.read()))
            return web.json_response({"accepted": False}, status=409, headers={"X-Fact": "retained"})

        runner, base, context = await _https(tmp_path, handler)
        transport = _transport(base, context)
        calls = []
        try:
            assert transport._session is None
            response = await transport.request(
                "post", base + "/signed?prefix=%2f", params={"client": "Ab:cd/ef", "n": 2},
                data="sig=literal%2F", before_send=lambda: calls.append("permit"),
            )
            assert calls == ["permit"]
            assert transport._session is not None
            assert seen == [("/signed?prefix=%2f&client=Ab%3Acd%2Fef&n=2", b"sig=literal%2F")]
            assert response.status == 409 and response.body == {"accepted": False}
            assert response.headers["X-Fact"] == "retained"
        finally:
            await transport.close()
            await runner.cleanup()
        with pytest.raises(TransportClosed):
            await transport.request("GET", base + "/")
    asyncio.run(scenario())


def test_origin_policy_redirect_response_bound_and_finite_json(tmp_path):
    async def scenario():
        observed = []

        async def handler(request):
            observed.append(request.path)
            if request.path == "/redirect":
                raise web.HTTPFound(location="/mutate")
            if request.path == "/large":
                return web.Response(body=b"x" * 80)
            return web.Response(text='{"value":NaN}', content_type="application/json")

        runner, base, context = await _https(tmp_path, handler)
        transport = _transport(base, context, max_response_bytes=32)
        try:
            await transport.start()
            with pytest.raises(OriginNotAllowed):
                await transport.request("GET", base.replace("localhost", "127.0.0.1") + "/other")
            redirect = await transport.request("GET", base + "/redirect")
            assert redirect.status == 302 and observed == ["/redirect"]
            nonfinite = await transport.request("GET", base + "/nan")
            assert nonfinite.body == {"text": '{"value":NaN}'}
            with pytest.raises(ResponseTooLarge):
                await transport.request("GET", base + "/large")
            assert observed == ["/redirect", "/nan", "/large"]
        finally:
            await transport.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_inflight_admission_is_bounded_and_permit_denial_sends_no_bytes(tmp_path):
    async def scenario():
        reached = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def handler(request):
            seen.append(request.path)
            if request.path == "/hold":
                reached.set()
                await release.wait()
            return web.json_response({"ok": True})

        runner, base, context = await _https(tmp_path, handler)
        transport = _transport(base, context, max_connections=1, max_in_flight=1)
        try:
            await transport.start()
            blocker = asyncio.create_task(transport.request("GET", base + "/hold"))
            await asyncio.wait_for(reached.wait(), 2)
            with pytest.raises(TransportCapacityExceeded):
                await transport.request("POST", base + "/denied", json_body={"x": 1}, before_send=lambda: None)
            release.set()
            await blocker
            calls = []

            def deny():
                calls.append(1)
                raise RuntimeError("revoked")

            with pytest.raises(RuntimeError):
                await transport.request("POST", base + "/mutation", json_body={"x": 1}, before_send=deny)
            assert calls == [1] and seen == ["/hold"]
        finally:
            release.set()
            await transport.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_queued_permit_revoked_after_connector_wait_and_timeout_does_not_retry(tmp_path):
    async def scenario():
        arrived = asyncio.Event()
        release = asyncio.Event()
        received = []

        async def handler(request):
            if request.path == "/hold":
                arrived.set()
                await release.wait()
            else:
                received.append(request.path)
                await request.read()
                if request.path == "/slow":
                    await asyncio.sleep(.15)
            return web.json_response({"ok": True})

        runner, base, context = await _https(tmp_path, handler)
        transport = _transport(base, context, max_connections=1, max_in_flight=2,
                               timeout_seconds=.07)
        try:
            await transport.start()
            # Hold the only connector slot, then revoke while the signed call is
            # waiting for that slot. Cancellation frees the connector; the gate
            # must run once and prevent actual HTTP dispatch.
            blocker = asyncio.create_task(transport.request("GET", base + "/hold"))
            await asyncio.wait_for(arrived.wait(), 1)
            permit = {"live": True, "calls": 0}

            def guarded():
                permit["calls"] += 1
                if not permit["live"]:
                    raise RuntimeError("revoked")

            queued = asyncio.create_task(transport.request(
                "POST", base + "/queued", json_body={"x": 1}, before_send=guarded))
            await asyncio.sleep(.02)
            permit["live"] = False
            blocker.cancel()
            await asyncio.gather(blocker, return_exceptions=True)
            with pytest.raises(RuntimeError, match="revoked"):
                await queued
            assert permit["calls"] == 1 and received == []

            calls = []
            with pytest.raises(TimeoutError):
                await transport.request("POST", base + "/slow", json_body={"x": 1},
                                        before_send=lambda: calls.append("sent"))
            assert calls == ["sent"] and received == ["/slow"]
        finally:
            release.set()
            await transport.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_callback_context_isolated_async_permit_rejected_and_url_config_strict(tmp_path):
    async def scenario():
        seen = []

        async def handler(request):
            seen.append(request.path)
            return web.json_response({"ok": 1})

        runner, base, context = await _https(tmp_path, handler)
        transport = _transport(base, context, max_in_flight=2)
        try:
            await transport.start()
            counts = [0, 0]

            async def one(i):
                def permit():
                    counts[i] += 1
                await transport.request("POST", base + f"/parallel/{i}", json_body={"i": i}, before_send=permit)

            await asyncio.gather(one(0), one(1))

            async def async_permit():
                return None

            with pytest.raises(TypeError):
                await transport.request("POST", base + "/async", before_send=async_permit)
            assert counts == [1, 1] and set(seen) == {"/parallel/0", "/parallel/1"}
        finally:
            await transport.close()
            await runner.cleanup()
    asyncio.run(scenario())
    for invalid in ("http://localhost", "https://user@host", "https://host/path", "https://host?x=1", "https://*.example"):
        with pytest.raises((ValueError, OriginNotAllowed)):
            AioHttpTransport([invalid])
    with pytest.raises(TypeError):
        AioHttpTransport(["https://localhost"], ssl_context=False)
    with pytest.raises(ValueError):
        AioHttpTransport(["https://localhost"], max_request_bytes=0)


def test_manual_expect_transfer_encoding_and_oversized_body_fail_before_lazy_session(tmp_path):
    async def scenario():
        async def handler(request):
            return web.json_response({"ok": True})

        runner, base, context = await _https(tmp_path, handler)
        transport = _transport(base, context, max_request_bytes=4)
        permits = []
        try:
            for headers in ({"Expect": "100-continue"}, {"Transfer-Encoding": "chunked"}):
                with pytest.raises(ValueError):
                    await transport.request("POST", base + "/blocked", data=b"x",
                                            headers=headers, before_send=lambda: permits.append(1))
            with pytest.raises(ValueError, match="body"):
                await transport.request("POST", base + "/large", json_body={"x": "long"},
                                        before_send=lambda: permits.append(1))
            assert permits == [] and transport._session is None
        finally:
            await transport.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_arcus_wrapper_reuses_adapter_and_preserves_raw_rpc_facts():
    class HttpStub:
        mutation_permit_protocol = 1
        def __init__(self): self.calls = []
        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return "http-response"

    async def scenario():
        broker = ArcusBrokerAdapter.__new__(ArcusBrokerAdapter)
        broker.ws_url = "wss://arcus.invalid/v1/ws"
        broker.address = "0x" + "a" * 40
        broker.env = {"ARCUS_ACCOUNT_INDEX": "2"}
        broker.use_testnet = True
        broker.close = lambda: asyncio.sleep(0)
        calls = []
        waiting = False
        entered = asyncio.Event()
        release = asyncio.Event()

        async def rpc(message, **kwargs):
            calls.append((message, kwargs))
            if waiting:
                entered.set()
                await release.wait()
            kwargs["before_send"]()
            return {"id": 4, "status": 422, "error": {"code": "rejected"}}

        broker._rpc = rpc
        http = HttpStub()
        wrapper = ArcusRpcTransport(http, broker, expected_ws_url=broker.ws_url)
        assert await wrapper.request("GET", "https://synthetic.invalid") == "http-response"
        dispatched = []
        result = await wrapper.rpc(broker.ws_url, {"method": "place"}, before_send=lambda: dispatched.append(1))
        assert result.status == 422 and result.body["error"] == {"code": "rejected"}
        assert dispatched == [1]  # owner _rpc invokes the wrapper at its own send seam
        message, options = calls[0]
        assert message == {"method": "place"}
        assert options["mutation"] is True and options["raw_response"] is True
        assert callable(options["before_send"])
        assert len(http.calls) == 1
        with pytest.raises(ValueError):
            await wrapper.rpc("wss://other.invalid/v1/ws", {})

        # Scope mutation while the owner is waiting must be seen at its actual
        # send callback and must suppress the caller's permit.
        for mutate in (
            lambda: setattr(broker, "address", "0x" + "b" * 40),
            lambda: broker.env.__setitem__("ARCUS_ACCOUNT_INDEX", "3"),
            lambda: setattr(broker, "use_testnet", False),
            lambda: setattr(broker, "ws_url", "wss://changed.invalid/v1/ws"),
        ):
            # Restore the bound values before each independent mutation probe.
            broker.address = "0x" + "a" * 40
            broker.env["ARCUS_ACCOUNT_INDEX"] = "2"
            broker.use_testnet = True
            broker.ws_url = wrapper.ws_url
            waiting = True
            entered.clear()
            release.clear()
            caller_permit = []
            task = asyncio.create_task(wrapper.rpc(wrapper.ws_url, {"method": "place"},
                                                   before_send=lambda: caller_permit.append(1)))
            await asyncio.wait_for(entered.wait(), 1)
            mutate()
            release.set()
            with pytest.raises(RuntimeError):
                await task
            assert caller_permit == []
            waiting = False

    asyncio.run(scenario())


def test_arcus_owner_rpc_permit_is_after_lock_connect_and_raw_default_semantics(monkeypatch):
    import json
    from execution import extended_brokers

    class FakeWebSocket:
        def __init__(self): self.sent = []; self.responses = []
        async def send(self, value): self.sent.append(value)
        async def recv(self): return self.responses.pop(0)
        async def close(self): pass

    async def scenario():
        ws = FakeWebSocket()
        connect_calls = []

        async def connect(url, **kwargs):
            connect_calls.append(url)
            return ws

        monkeypatch.setattr(extended_brokers.websockets, "connect", connect)
        broker = ArcusBrokerAdapter.__new__(ArcusBrokerAdapter)
        broker.name = "arcus"
        broker.ws_url = "wss://arcus.invalid/v1/ws"
        broker._ws = None
        broker._rpc_lock = asyncio.Lock()
        broker._request_id = 0
        ws.responses.append(json.dumps({"id": 1, "status": 422, "error": {"code": "no"}}))
        # Lock contention is resolved before connect and permit invocation.
        await broker._rpc_lock.acquire()
        permitted = []
        task = asyncio.create_task(broker._rpc({"method": "trade"}, mutation=True,
                                              before_send=lambda: permitted.append("dispatch"),
                                              raw_response=True))
        await asyncio.sleep(0)
        assert connect_calls == [] and permitted == []
        broker._rpc_lock.release()
        await asyncio.sleep(0)
        assert connect_calls == [broker.ws_url] and permitted == ["dispatch"]
        raw = await task
        assert raw["status"] == 422 and raw["error"] == {"code": "no"}
        assert len(ws.sent) == 1

        ws.responses.append(json.dumps({"id": 2, "status": 200, "result": {"ok": True}}))
        default = await broker._rpc({"method": "read"})
        assert default == {"ok": True}
        ws.responses.append(json.dumps({"id": 3, "status": 422, "error": {"code": "no"}}))
        with pytest.raises(RuntimeError, match="RPC rejected"):
            await broker._rpc({"method": "reject"})

        ws.sent.clear()
        def deny(): raise RuntimeError("revoked")
        with pytest.raises(RuntimeError):
            await broker._rpc({"method": "blocked"}, mutation=True, before_send=deny, raw_response=True)
        assert ws.sent == []

    asyncio.run(scenario())
