from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from vibetrading.settings import ProviderSettings, RuntimeSettings, get_settings


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class KisEndpoints:
    rest_base_url: str
    ws_url: str
    use_mock: bool


@dataclass(frozen=True)
class KisAuthResult:
    ok: bool
    kind: str
    status_code: int | None = None
    expires_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class KisAuthClient:
    """Minimal KIS auth client for read-only/live-data validation.

    This intentionally does not expose token values to callers. Callers only get
    the secret material in-process when it is strictly needed to connect a
    websocket session.
    """

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = self.settings.providers["kis"]
        self.endpoints = self._endpoints(self.provider)

    @staticmethod
    def _endpoints(provider: ProviderSettings) -> KisEndpoints:
        use_mock = _as_bool(provider.values.get("KIS_USE_MOCK"), default=True)
        if use_mock:
            return KisEndpoints(
                rest_base_url="https://openapivts.koreainvestment.com:29443",
                ws_url="ws://ops.koreainvestment.com:31000",
                use_mock=True,
            )
        return KisEndpoints(
            rest_base_url="https://openapi.koreainvestment.com:9443",
            ws_url="ws://ops.koreainvestment.com:21000",
            use_mock=False,
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.provider.values.get("KIS_APP_KEY") and self.provider.values.get("KIS_APP_SECRET"))

    def _base_headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "accept": "application/json",
            "charset": "UTF-8",
        }

    def _app_key(self) -> str:
        value = self.provider.values.get("KIS_APP_KEY")
        if not value:
            raise RuntimeError("KIS_APP_KEY is not configured")
        return value

    def _app_secret(self) -> str:
        value = self.provider.values.get("KIS_APP_SECRET")
        if not value:
            raise RuntimeError("KIS_APP_SECRET is not configured")
        return value

    async def issue_approval_key(self) -> str:
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key(),
            "secretkey": self._app_secret(),
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                f"{self.endpoints.rest_base_url}/oauth2/Approval",
                json=payload,
                headers=self._base_headers(),
            ) as response:
                data = await self._safe_json(response)
                if response.status != 200 or not data.get("approval_key"):
                    raise RuntimeError(self._format_error("approval", response.status, data))
                return str(data["approval_key"])

    async def issue_access_token(self) -> tuple[str, str | None]:
        cached = self._read_cached_access_token()
        if cached is not None:
            return cached
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key(),
            "appsecret": self._app_secret(),
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                f"{self.endpoints.rest_base_url}/oauth2/tokenP",
                json=payload,
                headers=self._base_headers(),
            ) as response:
                data = await self._safe_json(response)
                token = data.get("access_token")
                if response.status != 200 or not token:
                    raise RuntimeError(self._format_error("access_token", response.status, data))
                expires_at = data.get("access_token_token_expired")
                self._write_cached_access_token(str(token), str(expires_at or ""))
                return str(token), expires_at

    async def auth_check(self, *, include_access_token: bool = True) -> dict[str, Any]:
        if not self.has_credentials:
            return {
                "provider": "kis",
                "status": "SKIPPED_MISSING_CREDENTIALS",
                "mock": self.endpoints.use_mock,
                "rest_base_url": self.endpoints.rest_base_url,
                "ws_url": self.endpoints.ws_url,
            }
        result: dict[str, Any] = {
            "provider": "kis",
            "status": "ok",
            "mock": self.endpoints.use_mock,
            "rest_base_url": self.endpoints.rest_base_url,
            "ws_url": self.endpoints.ws_url,
            "approval_key": {"ok": False},
            "access_token": {"skipped": not include_access_token},
        }
        try:
            approval_key = await self.issue_approval_key()
            result["approval_key"] = {"ok": bool(approval_key)}
        except Exception as exc:
            result["approval_key"] = {"ok": False, "error": str(exc)}
            result["status"] = "failed"
        if include_access_token:
            try:
                token, expires_at = await self.issue_access_token()
                result["access_token"] = {"ok": bool(token), "expires_at": expires_at}
            except Exception as exc:
                result["access_token"] = {"ok": False, "error": str(exc)}
                result["status"] = "failed"
        return result

    async def get(
        self,
        path: str,
        *,
        tr_id: str,
        params: dict[str, str],
        token: str | None = None,
        tr_cont: str = "",
        ok_msg_codes: set[str] | None = None,
    ) -> dict[str, Any]:
        token = token or (await self.issue_access_token())[0]
        headers = self._base_headers()
        headers.update(
            {
                "authorization": f"Bearer {token}",
                "appkey": self._app_key(),
                "appsecret": self._app_secret(),
                "tr_id": tr_id,
                "custtype": "P",
                "tr_cont": tr_cont,
            }
        )
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(f"{self.endpoints.rest_base_url}{path}", params=params, headers=headers) as response:
                data = await self._safe_json(response)
                msg_code = str(data.get("msg_cd") or "")
                is_ok = str(data.get("rt_cd", "0")) == "0" or msg_code in (ok_msg_codes or set())
                if response.status != 200 or not is_ok:
                    raise RuntimeError(self._format_error(tr_id, response.status, data))
                return {"headers": dict(response.headers), "body": data}

    async def domestic_futureoption_balance(self) -> dict[str, Any]:
        cano = self.provider.values.get("KIS_ACCOUNT_NUMBER")
        acnt_prdt_cd = self.provider.values.get("KIS_ACCOUNT_PRODUCT_CODE")
        if not cano or not acnt_prdt_cd:
            raise RuntimeError("KIS account number/product code are not configured")
        token, _ = await self.issue_access_token()
        tr_id = "VTFO6118R" if self.endpoints.use_mock else "CTFO6118R"
        response = await self.get(
            "/uapi/domestic-futureoption/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "MGNA_DVSN": "01",
                "EXCC_STAT_CD": "1",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            token=token,
            ok_msg_codes={"KIOK0560"},
        )
        body = response["body"]
        return {
            "provider": "kis",
            "account_product_code": acnt_prdt_cd,
            "positions_count": len(body.get("output1") or []),
            "has_summary": bool(body.get("output2")),
            "rt_cd": body.get("rt_cd"),
            "msg_cd": body.get("msg_cd"),
        }

    async def inquire_investor_time_by_market(self, *, market_code: str, market_subcode: str) -> list[dict[str, Any]]:
        """Read KIS' market-level investor-flow snapshot.

        This endpoint is read-only and has no provider timestamp.  Callers must
        attach their own observation clock rather than pretending it is an
        exchange event time.
        """
        response = await self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
            tr_id="FHPTJ04030000",
            params={"FID_INPUT_ISCD": market_code, "FID_INPUT_ISCD_2": market_subcode},
        )
        output = response["body"].get("output") or []
        if not isinstance(output, list):
            raise RuntimeError("KIS investor-flow response has a non-list output")
        return [dict(row) for row in output if isinstance(row, dict)]

    async def domestic_stock_balance(self) -> dict[str, Any]:
        cano = self.provider.values.get("KIS_ACCOUNT_NUMBER")
        acnt_prdt_cd = self.provider.values.get("KIS_ACCOUNT_PRODUCT_CODE")
        if not cano or not acnt_prdt_cd:
            raise RuntimeError("KIS account number/product code are not configured")
        token, _ = await self.issue_access_token()
        tr_id = "VTTC8434R" if self.endpoints.use_mock else "TTTC8434R"
        response = await self.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "01",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            token=token,
            ok_msg_codes={"KIOK0560"},
        )
        body = response["body"]
        return {
            "provider": "kis",
            "account_product_code": acnt_prdt_cd,
            "account_type": "domestic_stock",
            "positions_count": len(body.get("output1") or []),
            "has_summary": bool(body.get("output2")),
            "rt_cd": body.get("rt_cd"),
            "msg_cd": body.get("msg_cd"),
        }

    async def account_state_summary(self) -> dict[str, Any]:
        product_code = self.provider.values.get("KIS_ACCOUNT_PRODUCT_CODE")
        if product_code == "03":
            result = await self.domestic_futureoption_balance()
            result["account_type"] = "domestic_futureoption"
            return result
        if product_code == "01":
            return await self.domestic_stock_balance()
        return {
            "provider": "kis",
            "account_product_code": product_code,
            "status": "SKIPPED_UNSUPPORTED_ACCOUNT_PRODUCT_CODE",
        }

    @staticmethod
    async def _safe_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
        payload = await response.read()
        for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                data = json.loads(payload.decode(encoding))
                return data if isinstance(data, dict) else {"payload": data}
            except Exception:
                continue
        try:
            text = payload.decode("utf-8", errors="replace")
            data = json.loads(text)
            return data if isinstance(data, dict) else {"payload": data}
        except Exception:
            return {"msg1": payload[:300].decode("utf-8", errors="replace")}

    @staticmethod
    def _format_error(kind: str, status_code: int, data: dict[str, Any]) -> str:
        code = data.get("msg_cd") or data.get("error_code") or data.get("rt_cd")
        message = data.get("msg1") or data.get("error_description") or data.get("error") or data.get("msg")
        if message is None:
            message = "KIS auth request failed"
        return f"KIS {kind} failed status={status_code} code={code or 'unknown'} message={message}"

    def _token_cache_path(self):
        return self.settings.state_dir / "kis_access_token.json"

    def _read_cached_access_token(self) -> tuple[str, str | None] | None:
        path = self._token_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("access_token")
            expires_at = data.get("expires_at")
            if not token or not expires_at:
                return None
            expiry = datetime.strptime(str(expires_at), "%Y-%m-%d %H:%M:%S")
            if expiry <= datetime.now() + timedelta(minutes=5):
                return None
            return str(token), str(expires_at)
        except Exception:
            return None

    def _write_cached_access_token(self, token: str, expires_at: str) -> None:
        if not token or not expires_at:
            return
        path = self._token_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"access_token": token, "expires_at": expires_at}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
