"""TX00 TOSS DATA_ONLY regression tests - mutation policy enforcement.

Every test is an ordinary synchronous pytest function that drives async code
with ``asyncio.run`` (like the existing recovery test module), so the exact
repository pytest command works without any asyncio plugin.  Blocked paths
must reject before any intent attribute, credential, token, or transport
access; the bomb helpers prove that without real network or credentials.
"""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibetrading.brokers import TossBrokerAdapter
from vibetrading.providers.toss import TossAuthClient, TossUnauthorized
from vibetrading.settings import RuntimeSettings


MUTATING_METHODS = ["POST", "PUT", "PATCH", "DELETE", "HEAD", "post", "Put", "PaTcH"]
# A method that is not a string cannot be proven to be a read, so the DATA_ONLY
# gate must reject it too - before authentication or any client construction.
INVALID_METHODS = [None, 7, b"GET", ["GET"], object()]


class _Bomb:
    """Any attribute access or call on this object fails the test."""

    def __init__(self, name: str = "bomb") -> None:
        self.name = name

    def __getattr__(self, item: str):
        raise AssertionError(f"unexpected {self.name} attribute access: {item}")

    def __call__(self, *args, **kwargs):
        raise AssertionError(f"unexpected {self.name} call")


class _CountingBomb:
    """Counts invocations so a blocked path can assert exactly zero calls."""

    def __init__(self, name: str = "bomb") -> None:
        self.name = name
        self.calls = 0

    def __getattr__(self, item: str):
        raise AssertionError(f"unexpected {self.name} attribute access: {item}")

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError(f"{self.name} must never be invoked")


class _FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Synthetic aiohttp.ClientSession stand-in built on async context managers."""

    def __init__(self, *, get_response: _FakeResponse | None = None,
                 post_response: _FakeResponse | None = None) -> None:
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []
        self._get_response = get_response
        self._post_response = post_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _FakeContext(self._get_response)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _FakeContext(self._post_response)


def _patch_session(monkeypatch, session) -> None:
    monkeypatch.setattr(
        "vibetrading.providers.toss.aiohttp.ClientSession",
        lambda **kwargs: session,
    )


def _bomb_adapter() -> TossBrokerAdapter:
    """Adapter built without __init__: a bomb client proves no client access."""
    adapter = object.__new__(TossBrokerAdapter)
    adapter.client = _Bomb("toss client")
    return adapter


def _client(settings: RuntimeSettings | None = None) -> TossAuthClient:
    settings = settings or MagicMock(spec=RuntimeSettings)
    settings.env = {}
    return TossAuthClient(settings)


def _client_for(tmp_path: Path) -> TossAuthClient:
    env = tmp_path / ".env"
    env.write_text("VIBETRADING_DATA_ROOT=" + tmp_path.as_posix())
    return TossAuthClient(RuntimeSettings.from_env(env))


def test_broker_submit_order_rejects_before_intent_processing():
    """submit_order must raise DATA_ONLY without reading any attribute."""
    adapter = _bomb_adapter()
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: submit_order rejected"):
        asyncio.run(adapter.submit_order(None))


def test_broker_submit_order_rejects_bomb_intent_without_attribute_reads():
    """A bomb intent proves the blocked method never touches intent fields."""
    adapter = _bomb_adapter()
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: submit_order rejected"):
        asyncio.run(adapter.submit_order(_Bomb("order intent")))


@pytest.mark.parametrize("method", MUTATING_METHODS)
def test_auth_client_request_rejects_mutating_methods_before_authentication(method):
    """request() must reject non-GET methods before access_token/refresh."""
    client = _client()
    token_bomb = _CountingBomb("access_token")
    refresh_bomb = _CountingBomb("refresh_rejected_token")
    once_bomb = _CountingBomb("_request_once")
    client.access_token = token_bomb
    client.refresh_rejected_token = refresh_bomb
    client._request_once = once_bomb
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: request rejected"):
        asyncio.run(client.request(method, "/api/v1/orders"))
    assert token_bomb.calls == 0
    assert refresh_bomb.calls == 0
    assert once_bomb.calls == 0


@pytest.mark.parametrize("method", INVALID_METHODS)
def test_auth_client_request_rejects_invalid_methods_before_authentication(method):
    """Non-string methods cannot be proven read-only and must not authenticate."""
    client = _client()
    token_bomb = _CountingBomb("access_token")
    once_bomb = _CountingBomb("_request_once")
    client.access_token = token_bomb
    client._request_once = once_bomb
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: request rejected"):
        asyncio.run(client.request(method, "/api/v1/orders"))
    assert token_bomb.calls == 0
    assert once_bomb.calls == 0


def test_auth_client_request_get_passes_through_all_fields():
    """GET keeps its full token/params/json_body/account_seq pass-through."""
    client = _client()
    seen: dict = {}

    async def token():
        return "cached-token"

    async def once(method, path, **kwargs):
        seen["method"] = method
        seen["path"] = path
        seen.update(kwargs)
        return {"result": "accounts-ok"}

    client.access_token = token
    client._request_once = once
    result = asyncio.run(
        client.request(
            "GET",
            "/api/v1/holdings",
            params={"symbol": "005930"},
            json_body=None,
            account_seq="ACC-1",
        )
    )
    assert result == {"result": "accounts-ok"}
    assert seen == {
        "method": "GET",
        "path": "/api/v1/holdings",
        "token": "cached-token",
        "params": {"symbol": "005930"},
        "json_body": None,
        "account_seq": "ACC-1",
    }


def test_auth_client_request_refreshes_rejected_token_once_for_get():
    """One 401 on a GET still triggers exactly one refresh then one retry."""
    client = _client()
    attempts: list[str] = []
    refreshed: list[str] = []

    async def token():
        return "old"

    async def refresh(rejected):
        refreshed.append(rejected)
        return "new"

    async def once(method, path, **kwargs):
        attempts.append(kwargs["token"])
        if kwargs["token"] == "old":
            raise TossUnauthorized("401")
        return {"result": "ok"}

    client.access_token = token
    client.refresh_rejected_token = refresh
    client._request_once = once
    assert asyncio.run(client.request("GET", "/api/v1/accounts")) == {"result": "ok"}
    assert attempts == ["old", "new"]
    assert refreshed == ["old"]


@pytest.mark.parametrize("method", MUTATING_METHODS)
def test_auth_client_request_once_rejects_mutating_methods_before_session(monkeypatch, method):
    """_request_once must reject mutations before any ClientSession exists."""
    client = _client()
    factory = _CountingBomb("aiohttp.ClientSession factory")
    monkeypatch.setattr("vibetrading.providers.toss.aiohttp.ClientSession", factory)
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: _request_once rejected"):
        asyncio.run(
            client._request_once(
                method,
                "/api/v1/orders",
                token="token-abc",
                params=None,
                json_body={"symbol": "005930"},
                account_seq="ACC-1",
            )
        )
    assert factory.calls == 0


@pytest.mark.parametrize("method", INVALID_METHODS)
def test_auth_client_request_once_rejects_invalid_methods_before_session(monkeypatch, method):
    """Invalid non-string methods are rejected before any ClientSession exists."""
    client = _client()
    factory = _CountingBomb("aiohttp.ClientSession factory")
    monkeypatch.setattr("vibetrading.providers.toss.aiohttp.ClientSession", factory)
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: _request_once rejected"):
        asyncio.run(
            client._request_once(
                method,
                "/api/v1/orders",
                token="token-abc",
                params=None,
                json_body={"symbol": "005930"},
                account_seq="ACC-1",
            )
        )
    assert factory.calls == 0


def test_auth_client_request_once_get_uses_transport_with_headers(monkeypatch):
    """GET proceeds on a real async context-manager transport with auth headers."""
    client = _client()
    session = _FakeSession(
        get_response=_FakeResponse(200, {"result": {"accounts": [{"accountSeq": "ACC-1"}]}})
    )
    _patch_session(monkeypatch, session)
    result = asyncio.run(
        client._request_once(
            "GET",
            "/api/v1/accounts",
            token="token-abc",
            params={"include": "balances"},
            json_body=None,
            account_seq="ACC-1",
        )
    )
    assert result == {"result": {"accounts": [{"accountSeq": "ACC-1"}]}}
    assert session.post_calls == []
    url, kwargs = session.get_calls[0]
    assert url == client.rest_url + "/api/v1/accounts"
    assert kwargs["params"] == {"include": "balances"}
    assert kwargs["json"] is None
    assert kwargs["headers"]["Authorization"] == "Bearer token-abc"
    assert kwargs["headers"]["X-Tossinvest-Account"] == "ACC-1"


def test_existing_get_behavior_is_preserved_end_to_end(monkeypatch, tmp_path):
    """request(GET) end-to-end keeps working on the synthetic transport."""
    client = _client_for(tmp_path)
    session = _FakeSession(
        get_response=_FakeResponse(200, {"result": {"items": [{"symbol": "005930", "quantity": 10}]}})
    )
    _patch_session(monkeypatch, session)

    async def token():
        return "cached-token"

    client.access_token = token
    holdings = asyncio.run(client.request("GET", "/api/v1/holdings", account_seq="ACC-1"))
    assert holdings["result"]["items"][0]["symbol"] == "005930"
    assert session.post_calls == []
    url, kwargs = session.get_calls[0]
    assert url == client.rest_url + "/api/v1/holdings"
    assert kwargs["headers"]["Authorization"] == "Bearer cached-token"
    assert kwargs["headers"]["X-Tossinvest-Account"] == "ACC-1"


def test_oauth_token_post_is_preserved_outside_generic_request(monkeypatch, tmp_path):
    """The OAuth client-credentials POST lives outside the DATA_ONLY gate."""
    client = _client_for(tmp_path)
    client.client_id = "synthetic-client"
    client.client_secret = "synthetic-secret"
    session = _FakeSession(
        post_response=_FakeResponse(200, {"access_token": "oauth-token", "expires_in": 3600})
    )
    _patch_session(monkeypatch, session)

    assert asyncio.run(client._access_token_locked()) == "oauth-token"
    url, kwargs = session.post_calls[0]
    assert url == client.rest_url + "/oauth2/token"
    assert kwargs["data"] == {
        "grant_type": "client_credentials",
        "client_id": "synthetic-client",
        "client_secret": "synthetic-secret",
    }
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert client._read_token_cache() == "oauth-token"
    # The generic request path still rejects mutations with zero extra calls.
    with pytest.raises(RuntimeError, match="TOSS DATA_ONLY: request rejected"):
        asyncio.run(client.request("POST", "/api/v1/orders"))
    assert len(session.post_calls) == 1


import asyncio
import pytest
from vibetrading.providers.toss import TossAuthClient
class Flip(str):
 def upper(self):
  self.calls=getattr(self,'calls',0)+1
  return 'GET' if self.calls==1 else 'POST'
def test_data_only_dispatch_cannot_reinterpret_a_validated_method(monkeypatch):
 methods=[]
 class Response:
  status=200
  async def __aenter__(self):return self
  async def __aexit__(self,*a):pass
  async def json(self,**kwargs):return {'result':'ok'}
 class Session:
  async def __aenter__(self):return self
  async def __aexit__(self,*a):pass
  def get(self,*a,**k):methods.append('GET');return Response()
  def post(self,*a,**k):methods.append('POST');return Response()
 monkeypatch.setattr('vibetrading.providers.toss.aiohttp.ClientSession',lambda **k:Session())
 client=object.__new__(TossAuthClient);client.rest_url='http://synthetic.invalid'
 asyncio.run(client._request_once(Flip('GET'),'/api/v1/orders',token='synthetic',params=None,json_body={},account_seq=None))
 assert methods==['GET']
