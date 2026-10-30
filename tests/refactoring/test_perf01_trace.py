"""Pure PERF-01 trace invariants; no runtime or benchmark invocation."""
import pytest

from perf01_source_fixture import (TraceRecorder, causal_span_summaries, check_ordered_a1,
    install_scripted_working_inventory, percentile, summarize)


def _recorder(max_rows=10):
    return TraceRecorder(source_sha256="a" * 64, config_sha256="b" * 64,
                         host_sha256="c" * 64, max_rows=max_rows)


def test_trace_rows_are_bounded_and_omissions_are_counted():
    trace = _recorder(max_rows=1)
    assert trace.add(source_id="a", product_id="p", trace_id="t1", stage="accepted",
                     start_ns=10, end_ns=12)
    assert not trace.add(source_id="a", product_id="p", trace_id="t2", stage="accepted",
                         start_ns=20, end_ns=25)
    assert len(trace.rows) == 1 and trace.omitted == 1
    row = trace.rows[0]
    assert row["account_scope_present"] is False
    assert {row["source_sha256"], row["config_sha256"], row["host_sha256"]} == {
        "a" * 64, "b" * 64, "c" * 64}


def test_trace_never_subtracts_across_process_or_clock_domains():
    trace = _recorder()
    first = {"pid": 1, "clock_domain": "pid1", "trace_id": "x", "start_ns": 10, "end_ns": 20}
    second = {"pid": 2, "clock_domain": "pid2", "trace_id": "x", "start_ns": 25, "end_ns": 30}
    with pytest.raises(ValueError, match="cross-process"):
        trace.join_duration(first, second)
    same_pid_other_clock = dict(second, pid=1, clock_domain="foreign")
    with pytest.raises(ValueError, match="cross-process"):
        trace.join_duration(first, same_pid_other_clock)
    with pytest.raises(ValueError, match="trace identities"):
        trace.join_duration(first, dict(first, trace_id="y"))


def test_percentiles_are_nearest_rank_and_summary_keeps_source_product_cohorts():
    assert [percentile([9, 1, 5, 2], q) for q in (.5, .95, .99, .999)] == [2, 9, 9, 9]
    rows = []
    for source, product, duration in (("a", "ETF", 10), ("a", "ETF", 30), ("b", "BTC", 100)):
        rows.append({"source_id": source, "product_id": product, "stage": "T5",
                     "status": "OK", "elapsed_ns": duration})
    groups = summarize(rows)
    assert {(row["source_id"], row["product_id"], row["n_completed"]) for row in groups} == {
        ("a", "ETF", 2), ("b", "BTC", 1)}


def test_percentile_rejects_empty_or_invalid_request():
    with pytest.raises(ValueError):
        percentile([], .99)
    with pytest.raises(ValueError):
        percentile([1], 0)


def test_runtime_latency_observer_receives_benchmark_spans():
    trace = TraceRecorder(source_sha256="a" * 64, config_sha256="b" * 64,
        host_sha256="c" * 64, observe_runtime=True)
    start = __import__("time").monotonic_ns()
    assert trace.add(source_id="binance-perp", product_id="binance:crypto_perp:BTCUSDT",
        trace_id="evt-1", stage="T_PERF_test", start_ns=start, end_ns=start + 1)
    from operations.dashboard.latency import snapshot
    rows = snapshot()["metrics"]
    assert any(row["stage"] == "T_PERF_test" and row["source_id"] == "binance-perp" for row in rows)


def test_a1_structural_check_rejects_t5_without_every_durable_stage():
    required=("T0_fake_recv","T1_native_normalized","T_DQ","T_FS_append","T_FS_flush","T_DUR",
        "T_UDS_send","T_UDS_RECEIVED","T_UDS_APPLIED","T2_engine_apply",
        "T3_strategy_eval","T4_authorize_commit","T4_mark_handoff")
    rows=[{"trace_id":"e1","stage":stage,"start_ns":i*10,"end_ns":i*10+1}
        for i,stage in enumerate(required)]
    intent={"intent_id":"synthetic-1"}
    rows.extend([
        {"trace_id":"e1","stage":"T4_authorize_commit","start_ns":120,"end_ns":121,"counts":intent},
        {"trace_id":"e1","stage":"T5_transport_request","start_ns":122,"end_ns":122,"counts":intent},
        {"trace_id":"e1","stage":"T4_mark_handoff","start_ns":123,"end_ns":124,"counts":intent},
        {"trace_id":"e1","stage":"T5_authorized_before_send","start_ns":125,"end_ns":125,"counts":intent},
        {"trace_id":"e1","stage":"T6_scripted_response","start_ns":122,"end_ns":130,"counts":intent},
    ])
    assert check_ordered_a1(rows)["a1_linkage_errors"] == 0
    damaged=[row for row in rows if row["stage"]!="T_DUR"]
    with pytest.raises(RuntimeError,match="T_DUR"):
        check_ordered_a1(damaged)


def test_a1_rejects_mark_handoff_after_authorized_before_send():
    required=("T0_fake_recv","T1_native_normalized","T_DQ","T_FS_append","T_FS_flush","T_DUR",
        "T_UDS_send","T_UDS_RECEIVED","T_UDS_APPLIED","T2_engine_apply",
        "T3_strategy_eval","T4_authorize_commit","T4_mark_handoff")
    rows=[{"trace_id":"e1","stage":stage,"start_ns":i*10,"end_ns":i*10+1}
        for i,stage in enumerate(required)]
    intent={"intent_id":"synthetic-1"}
    rows.extend([
        {"trace_id":"e1","stage":"T4_authorize_commit","start_ns":120,"end_ns":121,"counts":intent},
        {"trace_id":"e1","stage":"T5_transport_request","start_ns":122,"end_ns":122,"counts":intent},
        {"trace_id":"e1","stage":"T4_mark_handoff","start_ns":130,"end_ns":131,"counts":intent},
        {"trace_id":"e1","stage":"T5_authorized_before_send","start_ns":125,"end_ns":125,"counts":intent},
        {"trace_id":"e1","stage":"T6_scripted_response","start_ns":122,"end_ns":132,"counts":intent},
    ])
    with pytest.raises(RuntimeError,match="mark_handoff"):
        check_ordered_a1(rows)


def test_a1_structural_check_keeps_no_action_cohort_without_fabricated_t5():
    assert check_ordered_a1([{"trace_id":"no-action","stage":"T3_strategy_eval"}])["a1_linkage_errors"] == 0


def test_phase_summary_keeps_baseline_and_injected_ack_cohorts_separate():
    trace=_recorder()
    trace.phase_by_trace["baseline-event"]="baseline_ack0"
    trace.phase_by_trace["variable-event"]="variable_ack"
    for event, duration in (("baseline-event",10),("variable-event",100)):
        trace.add(source_id="binance-perp",product_id="BTCUSDT",trace_id=event,
            stage="T_UDS_APPLIED",start_ns=1,end_ns=duration+1)
    summary=summarize(trace.rows)
    assert {(row["phase_id"],row["n_completed"]) for row in summary} == {
        ("baseline_ack0",1),("variable_ack",1)}


def test_causal_spans_include_no_signal_evaluations_and_reject_cross_clock_join():
    rows=[]
    common={"source_id":"binance-perp","product_id":"BTCUSDT","phase_id":"baseline_ack0",
        "pid":123,"clock_domain":"123","status":"OK"}
    rows.extend([{**common,"trace_id":"signal","stage":"T0_fake_recv","start_ns":100,"end_ns":100},
        {**common,"trace_id":"signal","stage":"T3_strategy_eval","start_ns":150,"end_ns":170},
        {**common,"trace_id":"signal","stage":"T5_transport_request","start_ns":180,"end_ns":200},
        {**common,"trace_id":"quiet","stage":"T0_fake_recv","start_ns":300,"end_ns":300},
        {**common,"trace_id":"quiet","stage":"T3_strategy_eval","start_ns":330,"end_ns":350}])
    report=causal_span_summaries(rows)
    assert {row["stage"] for row in report["spans"]}=={"T0_to_T3_strategy_eval","T0_to_T5_transport_request"}
    rows[-1]["pid"]=456;rows[-1]["clock_domain"]="456"
    with pytest.raises(ValueError,match="cross-process"):
        causal_span_summaries(rows)


@pytest.mark.asyncio
async def test_scripted_inventory_uses_real_two_venue_adapter_parsers_and_restores(tmp_path):
    from cap05_source_fixture import NOW, build_native_group
    from execution.execution_groups import CrossVenueExecutionGroups
    from test_cap06_routing import prepare_scripted_reconcile

    fixture=build_native_group(tmp_path)
    prepare_scripted_reconcile(fixture,"binance-perp")
    prepare_scripted_reconcile(fixture,"bybit-perp")
    transports=(fixture["binance_transport"],fixture["bybit_transport"])
    original=((transport.request,transport._get) for transport in transports)
    original=tuple(original)
    inventory=install_scripted_working_inventory(fixture,max_orders=4)
    coordinator=CrossVenueExecutionGroups(fixture["binance_owner"].ledger,
        {fixture["binance_scope"].key:fixture["binance_owner"],
         fixture["bybit_scope"].key:fixture["bybit_owner"]},
        max_groups=8,max_facts=128,max_bytes=200_000,max_followup_actions=2,
        clock=lambda:NOW)
    try:
        result=await coordinator.execute(fixture["plan"],registry=fixture["registry"],
            readiness=fixture["readiness"],evidence=fixture["evidence"])
        assert result["state"]=="ACKNOWLEDGED"
        report=inventory.report
        assert dict(report["counts"])=={"binance":1,"bybit":1}
        for venue,route,symbol in (("binance","binance-perp","BTCUSDT"),
                                   ("bybit","bybit-perp","BTCUSDT.P")):
            adapter=fixture["registry"].binding(route).adapter
            expected=report["orders"][venue][0]
            rows=await adapter.working_orders(symbol)
            assert len(rows)==1
            row=rows[0]
            assert row["symbol"]==expected["symbol"]==symbol
            assert row["order_id"]==expected["native_order_id"]
            assert row["client_order_id"]==expected["client_order_id"]
            assert row["cumulative_quantity"]==expected["cumulative_quantity"]=="0"
            status=await adapter.order_status(symbol,expected["client_order_id"])
            assert status["order_id"]==expected["native_order_id"]
            assert status["client_order_id"]==expected["client_order_id"]
            assert status["cumulative_quantity"]=="0"
            account=await adapter.account_state()
            assert account["equity"]=="1000000"
            fills=await adapter.fills(symbol,start_ns=NOW-1,end_ns=NOW+1)
            funding=await adapter.funding(symbol,start_ns=NOW-1,end_ns=NOW+1)
            assert fills["rows"]==[] and fills["complete"]
            assert funding["rows"]==[] and funding["complete"]
            assert ("/fapi/v1/openOrders" in str(transports[0].read_calls)
                    if venue=="binance" else "/v5/order/realtime" in str(transports[1].read_calls))
        with pytest.raises(TypeError):report["counts"]["binance"]=100
        with pytest.raises(TypeError):report["orders"]["binance"][0]["symbol"]="foreign"
    finally:
        inventory.close()
        def same_method(left,right):
            if hasattr(left,"__self__") or hasattr(right,"__self__"):
                return (getattr(left,"__self__",None) is getattr(right,"__self__",None)
                    and getattr(left,"__func__",None) is getattr(right,"__func__",None))
            return left is right
        assert same_method(transports[0].request,original[0][0])
        assert same_method(transports[0]._get,original[0][1])
        assert same_method(transports[1].request,original[1][0])
        assert same_method(transports[1]._get,original[1][1])
        await fixture["binance_owner"].close()
        await fixture["bybit_owner"].close()


@pytest.mark.asyncio
async def test_scripted_inventory_bounds_duplicate_identity_and_transport_isolation(tmp_path):
    from cap05_source_fixture import build_native_group
    from test_cap06_routing import prepare_scripted_reconcile

    first=build_native_group(tmp_path/"first")
    second=build_native_group(tmp_path/"second")
    third=build_native_group(tmp_path/"third")
    for fixture in (first,second,third):
        prepare_scripted_reconcile(fixture,"binance-perp")
        prepare_scripted_reconcile(fixture,"bybit-perp")
    handles=[]
    try:
        for value in (0,-1,513,True,1.5):
            with pytest.raises(ValueError,match="max_orders"):
                install_scripted_working_inventory(first,max_orders=value)
        one=install_scripted_working_inventory(first,max_orders=1)
        empty=install_scripted_working_inventory(second,max_orders=2)
        conflict=install_scripted_working_inventory(third,max_orders=2)
        handles.extend((one,empty,conflict))
        transport=first["binance_transport"]
        payload={"symbol":"BTCUSDT","side":"BUY","quantity":"0.1",
            "newClientOrderId":"fixture-order-one"}
        permits=[]
        async def send(client):
            return await transport.request("POST","/fapi/v1/order",json_body={
                **payload,"newClientOrderId":client},before_send=lambda:permits.append(client))
        response=await send("fixture-order-one")
        assert response.status==200 and permits==["fixture-order-one"]
        assert dict(one.counts)=={"binance":1,"bybit":0}
        assert dict(empty.counts)=={"binance":0,"bybit":0}
        with pytest.raises(RuntimeError,match="max_orders"):
            await send("fixture-order-two")
        assert len(transport.calls)==1 and permits==["fixture-order-one"]

        # One venue exhausting its finite bound does not consume the other's
        # independently declared inventory capacity.
        bybit=first["bybit_transport"]
        response=await bybit.request("POST","/v5/order/create",json_body={
            "symbol":"BTCUSDT.P","side":"Buy","qty":"0.1","orderLinkId":"bybit-one"})
        assert response.body["result"]["orderLinkId"]=="bybit-one"
        assert dict(one.counts)=={"binance":1,"bybit":1}

        # With room available, reusing one client ID for a different scripted
        # native ID is a consistency error, never a second inventory identity.
        conflict_transport=third["binance_transport"]
        async def conflict_send():
            return await conflict_transport.request("POST","/fapi/v1/order",json_body=payload)
        await conflict_send()
        with pytest.raises(RuntimeError,match="duplicate client/native identity conflict"):
            await conflict_send()
        assert dict(conflict.counts)=={"binance":1,"bybit":0}
    finally:
        for handle in reversed(handles):handle.close()
        await first["binance_owner"].close();await first["bybit_owner"].close()
        await second["binance_owner"].close();await second["bybit_owner"].close()
        await third["binance_owner"].close();await third["bybit_owner"].close()
