"""Fixed testnet endpoints. Mutations require explicit runner trading mode."""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from eth_account import Account
from eth_account.messages import encode_typed_data

from execution.extended_brokers import GRVT_ORDER_TYPES
from vibetrading.liquidity_mm.exchange import TestnetClient
from runtime.settings import get_settings

D = Decimal
ASSETS = ("BTC", "ETH", "SOL", "XRP")


class UncertainMutation(Exception):
    """The server may have processed the request; never release/resubmit."""


@dataclass
class Instrument:
    symbol: str
    tick: Decimal
    lot: Decimal
    minimum: Decimal
    min_notional: Decimal
    raw: dict

    def floor(self, quantity):
        return (quantity / self.lot).to_integral_value(rounding=ROUND_FLOOR) * self.lot

    def valid(self, quantity, price, reduce=False):
        return quantity > 0 and quantity >= self.minimum and (reduce or quantity * price >= self.min_notional)


class Bybit:
    name = "bybit"

    def __init__(self, session, trading=False):
        self.client = TestnetClient(session, trading=trading)
        self.trading = trading
        self.instruments, self.fees = {}, {}

    async def connect(self):
        await self.client.sync_clock()
        for asset in ASSETS:
            symbol = asset + "USDT"
            row = (await self.client.request("GET", "/v5/market/instruments-info", {"category":"linear", "symbol":symbol}, public=True))["result"]["list"][0]
            if row["status"] != "Trading" or row["contractType"] != "LinearPerpetual":
                raise RuntimeError("Bybit linear perpetual market unavailable")
            self.instruments[asset] = Instrument(symbol, D(row["priceFilter"]["tickSize"]), D(row["lotSizeFilter"]["qtyStep"]), D(row["lotSizeFilter"]["minOrderQty"]), D(row["lotSizeFilter"]["minNotionalValue"]), row)
            fee = (await self.client.request("GET", "/v5/account/fee-rate", {"category":"linear", "symbol":symbol}))["result"]["list"][0]
            self.fees[asset] = (D(fee["makerFeeRate"]), D(fee["takerFeeRate"]))
        permissions = (await self.client.request("GET", "/v5/user/query-api"))["result"]
        if self.trading and (int(permissions["readOnly"]) or "Order" not in permissions.get("permissions", {}).get("ContractTrade", [])):
            raise RuntimeError("Bybit API has no contract order permission")

    async def account(self):
        w = await self.client.wallet()
        return {"equity":w["totalEquity"], "available":w["totalAvailableBalance"], "margin":max(w["accountIMRate"],w["accountMMRate"])}

    async def positions(self):
        rows = await self.client.positions()
        if any(int(r.get("positionIdx",0)) != 0 and D(r.get("size") or 0) for r in rows):
            raise RuntimeError("Bybit one-way position mode required")
        return {r["symbol"].removesuffix("USDT"):D(r["size"])*(1 if r["side"]=="Buy" else -1) for r in rows if D(r.get("size") or 0)}

    async def open_orders(self):
        return await self.client.open_orders()

    async def create(self, asset, intent, client_id, ioc=False):
        i = self.instruments[asset]
        try:
            return await self.client.request("POST", "/v5/order/create", {"category":"linear", "symbol":i.symbol,"side":intent.side,"qty":format(intent.quantity,"f"),"price":format(intent.price,"f"),"orderType":"Limit","timeInForce":"IOC" if ioc else "PostOnly","reduceOnly":intent.reduce_only,"positionIdx":0,"orderLinkId":client_id})
        except RuntimeError as exc:
            if any(": "+code+" " in str(exc) for code in ("10000","10016")):
                raise UncertainMutation("Bybit mutation returned a server timeout/error") from None
            raise

    async def cancel(self, asset, client_id):
        return await self.client.request("POST", "/v5/order/cancel", {"category":"linear","symbol":self.instruments[asset].symbol,"orderLinkId":client_id})

    async def order(self, asset, client_id):
        args = {"category":"linear","symbol":self.instruments[asset].symbol,"orderLinkId":client_id}
        rows = await self.client.rows("/v5/order/realtime", args)
        if not rows:
            rows = await self.client.rows("/v5/order/history", args)
        if not rows:
            return None
        row = rows[0]
        return {"terminal": row["orderStatus"] in ("Filled","Cancelled","Rejected","Deactivated","PartiallyFilledCanceled"),"status":row["orderStatus"],"leaves":D(row.get("leavesQty") or 0),"raw":row}

    async def fills(self, start_ns, end_ns):
        return await self.client.rows("/v5/execution/list", {"category":"linear","startTime":start_ns//10**6,"endTime":end_ns//10**6,"limit":100})

    async def funding(self, start_ns, end_ns):
        return await self.client.rows("/v5/account/transaction-log", {"accountType":"UNIFIED","category":"linear","type":"SETTLEMENT","startTime":start_ns//10**6,"endTime":end_ns//10**6,"limit":50})


class Grvt:
    name = "grvt"
    REST = "https://trades.testnet.grvt.io/full/v1/"
    MARKET = "https://market-data.testnet.grvt.io/full/v1/"
    AUTH = "https://edge.testnet.grvt.io/auth/api_key/login"

    def __init__(self, session, trading=False):
        self.session, self.trading = session, trading
        env = get_settings().env
        self.api_key = env.get("GRVT_TESTNET_API_KEY")
        self.private_key = env.get("GRVT_TESTNET_PRIVATE_KEY") or env.get("GRVT_TESTNET_API_SECRET")
        self.subaccount = env.get("GRVT_TESTNET_SUB_ACCOUNT_ID") or env.get("GRVT_TESTNET_ACCOUNT_ID")
        self.address = env.get("GRVT_TESTNET_ADDRESS")
        self.headers, self.instruments, self.fees = {}, {}, {}
        self.auth_time = 0
        self.auth_lock = asyncio.Lock()

    async def login(self):
        async with self.auth_lock:
            if time.monotonic()-self.auth_time < 240:
                return
            if not self.api_key or not self.private_key or not self.subaccount:
                raise RuntimeError("GRVT testnet key, signer and subaccount required")
            signer = Account.from_key(self.private_key)
            if self.address and signer.address.lower() != self.address.lower():
                raise RuntimeError("GRVT signing address mismatch")
            async with self.session.post(self.AUTH,json={"api_key":self.api_key}) as r:
                if r.status != 200:
                    raise RuntimeError(f"GRVT testnet API login HTTP {r.status}")
                cookie, account = r.cookies.get("gravity"), r.headers.get("X-Grvt-Account-Id")
                data = await r.json()
                if not cookie or not account:
                    raise RuntimeError("GRVT session cookie missing")
                if data.get("sub_account_id") and str(data["sub_account_id"])!=str(self.subaccount):
                    raise RuntimeError("GRVT API key subaccount mismatch")
                self.headers = {"Cookie":"gravity="+cookie.value,"X-Grvt-Account-Id":account}
                self.auth_time = time.monotonic()

    async def rpc(self, endpoint, payload=None, mutation=False):
        if mutation and (not self.trading or endpoint not in ("create_order","cancel_order")):
            raise RuntimeError("GRVT mutation disabled")
        await self.login()
        async with self.session.post(self.REST+endpoint,json={"sub_account_id":str(self.subaccount),**(payload or {})},headers=self.headers) as r:
            data = await r.json()
            if r.status != 200 or "result" not in data:
                if mutation and (r.status>=500 or r.status==200):
                    raise UncertainMutation(f"GRVT {endpoint}: indeterminate server response HTTP {r.status}")
                # Do not log response payloads containing session/auth material.
                raise RuntimeError(f"GRVT {endpoint}: HTTP {r.status} code={data.get('code',data.get('error',{}).get('code') if isinstance(data.get('error'),dict) else None)}")
            return data

    async def connect(self):
        await self.login()
        for asset in ASSETS:
            native = asset+"_USDT_Perp"
            async with self.session.post(self.MARKET+"instrument",json={"instrument":native}) as r:
                r.raise_for_status()
                row = (await r.json())["result"]
            # min_size is the exchange contract quantum; base_decimals is only
            # the EIP-712 integer encoding scale, not an allowed order step.
            self.instruments[asset] = Instrument(native,D(row["tick_size"]),D(row["min_size"]),D(row["min_size"]),D(row.get("min_notional",0)),row)
            # Published maximum perpetual fees; no speculative rebate credit.
            self.fees[asset] = (D(0),D("0.00045"))

    async def account(self):
        w = (await self.rpc("account_summary"))["result"]
        equity = D(w["total_equity"])
        initial = D(w["initial_margin"])
        return {"equity":equity,"available":D(w.get("available_balance",equity-initial)),"margin":initial/equity if equity>0 else D(1)}

    async def positions(self):
        rows = (await self.rpc("positions"))["result"]
        return {r["instrument"].split("_")[0]:D(r["size"]) for r in rows if D(r["size"])}

    async def open_orders(self):
        return (await self.rpc("open_orders"))["result"]

    def signed_order(self, asset, intent, client_id, ioc=False, now_ns=None):
        i, now = self.instruments[asset], now_ns or time.time_ns()
        if not i.valid(intent.quantity,intent.price,intent.reduce_only):
            raise ValueError("GRVT order below exchange minimum")
        if i.floor(intent.quantity)!=intent.quantity or intent.price % i.tick:
            raise ValueError("GRVT size or price precision mismatch")
        size = int(intent.quantity * D(10)**int(i.raw["base_decimals"]))
        nonce, expiry = secrets.randbits(32), now+60*10**9
        message = {"subAccountID":int(self.subaccount),"isMarket":False,"timeInForce":3 if ioc else 1,"postOnly":not ioc,"reduceOnly":intent.reduce_only,"legs":[{"assetID":int(i.raw["instrument_hash"],16),"contractSize":size,"limitPrice":int(intent.price*10**9),"isBuyingContract":intent.side=="Buy"}],"nonce":nonce,"expiration":expiry}
        signer = Account.from_key(self.private_key)
        signature = signer.sign_message(encode_typed_data({"name":"GRVT Exchange","version":"0","chainId":326}, GRVT_ORDER_TYPES, message))
        return {"sub_account_id":str(self.subaccount),"is_market":False,"time_in_force":"IMMEDIATE_OR_CANCEL" if ioc else "GOOD_TILL_TIME","post_only":not ioc,"reduce_only":intent.reduce_only,"legs":[{"instrument":i.symbol,"size":format(intent.quantity,"f"),"limit_price":format(intent.price,"f"),"is_buying_asset":intent.side=="Buy"}],"signature":{"signer":signer.address,"r":"0x"+signature.r.to_bytes(32,"big").hex(),"s":"0x"+signature.s.to_bytes(32,"big").hex(),"v":signature.v,"nonce":nonce,"expiration":str(expiry),"chain_id":"326"},"metadata":{"client_order_id":client_id},"builder":"0x0000000000000000000000000000000000000000","builder_fee":"0"}

    async def create(self, asset, intent, client_id, ioc=False):
        return await self.rpc("create_order",{"order":self.signed_order(asset,intent,client_id,ioc)},mutation=True)

    async def cancel(self, asset, client_id):
        return await self.rpc("cancel_order",{"client_order_id":client_id,"time_to_live_ms":"5000"},mutation=True)

    async def order(self, asset, client_id):
        data = (await self.rpc("order",{"client_order_id":client_id}))["result"]
        if not data:
            return None
        state = data.get("state", {})
        status = state.get("status", "UNKNOWN")
        sizes = state.get("traded_size",[]) or []
        traded = D(sizes[0]) if sizes else D(0)
        return {"terminal":status in ("FILLED","REJECTED","CANCELLED","CANCELED","EXPIRED"),"status":status,"leaves":max(D(0),D(data["legs"][0]["size"])-traded),"raw":data}

    async def history(self, endpoint, start_ns, end_ns):
        rows, cursor = [], ""
        for _ in range(50):
            data = await self.rpc(endpoint,{"start_time":str(start_ns),"end_time":str(end_ns),"limit":500,"cursor":cursor})
            rows.extend(data["result"])
            nxt = data.get("next","")
            if not nxt:
                return rows
            if nxt==cursor:
                raise RuntimeError("GRVT history pagination failed")
            cursor=nxt
        raise RuntimeError("GRVT history page bound exceeded")

    async def fills(self, start_ns, end_ns):
        return await self.history("fill_history",start_ns,end_ns)

    async def funding(self, start_ns, end_ns):
        return await self.history("funding_payment_history",start_ns,end_ns)
