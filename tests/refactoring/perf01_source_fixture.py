"""Credential-free event-driven file strategy fixture for PERF-01 only.

The provider adapters, runtime pipeline, owner, ledger and execution adapters are
production classes. This file supplies synthetic policy and finite provider wire
messages; it never reads provider configuration or opens an external socket.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from decimal import Decimal
from math import isfinite
from types import MappingProxyType


class _ScriptedWorkingInventory:
    """Bounded per-venue synthetic native open-order inventory from scripted POST ACKs."""
    MAX_ORDERS = 512

    def __init__(self, transports, max_orders):
        if type(max_orders) is not int or not 1 <= max_orders <= self.MAX_ORDERS:
            raise ValueError("scripted inventory max_orders must be an integer in [1, 512]")
        from execution.perp_execution import HttpResponse
        self.max_orders=max_orders
        self._http_response=HttpResponse
        self._closed=False
        self._orders={"binance":{},"bybit":{}}
        self._methods=[]
        if (len(transports)!=2 or {venue for venue,_ in transports}!={"binance","bybit"}
                or any(getattr(transport,"venue",None)!=venue for venue,transport in transports)):
            raise ValueError("scripted inventory requires the exact Binance and Bybit transports")
        for venue,transport in transports:
            self._install(venue,transport)

    @staticmethod
    def _payload(json_body,data,params):
        if isinstance(json_body,dict):return dict(json_body)
        if isinstance(data,str):
            value=json.loads(data)
            return dict(value) if isinstance(value,dict) else {}
        return dict(params or {})

    @staticmethod
    def _identity(value,name):
        if not isinstance(value,(str,int)) or isinstance(value,bool) or not str(value) or len(str(value))>128:
            raise RuntimeError("scripted inventory mutation response has invalid " + name)
        return str(value)

    def _record(self,venue,payload,response):
        body=response.body
        if venue=="binance":
            if not isinstance(body,dict) or (type(body.get("code")) is int and body["code"]<0):return
            symbol=payload.get("symbol");client=payload.get("newClientOrderId")
            if body.get("clientOrderId")!=client or body.get("symbol")!=symbol:
                raise RuntimeError("scripted inventory Binance response identity differs from submitted NEW")
            native=self._identity(body.get("orderId"),"Binance native order ID")
            quantity=str(payload.get("quantity",""))
            cumulative=str(body.get("executedQty","0"))
            status=body.get("status")
            side=payload.get("side")
            if status!="NEW":
                raise RuntimeError("scripted inventory accepts only the venue's successful NEW status")
        else:
            if not isinstance(body,dict) or body.get("retCode")!=0:return
            result=body.get("result")
            if not isinstance(result,dict) or result.get("orderLinkId")!=payload.get("orderLinkId"):
                raise RuntimeError("scripted inventory Bybit response identity differs from submitted NEW")
            symbol=payload.get("symbol")
            if result.get("symbol")!=symbol:
                raise RuntimeError("scripted inventory Bybit response symbol differs from submitted NEW")
            client=payload.get("orderLinkId")
            native=self._identity(result.get("orderId"),"Bybit native order ID")
            quantity=str(payload.get("qty",""));cumulative="0";status="New"
            side=payload.get("side")
        if not isinstance(client,str) or not client or len(client)>128:
            raise RuntimeError("scripted inventory submitted NEW has no bounded client order ID")
        try:
            amount=Decimal(quantity);filled=Decimal(cumulative)
        except Exception as error:
            raise RuntimeError("scripted inventory quantity is not exact decimal text") from error
        if (response.status!=200 or not isinstance(symbol,str) or not symbol
                or not amount.is_finite() or amount<=0 or not filled.is_finite() or filled!=0):
            raise RuntimeError("scripted inventory supports only successful explicit no-fill NEW responses")
        record={"venue":venue,"symbol":symbol,"client_order_id":client,
            "native_order_id":native,"quantity":quantity,"cumulative_quantity":"0",
            "status":status,"side":side}
        rows=self._orders[venue]
        previous=rows.get(client)
        if previous is not None:
            if previous!=record:
                raise RuntimeError("scripted inventory duplicate client/native identity conflict")
            return
        if len(self._orders[venue])>=self.max_orders:
            raise RuntimeError("scripted inventory max_orders exceeded")
        if any(row["native_order_id"]==native for row in rows.values()):
            raise RuntimeError("scripted inventory duplicate native order ID conflict")
        rows[client]=record

    def _install(self,venue,transport):
        original_request,original_get=transport.request,transport._get
        self._methods.append((transport,original_request,original_get))

        async def request(method,url,*,params=None,json_body=None,data=None,headers=None,before_send=None):
            path=url.split("?",1)[0]
            creates=(method.upper()=="POST" and
                ((venue=="binance" and path.endswith("/fapi/v1/order")) or
                 (venue=="bybit" and path.endswith("/v5/order/create"))))
            payload=self._payload(json_body,data,params) if creates else {}
            client=payload.get("newClientOrderId") if venue=="binance" else payload.get("orderLinkId")
            if (creates and client not in self._orders[venue]
                    and len(self._orders[venue])>=self.max_orders):
                raise RuntimeError("scripted inventory max_orders exceeded before scripted POST")
            response=await original_request(method,url,params=params,json_body=json_body,data=data,
                headers=headers,before_send=before_send)
            if creates and response.status==200:self._record(venue,payload,response)
            return response

        def get(url,params):
            params=params or {}
            # Preserve existing ScriptedPerpTransport and reconcile-wrapper observations.
            reads=getattr(transport,"read_calls",None)
            before_reads=len(reads) if isinstance(reads,list) else None
            fallback=original_get(url,params)
            if before_reads is not None and len(reads)==before_reads:
                client=(params.get("origClientOrderId") or params.get("orderLinkId")
                    or params.get("client_order_id"))
                reads.append((url,client))
            if venue=="binance" and ("/fapi/v1/openOrders" in url or "/fapi/v1/order" in url):
                rows=list(self._orders[venue].values())
                if params.get("origClientOrderId"):
                    rows=[row for row in rows if row["client_order_id"]==params["origClientOrderId"]]
                if params.get("symbol"):
                    rows=[row for row in rows if row["symbol"]==params["symbol"]]
                native=[{"symbol":row["symbol"],"orderId":row["native_order_id"],
                    "clientOrderId":row["client_order_id"],"status":"NEW",
                    "origQty":row["quantity"],"executedQty":"0"} for row in rows]
                if "/fapi/v1/order" in url:
                    return self._http_response(200,native[0]) if native else fallback
                return self._http_response(200,native)
            if venue=="bybit" and "/v5/order/realtime" in url:
                rows=list(self._orders[venue].values())
                if params.get("orderLinkId"):
                    rows=[row for row in rows if row["client_order_id"]==params["orderLinkId"]]
                if params.get("symbol"):
                    rows=[row for row in rows if row["symbol"]==params["symbol"]]
                native=[{"symbol":row["symbol"],"orderId":row["native_order_id"],
                    "orderLinkId":row["client_order_id"],"orderStatus":"New",
                    "cumExecQty":"0","executedQty":"0","queuePosition":0,
                    "size":row["quantity"],"cumExecValue":"0","avgPrice":"100",
                    "side":row["side"],"lastPriceOnCreated":"100","createType":"CreateByUser",
                    "reduceOnly":True,"timeInForce":"GTC","orderType":"Limit"} for row in rows]
                return self._http_response(200,{"retCode":0,
                    "result":{"list":native,"nextPageCursor":""}})
            return fallback

        transport.request=request
        transport._get=get

    @property
    def counts(self):
        return MappingProxyType({venue:len(rows) for venue,rows in self._orders.items()})

    @property
    def report(self):
        return MappingProxyType({"max_orders":self.max_orders,"max_orders_scope":"per_venue","counts":self.counts,
            "orders":MappingProxyType({venue:tuple(MappingProxyType(dict(row)) for row in rows.values())
                for venue,rows in self._orders.items()}),
            "external_native_service_claim":False,"fills_synthesized":False})

    def close(self):
        if self._closed:return
        for transport,original_request,original_get in reversed(self._methods):
            transport._get=original_get
            transport.request=original_request
        self._methods.clear()
        self._closed=True


def install_scripted_working_inventory(fixture,max_orders=512):
    """Use successful scripted native POST replies as bounded open-order facts.

    Call after `prepare_scripted_reconcile`; close this handle before unwinding
    any earlier reconcile wrapper. No Owner or ledger data feeds this oracle.
    """
    transports=(("binance",fixture.get("binance_transport")),
                ("bybit",fixture.get("bybit_transport")))
    if any(transport is None for _,transport in transports):
        raise ValueError("scripted inventory requires both fixture transports")
    return _ScriptedWorkingInventory(transports,max_orders)

STRATEGY_ID = "perf01-event-policy"
CONFIG_VERSION = "perf01-config-v1"
ACTION_PROTOCOL_VERSION = 1
SUBSCRIPTIONS = [{"venue": "binance", "symbol": "BTCUSDT", "event_type": "trade"}]
WARMUP = {"bars": 0}
PLAN_FACTORY = None
EVALUATED = 0
FEEDBACK = 0


class FakeWireSocket:
    """Finite fake collector wire; recv returns actual provider JSON frames."""
    def __init__(self, rows, on_recv=None):
        self._rows = iter(rows)
        self.on_recv = on_recv

    async def recv(self):
        try:
            row = next(self._rows)
            if self.on_recv is not None:
                self.on_recv(row)
            return row
        except StopIteration as exc:
            raise WireDrained from exc


class WireDrained(Exception):
    pass


class EventSink:
    def __init__(self, publish):
        self.publish = publish
        self.count = 0

    async def publish_market_event(self, event):
        self.count += 1
        result = self.publish(event)
        if asyncio.iscoroutine(result):
            await result


class EventDrivenSource:
    STRATEGY_ID = STRATEGY_ID
    ACTION_PROTOCOL_VERSION = ACTION_PROTOCOL_VERSION

    def __init__(self):
        self.config_version = CONFIG_VERSION
        self.evaluated = 0
        self.feedback = 0

    def configure(self, config):
        self.config_version = config["config_version"]

    def validate_registration(self, facts):
        if not facts.get("accounts"):
            raise ValueError("synthetic account facts are required")

    def on_event(self, event, data):
        global EVALUATED
        self.evaluated += 1
        EVALUATED += 1
        if PLAN_FACTORY is None:
            return []
        # The policy receives only a successfully applied event and its immutable
        # decision view. The factory derives unique action IDs from event identity.
        return PLAN_FACTORY(event, data, self.config_version)

    def on_order_event(self, event, context):
        global FEEDBACK
        self.feedback += 1
        FEEDBACK += 1
        return []


def create_strategy():
    return EventDrivenSource()


def module_alias():
    """Expose this test fixture at its selected canonical strategy module name."""
    import sys
    module = sys.modules.get(__name__)
    if module is None:
        raise RuntimeError("fixture module is not registered")
    sys.modules["strategies.perf01_source_fixture"] = module
    module.ACTION_PROTOCOL_VERSION = ACTION_PROTOCOL_VERSION
    module.STRATEGY_ID = STRATEGY_ID
    return module


def binance_trade_wire_rows(count: int, *, start: int = 1, symbol: str = "BTCUSDT"):
    now_ms = int(time.time_ns() // 1_000_000)
    return [json.dumps({"data": {"e": "aggTrade", "s": symbol,
        "T": now_ms - count + 1 + i, "a": start + i, "p": "100.1", "q": "0.001",
        "m": bool(i & 1)}}, separators=(",", ":")) for i in range(count)]


async def parse_binance_messages(count: int, sink: EventSink, *, start: int = 1, on_recv=None, collector=None):
    """Use the real BinancePerpCollector websocket parser on fake wire rows."""
    from feeds.providers.binance import BinancePerpCollector
    before=sink.count
    socket = FakeWireSocket(binance_trade_wire_rows(count, start=start), on_recv=on_recv)
    collector = collector or BinancePerpCollector(symbols=("BTCUSDT",))
    try:
        await collector._binance_market(socket, sink, time.monotonic() + 60)
    except WireDrained:
        pass
    if sink.count - before < count:
        raise RuntimeError(f"Binance parser emitted {sink.count-before} events; expected {count}")


def build_action_plan(event, config_version, scopes, *, grouped: bool, sequence: int):
    """Return a bounded synthetic NEW plan tied to this exact applied event."""
    from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
    from execution.trading_contracts import (
        ActionKind, ExecutionAction, GroupSubmitMode, StrategyActionPlan, TradingMode,
    )
    scopes = tuple(scopes)
    count = 2 if grouped else 1
    if len(scopes) < count:
        raise ValueError("policy owner scopes are incomplete")
    venue_by_name = {"binance": Venue.BINANCE, "bybit": Venue.BYBIT}
    actions = []
    event_ts_ns = int(event.wallclock_ts_ns or event.receive_ts_ns or time.time_ns())
    event_tag = event.event_id[-80:]
    short_tag = hashlib.sha256(event.event_id.encode()).hexdigest()[:8]
    group_identity = f"g-p01-{sequence:06d}-{short_tag}"
    for leg, scope in enumerate(scopes[:count]):
        venue = venue_by_name[scope.venue]
        raw_symbol = "BTCUSDT" if scope.venue == "binance" else "BTCUSDT.P"
        instrument = InstrumentId(venue=venue, asset_class=AssetClass.CRYPTO_PERP,
            raw_symbol=raw_symbol, std_symbol=raw_symbol, currency="USDT", security_kind="perpetual")
        side = Side.BUY if (sequence + leg) % 2 == 0 else Side.SELL
        intent_id = f"p01-{sequence:06d}-{leg}-{short_tag}"
        intent = OrderIntent(intent_id=intent_id, job_id="perf01", signal_id="trace-" + event_tag,
            destination=OrderDestination.REAL, client_order_id="c-" + intent_id,
            instrument=instrument, side=side, quantity=Decimal("0.1"),
            limit_price=Decimal("100"), metadata={"account_id": scope.account,
                "environment": scope.environment, "strategy_id": STRATEGY_ID,
                "source_event_id": event.event_id})
        action = ExecutionAction(action_id="a-" + intent_id, action=ActionKind.NEW,
            scope=scope, strategy_id=STRATEGY_ID, config_version=config_version,
            mode=TradingMode.SANDBOX, expiry_ns=event_ts_ns + 5_000_000_000,
            intent=intent, time_in_force="GTC", group_id=(group_identity if grouped else None))
        actions.append(action)
    return StrategyActionPlan(plan_id="p-" + str(sequence) + "-" + event_tag,
        strategy_id=STRATEGY_ID, config_version=config_version,
        event_ts_ns=event_ts_ns, actions=tuple(actions),
        group_submit_mode=GroupSubmitMode.CONCURRENT if grouped else None,
        group_policy=({"base_asset": "BTC", "deadline_ns": event_ts_ns + 5_000_000_000,
            "max_followup_actions": 0, "max_legs": 2,
            "notional_caps": {s.key: "1000000" for s in scopes[:2]},
            "cost_caps": {s.key: "1000" for s in scopes[:2]}} if grouped else None))


class TraceRecorder:
    """Bounded benchmark rows with same-PID clock-domain validation."""
    REQUIRED = ("source_id", "product_id", "trace_id", "stage", "elapsed_ns",
                "status", "clock_domain", "pid", "source_sha256", "config_sha256", "host_sha256")

    def __init__(self, *, source_sha256: str, config_sha256: str, host_sha256: str,
                 max_rows: int = 100_000, observe_runtime: bool = False):
        if max_rows <= 0:
            raise ValueError("trace row budget must be positive")
        self.hashes = (source_sha256, config_sha256, host_sha256)
        self.max_rows = max_rows
        self.observe_runtime = bool(observe_runtime)
        self.phase_by_trace = {}
        self._lock = threading.Lock()
        self.rows = []
        self.omitted = 0

    def add(self, *, source_id, product_id, trace_id, stage, start_ns, end_ns,
            status="OK", clock_domain=None, pid=None, cursor=None, counts=None,
            account_scope_present=False, reason="", phase_id=None):
        import os
        pid = os.getpid() if pid is None else pid
        clock_domain = str(pid) if clock_domain is None else clock_domain
        if type(start_ns) is not int or type(end_ns) is not int or end_ns < start_ns:
            raise ValueError("invalid monotonic span")
        phase_id = str(phase_id or self.phase_by_trace.get(str(trace_id), "unassigned"))
        row={"source_id": str(source_id), "product_id": str(product_id),
            "trace_id": str(trace_id), "stage": str(stage), "elapsed_ns": end_ns-start_ns,"sample_count":1,
            "phase_id": phase_id,
            "start_ns": start_ns, "end_ns": end_ns, "status": str(status),
            "clock_domain": str(clock_domain), "pid": int(pid),
            "cursor": cursor, "counts": counts or {},
            "account_scope_present": bool(account_scope_present), "reason": str(reason)[:96],
            "source_sha256": self.hashes[0], "config_sha256": self.hashes[1],
            "host_sha256": self.hashes[2]}
        with self._lock:
            if len(self.rows) >= self.max_rows:
                self.omitted += 1
                return False
            self.rows.append(row)
        if self.observe_runtime:
            try:
                from operations.dashboard.latency import observe
                observe(str(stage), started_ns=start_ns, ended_ns=end_ns,
                    source_id=str(source_id), strategy_id="perf01-event-policy",
                    account_scope="", mode="sandbox", trace_id=str(trace_id),
                    queue_depth=(counts or {}).get("depth"),
                    oldest_queue_age_ns=(counts or {}).get("oldest_age_ns"),
                    error=str(status).upper() in {"FAILED", "ERROR", "BLOCKED"})
            except Exception:
                pass
        return True

    def join_duration(self, first, last):
        if first["pid"] != last["pid"] or first["clock_domain"] != last["clock_domain"]:
            raise ValueError("cross-process or cross-clock subtraction is forbidden")
        if first["trace_id"] != last["trace_id"]:
            raise ValueError("span trace identities differ")
        if last["end_ns"] < first["start_ns"]:
            raise ValueError("causal span is reversed")
        return last["end_ns"] - first["start_ns"]


def check_ordered_a1(rows):
    """Reject incomplete or time-reversed durable-to-transport action chains."""
    stages=("T0_fake_recv","T1_native_normalized","T_DQ","T_FS_append","T_FS_flush","T_DUR",
        "T_UDS_send","T_UDS_RECEIVED","T_UDS_APPLIED","T2_engine_apply",
        "T3_strategy_eval","T4_authorize_commit","T4_mark_handoff")
    grouped={}
    by_trace={}
    for row in rows:
        grouped.setdefault(row["trace_id"],set()).add(row["stage"])
        by_trace.setdefault(row["trace_id"],[]).append(row)
    errors=[]
    for trace_id,seen in grouped.items():
        trace_rows=by_trace[trace_id]
        attempts=[row for row in trace_rows if row["stage"]=="T5_transport_request"]
        if attempts:
            absent=[stage for stage in stages if stage not in seen]
            if absent: errors.append({"trace_id":trace_id,"missing":absent,"seen":sorted(seen)})
            def one(stage, *, intent_id=None):
                matches=[row for row in trace_rows if row["stage"]==stage and
                    (intent_id is None or row.get("counts",{}).get("intent_id")==intent_id)]
                return matches
            # Check the measured causal event path, including actual append,
            # flush, DUR, server apply and strategy callback boundaries.
            chain=("T_DQ","T_FS_append","T_FS_flush","T_DUR","T_UDS_send","T2_engine_apply","T3_strategy_eval")
            boundaries={stage:one(stage) for stage in chain}
            evaluated=boundaries["T3_strategy_eval"]
            if any(not boundaries[stage] for stage in chain):
                errors.append({"trace_id":trace_id,"ordering":"incomplete durable-to-strategy event path"})
            elif not (
                max(r["end_ns"] for r in boundaries["T_DQ"])<=min(r["start_ns"] for r in boundaries["T_FS_append"]) and
                max(r["end_ns"] for r in boundaries["T_FS_append"])<=min(r["start_ns"] for r in boundaries["T_FS_flush"]) and
                max(r["end_ns"] for r in boundaries["T_FS_flush"])<=max(r["end_ns"] for r in boundaries["T_DUR"]) and
                max(r["end_ns"] for r in boundaries["T_DUR"])<=min(r["start_ns"] for r in boundaries["T_UDS_send"]) and
                max(r["end_ns"] for r in boundaries["T_UDS_send"])<=min(r["start_ns"] for r in boundaries["T2_engine_apply"]) and
                max(r["end_ns"] for r in boundaries["T2_engine_apply"])<=min(r["start_ns"] for r in boundaries["T3_strategy_eval"])):
                errors.append({"trace_id":trace_id,"ordering":"T_DQ <= append <= flush <= DUR <= UDS send <= engine apply <= strategy"})
            if not evaluated or any(min(r["start_ns"] for r in evaluated)>r["start_ns"] for r in one("T4_authorize_commit")):
                errors.append({"trace_id":trace_id,"ordering":"strategy evaluation must precede Owner authorization"})
            attempt_ids=[row.get("counts",{}).get("intent_id") for row in attempts]
            if any(not identity for identity in attempt_ids) or len(set(attempt_ids))!=len(attempt_ids):
                errors.append({"trace_id":trace_id,"ordering":"each adapter attempt requires a unique synthetic intent identity"})
            for identity in attempt_ids:
                req=one("T5_transport_request",intent_id=identity)
                auth=one("T4_authorize_commit",intent_id=identity)
                handoff=one("T4_mark_handoff",intent_id=identity)
                before_send=one("T5_authorized_before_send",intent_id=identity)
                response=one("T6_scripted_response",intent_id=identity)
                if not all(len(items)==1 for items in (req,auth,handoff,before_send,response)):
                    errors.append({"trace_id":trace_id,"intent_id":identity,
                        "missing_or_duplicate":[name for name,items in (("authorize",auth),("request",req),
                            ("mark_handoff",handoff),("before_send",before_send),("response",response)) if len(items)!=1]})
                    continue
                request,authorized,marked,sent,replied=req[0],auth[0],handoff[0],before_send[0],response[0]
                if not (authorized["end_ns"]<=request["start_ns"]<=marked["start_ns"]<=
                        marked["end_ns"]<=sent["start_ns"]<=replied["end_ns"]):
                    errors.append({"trace_id":trace_id,"intent_id":identity,
                        "ordering":"authorize end <= request entry <= mark_handoff <= before_send <= response end"})
    if errors: raise RuntimeError(f"A1 path has incomplete or misordered trace evidence: {errors[:5]}")
    return {"traces":len(grouped),"a1_linkage_errors":0,
        "rule":"durability < strategy < authorize < request-entry < mark-handoff <= before_send < response; same PID clock"}


def causal_span_summaries(rows):
    """Join same-process T0→T3 and T0→T5 spans without crossing clock domains."""
    grouped={}
    for row in rows: grouped.setdefault(row["trace_id"],[]).append(row)
    spans=[]
    for trace_id,items in grouped.items():
        receives=[row for row in items if row["stage"]=="T0_fake_recv"]
        if not receives: continue
        for end_stage in ("T3_strategy_eval","T5_transport_request"):
            ends=[row for row in items if row["stage"]==end_stage]
            if not ends: continue
            first=min(receives,key=lambda row:row["start_ns"])
            last=max(ends,key=lambda row:row["end_ns"])
            if first["pid"]!=last["pid"] or first["clock_domain"]!=last["clock_domain"]:
                raise ValueError("cross-process or cross-clock subtraction is forbidden")
            if first["trace_id"]!=last["trace_id"] or last["end_ns"]<first["start_ns"]:
                raise ValueError("causal event span identity/order is invalid")
            spans.append({"trace_id":trace_id,"source_id":first["source_id"],
                "product_id":first["product_id"],"phase_id":first.get("phase_id","unassigned"),
                "stage":f"T0_to_{end_stage}","elapsed_ns":last["end_ns"]-first["start_ns"],
                "status":last["status"],"pid":first["pid"],"clock_domain":first["clock_domain"]})
    from collections import defaultdict
    buckets=defaultdict(list)
    for row in spans:
        buckets[(row["phase_id"],row["source_id"],row["product_id"],row["stage"],row["status"])].append(row["elapsed_ns"])
    summaries=[]
    for key,values in sorted(buckets.items()):
        summaries.append({"phase_id":key[0],"source_id":key[1],"product_id":key[2],
            "stage":key[3],"status":key[4],"n_completed":len(values),
            "p50_ns":percentile(values,.50),"p95_ns":percentile(values,.95),
            "p99_ns":percentile(values,.99),"p999_ns":percentile(values,.999)})
    return {"spans":spans,"summary":summaries,"clock_policy":"same PID/domain only"}


def percentile(values, q):
    """Nearest-rank percentile, small pure helper independently tested."""
    values = sorted(int(value) for value in values)
    if not values or not 0 < q <= 1:
        raise ValueError("percentile requires samples and q in (0, 1]")
    rank = max(1, int((len(values) * q) + 0.999999999))
    return values[rank - 1]


def summarize(rows):
    from collections import defaultdict
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row.get("phase_id","unassigned"),row["source_id"], row["product_id"], row["stage"], row["status"])].append(row["elapsed_ns"])
    result = []
    for key, values in sorted(buckets.items()):
        result.append({"phase_id":key[0],"source_id": key[1], "product_id": key[2], "stage": key[3],
            "status": key[4], "n_completed": len(values),
            "p50_ns": percentile(values, .50), "p95_ns": percentile(values, .95),
            "p99_ns": percentile(values, .99), "p999_ns": percentile(values, .999)})
    return result
