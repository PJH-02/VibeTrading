"""Fail-closed Bybit linear-perpetual order probe with a separate audit ledger."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import websockets

from .providers.crypto_perp import SYMBOLS
from .settings import RuntimeSettings, get_settings


def _market_trigger_price(mode: str, side: str, bid: Decimal, ask: Decimal) -> Decimal:
    if bid <= 0 or ask <= bid or mode not in {"maker", "taker"} or side not in {"Buy", "Sell"}:
        raise ValueError("invalid quote or order type")
    if mode == "maker":
        return bid if side == "Buy" else ask
    return ask if side == "Buy" else bid


@dataclass(frozen=True)
class BybitCredentials:
    api_key: str
    api_secret: str
    testnet: bool = True

    @classmethod
    def from_settings(cls, settings: RuntimeSettings, *, testnet: bool = True) -> "BybitCredentials":
        prefix = "BYBIT_TESTNET" if testnet else "BYBIT"
        if not testnet and settings.env.get("CRYPTO_LIVE_ENABLED", "").lower() != "true":
            raise RuntimeError("Bybit live trading is disabled by CRYPTO_LIVE_ENABLED")
        key = settings.env.get(prefix + "_API_KEY", "")
        secret = settings.env.get(prefix + "_API_SECRET", "")
        if not key or not secret:
            raise RuntimeError(f"{prefix} API credentials are missing")
        return cls(key, secret, testnet)


class BybitAudit:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                client_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
                mode TEXT NOT NULL, qty TEXT NOT NULL, price TEXT NOT NULL,
                status TEXT NOT NULL, decision_ns INTEGER NOT NULL,
                submit_ns INTEGER, ack_ns INTEGER, posted_ns INTEGER,
                first_fill_ns INTEGER, final_fill_ns INTEGER, order_id TEXT,
                response_json TEXT, last_error TEXT);
            CREATE TABLE IF NOT EXISTS fills (
                exec_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, order_id TEXT,
                qty TEXT NOT NULL, price TEXT NOT NULL, is_maker INTEGER NOT NULL,
                exchange_ms INTEGER, receive_ns INTEGER NOT NULL,
                receive_mono_ns INTEGER NOT NULL, raw_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT NOT NULL,
                status TEXT NOT NULL, wall_ns INTEGER NOT NULL, mono_ns INTEGER NOT NULL,
                raw_json TEXT);
            CREATE TABLE IF NOT EXISTS controls (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS market_triggers (
                client_id TEXT PRIMARY KEY, exchange_cts_ms INTEGER NOT NULL,
                exchange_system_ts_ms INTEGER NOT NULL, receive_wall_ns INTEGER NOT NULL,
                receive_mono_ns INTEGER NOT NULL, decision_ns INTEGER NOT NULL,
                clock_offset_ms INTEGER NOT NULL, clock_rtt_ms REAL NOT NULL,
                update_id INTEGER NOT NULL, best_bid TEXT NOT NULL, best_ask TEXT NOT NULL,
                order_headers_sent_ns INTEGER, raw_json TEXT NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def kill_active(self) -> bool:
        row = self.db.execute("SELECT value FROM controls WHERE name='kill_switch'").fetchone()
        return row is not None and row[0] == "on"

    def set_kill(self, active: bool) -> None:
        self.db.execute("INSERT INTO controls(name,value) VALUES('kill_switch',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", ("on" if active else "off",))

    def create(self, client_id: str, symbol: str, side: str, mode: str, qty: str, price: str, decision_ns: int) -> None:
        self.db.execute("INSERT INTO orders(client_id,symbol,side,mode,qty,price,status,decision_ns) VALUES(?,?,?,?,?,?,?,?)",
                        (client_id,symbol,side,mode,qty,price,"CREATED",decision_ns))

    def record_trigger(self, client_id: str, trigger: dict, decision_ns: int) -> None:
        self.db.execute(
            "INSERT INTO market_triggers(client_id,exchange_cts_ms,exchange_system_ts_ms,receive_wall_ns,receive_mono_ns,decision_ns,clock_offset_ms,clock_rtt_ms,update_id,best_bid,best_ask,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (client_id,trigger["cts_ms"],trigger["system_ts_ms"],trigger["receive_wall_ns"],
             trigger["receive_mono_ns"],decision_ns,trigger["clock_offset_ms"],
             trigger["clock_rtt_ms"],trigger["update_id"],str(trigger["bid"]),
             str(trigger["ask"]),trigger["raw_json"]),
        )

    def record_order_headers_sent(self, client_id: str, sent_ns: int) -> None:
        self.db.execute("UPDATE market_triggers SET order_headers_sent_ns=COALESCE(order_headers_sent_ns,?) WHERE client_id=?",
                        (sent_ns,client_id))

    def transition(self, client_id: str, status: str, *, field: str | None = None,
                   timestamp: int | None = None, order_id: str | None = None,
                   response: dict | None = None, error: str | None = None) -> None:
        wall = time.time_ns()
        mono = timestamp or time.perf_counter_ns()
        allowed = {"submit_ns", "ack_ns", "posted_ns", "first_fill_ns", "final_fill_ns"}
        if field is not None and field not in allowed:
            raise ValueError("invalid audit timestamp field")
        current=self.db.execute("SELECT status FROM orders WHERE client_id=?",(client_id,)).fetchone()
        if current is None:
            raise KeyError(client_id)
        if current[0] in {"FILLED","FILL_TYPE_MISMATCH","CANCELLED","REJECTED","DEACTIVATED"} and status in {"SUBMITTED","ACKNOWLEDGED","POSTED","ORDER_FILLED","PARTIAL_FILL"}:
            status=str(current[0])
        self.db.execute("INSERT INTO transitions(client_id,status,wall_ns,mono_ns,raw_json) VALUES(?,?,?,?,?)",
                        (client_id,status,wall,mono,json.dumps(response, separators=(",", ":")) if response else None))
        self.db.execute("UPDATE orders SET status=?, order_id=COALESCE(?,order_id), response_json=COALESCE(?,response_json), last_error=COALESCE(?,last_error) WHERE client_id=?",
                        (status, order_id, json.dumps(response) if response else None, error, client_id))
        if field:
            self.db.execute(f"UPDATE orders SET {field}=COALESCE({field},?) WHERE client_id=?", (mono,client_id))

    def fill(self, client_id: str, row: dict, received: int) -> bool:
        cursor = self.db.execute("INSERT OR IGNORE INTO fills(exec_id,client_id,order_id,qty,price,is_maker,exchange_ms,receive_ns,receive_mono_ns,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(row["execId"]),client_id,str(row.get("orderId") or ""),str(row["execQty"]),str(row["execPrice"]),
             int(bool(row["isMaker"])),int(row["execTime"]) if row.get("execTime") else None,
             time.time_ns(),received,json.dumps(row,separators=(",", ":"))))
        return cursor.rowcount > 0

    def report(self, client_id: str) -> dict:
        row = dict(self.db.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone())
        fills = [dict(x) for x in self.db.execute("SELECT qty,price,is_maker,exchange_ms,receive_ns,receive_mono_ns FROM fills WHERE client_id=? ORDER BY receive_mono_ns", (client_id,))]
        origin = row["decision_ns"]
        latencies = {name.replace("_ns", "_ms"): round((row[name]-origin)/1e6,3) if row[name] else None
                     for name in ("submit_ns","ack_ns","posted_ns","first_fill_ns","final_fill_ns")}
        trigger_row=self.db.execute("SELECT * FROM market_triggers WHERE client_id=?",(client_id,)).fetchone()
        breakdown=None
        if trigger_row is not None:
            t=dict(trigger_row)
            def local_ms(start: int | None, end: int | None) -> float | None:
                return round((end-start)/1e6,3) if start is not None and end is not None else None
            exchange_receive_ms=t["receive_wall_ns"]/1e6+t["clock_offset_ms"]
            first_exchange_ms=fills[0]["exchange_ms"] if fills else None
            breakdown={
                "matching_engine_to_system_ms":t["exchange_system_ts_ms"]-t["exchange_cts_ms"],
                "system_generated_to_app_receive_est_ms":round(exchange_receive_ms-t["exchange_system_ts_ms"],3),
                "app_receive_to_decision_ms":local_ms(t["receive_mono_ns"],t["decision_ns"]),
                "decision_to_submit_ms":local_ms(t["decision_ns"],row["submit_ns"]),
                "submit_to_http_headers_sent_ms":local_ms(row["submit_ns"],t["order_headers_sent_ns"]),
                "http_headers_sent_to_ack_ms":local_ms(t["order_headers_sent_ns"],row["ack_ns"]),
                "ack_to_book_posted_ms":local_ms(row["ack_ns"],row["posted_ns"]),
                "book_posted_to_first_fill_ms":local_ms(row["posted_ns"],row["first_fill_ns"]),
                "http_headers_sent_to_first_fill_ms":local_ms(t["order_headers_sent_ns"],row["first_fill_ns"]),
                "app_receive_to_first_fill_ms":local_ms(t["receive_mono_ns"],row["first_fill_ns"]),
                "exchange_book_to_exchange_execution_ms":first_exchange_ms-t["exchange_cts_ms"] if first_exchange_ms is not None else None,
                "exchange_execution_to_private_receive_est_ms":round(fills[0]["receive_ns"]/1e6+t["clock_offset_ms"]-first_exchange_ms,3) if first_exchange_ms is not None else None,
                "clock_offset_ms":t["clock_offset_ms"],"clock_rtt_ms":t["clock_rtt_ms"],
                "cross_clock_uncertainty_at_least_ms":round(t["clock_rtt_ms"]/2,3),
                "actual_exchange_network_send_ts":"unavailable",
                "trigger":{"cts_ms":t["exchange_cts_ms"],"system_ts_ms":t["exchange_system_ts_ms"],
                           "receive_wall_ns":t["receive_wall_ns"],"update_id":t["update_id"],
                           "best_bid":t["best_bid"],"best_ask":t["best_ask"]},
            }
        return {"client_id":client_id,"symbol":row["symbol"],"mode":row["mode"],"status":row["status"],
                "order_id":row["order_id"],"latency_from_decision":latencies,
                "stage_latency":breakdown,"fills":fills,"last_error":row["last_error"]}

    def summary(self, mode: str) -> dict:
        rows = self.db.execute("SELECT decision_ns,submit_ns,ack_ns,posted_ns,first_fill_ns,final_fill_ns,status FROM orders WHERE mode=?", (mode,)).fetchall()
        completed=[r for r in rows if r["status"]=="FILLED" and r["final_fill_ns"]]
        def percentiles(values: list[float]) -> dict:
            values=sorted(values)
            def at(p: float) -> float | None:
                if not values:return None
                index=(len(values)-1)*p
                lo=int(index); hi=min(lo+1,len(values)-1)
                return round(values[lo]+(values[hi]-values[lo])*(index-lo),3)
            return {"p50":at(.5),"p95":at(.95),"p99":at(.99),"samples":len(values)}
        stages={field.replace("_ns","_ms"):percentiles([(r[field]-r["decision_ns"])/1e6 for r in completed if r[field]])
                for field in ("submit_ns","ack_ns","posted_ns","first_fill_ns","final_fill_ns")}
        statuses={}
        for row in rows: statuses[row["status"]]=statuses.get(row["status"],0)+1
        return {"mode":mode,"filled":len(completed),"other":len(rows)-len(completed),
                "status_counts":statuses,"decision_to_stage":stages}


class BybitOrderClient:
    def __init__(self, credentials: BybitCredentials, session: aiohttp.ClientSession) -> None:
        self.credentials=credentials
        self.session=session
        self.base="https://api-testnet.bybit.com" if credentials.testnet else "https://api.bybit.com"
        self.server_clock_offset_ms=0
        self.clock_rtt_ms=0.0

    async def sync_clock(self, samples: int = 3) -> None:
        measurements=[]
        for _ in range(samples):
            before_wall=time.time_ns()
            before_mono=time.perf_counter_ns()
            result=await self.request("GET","/v5/market/time",public=True)
            after_mono=time.perf_counter_ns()
            after_wall=time.time_ns()
            server_ms=int(result["result"]["timeNano"])//1_000_000
            measurements.append(((after_mono-before_mono)/1e6,
                                 server_ms-(before_wall+after_wall)//2//1_000_000))
        self.clock_rtt_ms,self.server_clock_offset_ms=min(measurements,key=lambda item:item[0])
        if abs(self.server_clock_offset_ms)>60_000:
            raise RuntimeError("local clock differs from Bybit testnet by over 60 seconds")

    def now_ms(self) -> int:
        return int(time.time()*1000)+self.server_clock_offset_ms

    async def request(self, method: str, path: str, payload: dict | None = None, *, public: bool = False) -> dict:
        payload=payload or {}
        body=json.dumps(payload,separators=(",", ":")) if method=="POST" else ""
        query=urlencode(payload) if method=="GET" else ""
        url=self.base+path+("?"+query if query else "")
        headers={"Content-Type":"application/json"}
        if not public:
            timestamp=str(self.now_ms()); window="10000"
            signed=timestamp+self.credentials.api_key+window+(body if method=="POST" else query)
            headers.update({"X-BAPI-API-KEY":self.credentials.api_key,"X-BAPI-TIMESTAMP":timestamp,
                "X-BAPI-RECV-WINDOW":window,"X-BAPI-SIGN":hmac.new(self.credentials.api_secret.encode(),signed.encode(),hashlib.sha256).hexdigest()})
        async with self.session.request(method,url,data=body if method=="POST" else None,headers=headers) as response:
            response.raise_for_status()
            result=await response.json()
        if result.get("retCode") != 0:
            raise RuntimeError(f"Bybit {path}: {result.get('retCode')} {result.get('retMsg')}")
        return result


class BybitExecutionProbe:
    def __init__(self, settings: RuntimeSettings | None = None, *, testnet: bool = True) -> None:
        self.settings=settings or get_settings()
        self.credentials=BybitCredentials.from_settings(self.settings,testnet=testnet)
        subdir="bybit_testnet" if testnet else "bybit_live"
        self.audit=BybitAudit(self.settings.state_dir/subdir/"orders.sqlite3")
        self.testnet=testnet

    async def _market_trigger(self, ws: Any, symbol: str, client: BybitOrderClient) -> dict:
        await ws.send(json.dumps({"op":"subscribe","args":[f"orderbook.1.{symbol}"]}))
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            raw=await asyncio.wait_for(ws.recv(),timeout=max(0.1,deadline-time.monotonic()))
            received_mono=time.perf_counter_ns()
            received_wall=time.time_ns()
            msg=json.loads(raw)
            if msg.get("success") is False:
                raise RuntimeError("Bybit testnet public orderbook subscription rejected")
            if msg.get("topic") != f"orderbook.1.{symbol}":
                continue
            data=msg.get("data") or {}
            if not data.get("b") or not data.get("a") or msg.get("cts") is None or msg.get("ts") is None:
                continue
            bid=Decimal(str(data["b"][0][0])); ask=Decimal(str(data["a"][0][0]))
            cts=int(msg["cts"]); system_ts=int(msg["ts"])
            age_ms=received_wall/1e6+client.server_clock_offset_ms-system_ts
            if bid<=0 or ask<=bid or cts>system_ts or not -1000<=age_ms<=5000:
                continue
            return {"cts_ms":cts,"system_ts_ms":system_ts,
                    "receive_wall_ns":received_wall,"receive_mono_ns":received_mono,
                    "clock_offset_ms":client.server_clock_offset_ms,"clock_rtt_ms":client.clock_rtt_ms,
                    "update_id":int(data["u"]),"bid":bid,"ask":ask,"raw_json":raw}
        raise RuntimeError("no fresh Bybit testnet orderbook with cts/ts within 20 seconds")

    async def _listen(self, ws: Any, client_id: str, expected_maker: bool, done: asyncio.Event, posted: asyncio.Event) -> None:
        async for raw in ws:
            msg=json.loads(raw)
            if msg.get("op") == "auth" and not msg.get("success"):
                raise RuntimeError("Bybit private websocket authentication failed")
            for row in msg.get("data") or []:
                if row.get("orderLinkId") != client_id:
                    continue
                received=time.perf_counter_ns()
                if msg.get("topic") == "order.linear":
                    status=str(row.get("orderStatus") or "UNKNOWN")
                    if status == "New":
                        self.audit.transition(client_id,"POSTED",field="posted_ns",timestamp=received,response=row)
                        posted.set()
                    elif status in {"Cancelled","Rejected","Deactivated"}:
                        self.audit.transition(client_id,status.upper(),timestamp=received,response=row)
                        done.set()
                    elif status == "Filled":
                        self.audit.transition(client_id,"ORDER_FILLED",timestamp=received,response=row)
                elif msg.get("topic") == "execution.linear" and row.get("execType") == "Trade":
                    if not self.audit.fill(client_id,row,received):
                        continue
                    if bool(row.get("isMaker")) != expected_maker or (expected_maker and not posted.is_set()):
                        self.audit.transition(client_id,"FILL_TYPE_MISMATCH",timestamp=received,response=row,
                                              error="fill was not the requested maker/taker type or maker was never posted")
                        done.set()
                        continue
                    self.audit.transition(client_id,"PARTIAL_FILL",field="first_fill_ns",timestamp=received,response=row)
                    if row.get("leavesQty") not in (None,"") and Decimal(str(row["leavesQty"])) == 0:
                        self.audit.transition(client_id,"FILLED",field="final_fill_ns",timestamp=received,response=row)
                        done.set()

    async def _reconcile(self, client: BybitOrderClient, symbol: str) -> None:
        # Resolve uncertain ACK/cancel state before any new submission.
        unresolved=self.audit.db.execute("SELECT client_id,symbol FROM orders WHERE status NOT IN ('FILLED','CANCELLED','REJECTED','DEACTIVATED')").fetchall()
        for row in unresolved:
            cid=str(row[0])
            result=await client.request("GET","/v5/order/realtime",{"category":"linear","symbol":str(row[1]),"orderLinkId":cid})
            orders=result.get("result",{}).get("list",[])
            if not orders:
                history=await client.request("GET","/v5/order/history",{"category":"linear","symbol":str(row[1]),"orderLinkId":cid})
                orders=history.get("result",{}).get("list",[])
            if not orders:
                self.audit.transition(cid,"RECOVERY_UNKNOWN",error="order absent from realtime query; inspect history before new submission")
                raise RuntimeError("previous order absent from Bybit order queries; manual reconciliation required")
            vendor_status=str(orders[0].get("orderStatus") or "UNKNOWN")
            if vendor_status in {"Cancelled","Rejected","Deactivated"}:
                self.audit.transition(cid,vendor_status.upper(),response=orders[0])
                continue
            if vendor_status in {"New","PartiallyFilled"}:
                await client.request("POST","/v5/order/cancel",{"category":"linear","symbol":str(row[1]),"orderLinkId":cid})
                self.audit.transition(cid,"CANCEL_REQUESTED",response=orders[0])
                raise RuntimeError("previous open order cancellation submitted; rerun after exchange confirms terminal state")
            self.audit.transition(cid,"RECOVERED_"+vendor_status.upper(),response=orders[0])
            raise RuntimeError("previous order was filled or has unknown state without complete private execution audit")

    async def activate_kill_switch(self) -> dict:
        self.audit.set_kill(True)
        cancelled=[]
        failures=[]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            client=BybitOrderClient(self.credentials,session)
            await client.sync_clock()
            for symbol in SYMBOLS:
                try:
                    await client.request("POST","/v5/order/cancel-all",{"category":"linear","symbol":symbol})
                    cancelled.append(symbol)
                except Exception as exc:
                    failures.append({"symbol":symbol,"error":str(exc)})
        return {"kill_switch":"on","cancel_submitted":cancelled,"failures":failures}

    async def _run_market_trigger_order(self, client: BybitOrderClient, wire: dict,
                                        symbol: str, side: str, qty: Decimal, mode: str,
                                        tick: Decimal, timeout_sec: float,
                                        max_notional: Decimal, reduce_only: bool) -> dict:
        client_id="vt-"+uuid.uuid4().hex[:28]
        created=False
        expires=str(client.now_ms()+10000)
        signature=hmac.new(self.credentials.api_secret.encode(),
                           ("GET/realtime"+expires).encode(),hashlib.sha256).hexdigest()
        try:
            async with websockets.connect("wss://stream-testnet.bybit.com/v5/private",
                                          ping_interval=20,ping_timeout=20) as ws:
                await ws.send(json.dumps({"op":"auth","args":[self.credentials.api_key,expires,signature]}))
                auth=json.loads(await asyncio.wait_for(ws.recv(),timeout=10))
                if not auth.get("success"):
                    raise RuntimeError("Bybit testnet private websocket authentication failed")
                await ws.send(json.dumps({"op":"subscribe","args":["order.linear","execution.linear"]}))
                subscribed=json.loads(await asyncio.wait_for(ws.recv(),timeout=10))
                if not subscribed.get("success"):
                    raise RuntimeError("Bybit testnet private subscription failed")
                await client.sync_clock()
                async with websockets.connect("wss://stream-testnet.bybit.com/v5/public/linear",
                                              ping_interval=20,ping_timeout=20,max_queue=32) as public_ws:
                    trigger=await self._market_trigger(public_ws,symbol,client)
                    bid=trigger["bid"]; ask=trigger["ask"]
                    # A PostOnly buy at ask crosses the spread and is cancelled.
                    # The best resting buy price is bid; ask is an IOC taker.
                    raw_price=_market_trigger_price(mode,side,bid,ask)
                    rounding=ROUND_DOWN if side=="Buy" else ROUND_UP
                    price=(raw_price/tick).to_integral_value(rounding=rounding)*tick
                    if qty*price>max_notional:
                        raise RuntimeError("probe exceeds max_notional")
                    decision=time.perf_counter_ns()
                    self.audit.create(client_id,symbol,side,mode,str(qty),str(price),decision)
                    created=True
                    self.audit.record_trigger(client_id,trigger,decision)
                    done=asyncio.Event(); posted=asyncio.Event()
                    listener=asyncio.create_task(self._listen(ws,client_id,mode=="maker",done,posted))
                    payload={"category":"linear","symbol":symbol,"side":side,"orderType":"Limit",
                             "qty":str(qty),"price":str(price),"timeInForce":"PostOnly" if mode=="maker" else "IOC",
                             "orderLinkId":client_id,"reduceOnly":reduce_only}
                    wire.clear()
                    self.audit.transition(client_id,"SUBMITTED",field="submit_ns")
                    try:
                        try:
                            ack=await client.request("POST","/v5/order/create",payload)
                        finally:
                            if wire.get("headers_sent_ns"):
                                self.audit.record_order_headers_sent(client_id,wire["headers_sent_ns"])
                        self.audit.transition(client_id,"ACKNOWLEDGED",field="ack_ns",
                                              order_id=ack.get("result",{}).get("orderId"),response=ack)
                        done_task=asyncio.create_task(done.wait())
                        try:
                            finished,_=await asyncio.wait({listener,done_task},timeout=timeout_sec,
                                                          return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            done_task.cancel()
                        if listener in finished:
                            await listener
                            if not done.is_set():
                                raise RuntimeError("private execution stream closed before terminal order state")
                        if not done.is_set():
                            await client.request("POST","/v5/order/cancel",
                                                 {"category":"linear","symbol":symbol,"orderLinkId":client_id})
                            self.audit.transition(client_id,"TIMEOUT",
                                                  error="fill timeout; cancellation submitted, final state requires reconciliation")
                    finally:
                        listener.cancel()
                        await asyncio.gather(listener,return_exceptions=True)
        except Exception as exc:
            if created:
                self.audit.transition(client_id,"UNKNOWN",error=str(exc))
            raise
        return {"order":self.audit.report(client_id),"summary":self.audit.summary(mode)}

    async def run(self, *, symbol: str, side: str, qty: Decimal, mode: str,
                  timeout_sec: float = 60, max_notional: Decimal = Decimal("25"),
                  reduce_only: bool = False, market_trigger: bool = False) -> dict:
        if not self.testnet:
            raise RuntimeError("live probe execution is intentionally unavailable")
        if symbol not in SYMBOLS or side not in {"Buy","Sell"} or mode not in {"maker","taker"} or qty <= 0:
            raise ValueError("invalid testnet order request")
        if self.audit.kill_active():
            raise RuntimeError("Bybit kill switch is active")
        wire: dict[str,int]={}
        trace=aiohttp.TraceConfig()
        async def on_headers_sent(_session: Any, _context: Any, params: Any) -> None:
            if params.url.path == "/v5/order/create":
                wire["headers_sent_ns"]=time.perf_counter_ns()
        trace.on_request_headers_sent.append(on_headers_sent)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),trace_configs=[trace]) as session:
            client=BybitOrderClient(self.credentials,session)
            await client.sync_clock()
            await self._reconcile(client,symbol)
            positions=await client.request("GET","/v5/position/list",{"category":"linear","symbol":symbol})
            nonzero=[p for p in positions.get("result",{}).get("list",[])
                     if Decimal(str(p.get("size") or "0")) != 0]
            if reduce_only:
                if len(nonzero) != 1 or nonzero[0].get("side") == side or qty > Decimal(str(nonzero[0]["size"])):
                    raise RuntimeError("reduce-only probe must oppose and fit exactly one existing position")
            elif nonzero:
                raise RuntimeError("existing testnet position requires reconciliation before probe")
            open_orders=await client.request("GET","/v5/order/realtime",{"category":"linear","symbol":symbol,"openOnly":0,"limit":50})
            if any(row.get("orderStatus") in {"New","PartiallyFilled","Untriggered"}
                   for row in open_orders.get("result",{}).get("list",[])):
                raise RuntimeError("existing testnet order requires reconciliation before probe")
            info=await client.request("GET","/v5/market/instruments-info",{"category":"linear","symbol":symbol},public=True)
            instrument=info["result"]["list"][0]
            tick=Decimal(str(instrument["priceFilter"]["tickSize"]))
            qty_step=Decimal(str(instrument["lotSizeFilter"]["qtyStep"]))
            minimum=Decimal(str(instrument["lotSizeFilter"]["minOrderQty"]))
            if qty < minimum or qty != (qty/qty_step).to_integral_value(rounding=ROUND_DOWN)*qty_step:
                raise ValueError("quantity violates instrument lot size")
            if market_trigger:
                return await self._run_market_trigger_order(client,wire,symbol,side,qty,mode,tick,
                                                            timeout_sec,max_notional,reduce_only)
            ob=await client.request("GET","/v5/market/orderbook",{"category":"linear","symbol":symbol,"limit":1},public=True)
            bid=Decimal(str(ob["result"]["b"][0][0])); ask=Decimal(str(ob["result"]["a"][0][0]))
            if mode == "maker":
                raw_price=(bid if side=="Buy" else ask)
            else:
                raw_price=(ask+2*tick if side=="Buy" else bid-2*tick)
            rounding=ROUND_DOWN if side=="Buy" else ROUND_UP
            price=(raw_price/tick).to_integral_value(rounding=rounding)*tick
            if qty*price > max_notional:
                raise RuntimeError("probe exceeds max_notional")
            client_id="vt-"+uuid.uuid4().hex[:28]
            decision=time.perf_counter_ns()
            self.audit.create(client_id,symbol,side,mode,str(qty),str(price),decision)
            ws_url="wss://stream-testnet.bybit.com/v5/private"
            expires=str(client.now_ms()+10000)
            signature=hmac.new(self.credentials.api_secret.encode(),("GET/realtime"+expires).encode(),hashlib.sha256).hexdigest()
            try:
                async with websockets.connect(ws_url,ping_interval=20,ping_timeout=20) as ws:
                    await ws.send(json.dumps({"op":"auth","args":[self.credentials.api_key,expires,signature]}))
                    auth=json.loads(await asyncio.wait_for(ws.recv(),timeout=10))
                    if not auth.get("success"):
                        raise RuntimeError("Bybit testnet private websocket authentication failed")
                    await ws.send(json.dumps({"op":"subscribe","args":["order.linear","execution.linear"]}))
                    subscribed=json.loads(await asyncio.wait_for(ws.recv(),timeout=10))
                    if not subscribed.get("success"):
                        raise RuntimeError("Bybit testnet private subscription failed")
                    done=asyncio.Event(); posted=asyncio.Event()
                    listener=asyncio.create_task(self._listen(ws,client_id,mode=="maker",done,posted))
                    payload={"category":"linear","symbol":symbol,"side":side,"orderType":"Limit",
                             "qty":str(qty),"price":str(price),"timeInForce":"PostOnly" if mode=="maker" else "IOC",
                             "orderLinkId":client_id,"reduceOnly":reduce_only}
                    self.audit.transition(client_id,"SUBMITTED",field="submit_ns")
                    try:
                        ack=await client.request("POST","/v5/order/create",payload)
                        self.audit.transition(client_id,"ACKNOWLEDGED",field="ack_ns",
                                              order_id=ack.get("result",{}).get("orderId"),response=ack)
                        try:
                            done_task=asyncio.create_task(done.wait())
                            try:
                                finished,_=await asyncio.wait({listener,done_task},timeout=timeout_sec,
                                                              return_when=asyncio.FIRST_COMPLETED)
                            finally:
                                done_task.cancel()
                            if listener in finished:
                                await listener
                                if not done.is_set():
                                    raise RuntimeError("private execution stream closed before terminal order state")
                            if not done.is_set():
                                raise asyncio.TimeoutError
                        except asyncio.TimeoutError:
                            await client.request("POST","/v5/order/cancel",{"category":"linear","symbol":symbol,"orderLinkId":client_id})
                            self.audit.transition(client_id,"TIMEOUT",error="fill timeout; cancellation submitted, final state requires reconciliation")
                    finally:
                        listener.cancel()
                        await asyncio.gather(listener,return_exceptions=True)
            except Exception as exc:
                self.audit.transition(client_id,"UNKNOWN",error=str(exc))
                raise
            return {"order":self.audit.report(client_id),"summary":self.audit.summary(mode)}
