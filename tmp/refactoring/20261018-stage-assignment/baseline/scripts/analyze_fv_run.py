"""Exploratory, dependence-aware diagnostics on the untouched evaluation period."""
from __future__ import annotations
import argparse
import bisect
import csv
import json
import math
from pathlib import Path
import sqlite3
from datetime import datetime,timezone,timedelta
from decimal import Decimal, ROUND_FLOOR

import numpy as np

KST=timezone(timedelta(hours=9))


def iso(ns):
    return datetime.fromtimestamp(ns/1e9,KST).isoformat(timespec="seconds")


def interval(values):
    a=np.asarray(values,dtype=float); a=a[np.isfinite(a)]
    return np.quantile(a,[.025,.975]).tolist() if len(a) else None


def diagnostics(rows,horizon,coefficient):
    rows=sorted(rows,key=lambda r:r["t"])
    if not rows:return {"n":0,"reason":"no_valid_feed_samples"}
    times=np.array([r["t"] for r in rows]); pairs=[]
    for n,r in enumerate(rows):
        if r["reason"] not in ("ok","unfitted_coefficient","convergence_coefficient_out_of_range_or_unfitted"):
            continue
        k=int(np.searchsorted(times,times[n]+horizon))
        if k>=len(rows) or times[k]-(times[n]+horizon)>2.5:continue
        x=float(r["x"]); y=float(rows[k]["mid"]-r["mid"])
        pairs.append((r["t"],x,y,float(r["mid"])))
    if not pairs:return {"n":0,"reason":"no_valid_feature_future_pairs"}
    a=np.array(pairs); t,x,y,mid=a.T
    # Signed feature is tested even when no deployable FV coefficient exists.
    xx=float(x@x); slope=float(x@y/xx) if xx>1e-18 else None
    direction=np.sign(x); effect=direction*y/mid*10000
    prediction=x*coefficient if coefficient is not None else None
    nonzero=(x!=0)&(y!=0)
    nonoverlap=[]; next_t=-math.inf
    for r in a:
        if r[0]>=next_t:nonoverlap.append(r); next_t=r[0]+horizon
    block_ids=np.floor((t-t[0])/60).astype(int)
    blocks=[np.where(block_ids==k)[0] for k in sorted(set(block_ids))]
    rng=np.random.default_rng(20261004+horizon)
    effects,slopes,mse_gains=[],[],[]
    for _ in range(1000):
        ix=np.concatenate([blocks[j] for j in rng.integers(0,len(blocks),len(blocks))])
        effects.append(float(effect[ix].mean()))
        den=float(x[ix]@x[ix])
        slopes.append(float(x[ix]@y[ix]/den) if den>1e-18 else math.nan)
        if prediction is not None:
            mse_gains.append(float(np.mean(y[ix]**2-(y[ix]-prediction[ix])**2)))
    p=None
    if slope is not None and len(blocks)>=10:
        # Null bootstrap centered at the observed statistic; time blocks retain
        # within-block dependence. Short-run inference remains exploratory.
        bootstrap=np.array(slopes); bootstrap=bootstrap[np.isfinite(bootstrap)]
        p=float((1+np.count_nonzero(np.abs(bootstrap-slope)>=abs(slope)))/(len(bootstrap)+1))
    result={"n":len(a),"nonoverlapping_pairs":len(nonoverlap),"time_blocks_60s":len(blocks),"nonzero_price_moves":int(np.count_nonzero(y)),"feature_slope_zero_intercept":slope,"slope_ci95_block_bootstrap":interval(slopes),"p_block_bootstrap_centered":p,"signed_feature_move_mean_bps":float(effect.mean()),"signed_move_ci95_block_bootstrap_bps":interval(effects),"sign_accuracy_excluding_zero_moves":float(np.mean(np.sign(x[nonzero])==np.sign(y[nonzero]))) if np.any(nonzero) else None,"method":"1000 resamples of 60s time blocks; 10/30/60s prespecified horizons; no IID event p-values","interpretation":"exploratory testnet pilot; overlapping labels and short history; association is not causation"}
    if prediction is not None:
        result.update(frozen_coefficient=coefficient,forecast_rmse=float(np.sqrt(np.mean((y-prediction)**2))),no_change_rmse=float(np.sqrt(np.mean(y**2))),mse_improvement_vs_mid=float(np.mean(y**2-(y-prediction)**2)),mse_improvement_ci95=interval(mse_gains),forecast_signed_move_mean_bps=float(np.mean(np.sign(prediction)*y/mid*10000)))
    else:result["fv_forecast_evaluation"]="no deployable frozen coefficient; feature-only diagnostic"
    result["inference_sufficient_time_blocks"]=len(blocks)>=10
    if len(blocks)<10:
        result["inference_limitation"]="Fewer than 10 time blocks: resampling intervals are descriptive only; insufficient history for hypothesis inference"
    if horizon!=30:result["coefficient_note"]="Coefficient calibrated at H=30; other horizons are response diagnostics"
    return result


def trade_metrics(fills,orders,samples,funding):
    by_id={r["client_id"]:r for r in orders}
    rows=sorted(fills,key=lambda r:r["source_ns"])
    inventory,cash,fees=0.,0.,0.
    episode_cash=0.; episode_start=None; episodes=[]; equity=[]
    trades=[]
    for f in rows:
        qty,price,fee=map(float,(f["qty"],f["price"],f["fee"]))
        signed=qty if f["side"]=="Buy" else -qty
        if abs(inventory)<1e-9:episode_start=f["source_ns"]; episode_cash=0.
        inventory+=signed; cash-=signed*price; fees+=fee; episode_cash-=signed*price+fee
        trades.append((f["receive"],signed,price,fee))
        if abs(inventory)<1e-8:
            inventory=0.
            episodes.append({"net_pnl_usdt_ex_funding":episode_cash,"holding_seconds":(f["source_ns"]-episode_start)/1e9,"forced":by_id[f["client_id"]]["role"]=="FORCED_REDUCE"})
    cash_state,pos,paid,j=0.,0.,0.,0
    for r in sorted(samples,key=lambda r:r["t"]):
        while j<len(trades) and trades[j][0]<=r["t"]:
            _,signed,price,fee=trades[j]; cash_state-=signed*price; pos+=signed; paid+=fee;j+=1
        equity.append(cash_state+pos*r["mid"]-paid)
    last_mid=samples[-1]["mid"] if samples else None
    mark=inventory*last_mid if inventory and last_mid is not None else 0.
    pnl=cash-fees+mark+funding if funding is not None else None
    equity.append(cash-fees+mark)
    peak=0.; dd=0.
    for value in equity:peak=max(peak,value);dd=max(dd,peak-value)
    maker_notional=sum(float(f["qty"])*float(f["price"]) for f in rows if f["maker"])
    done=[r for r in orders if r["status"] not in ("SHADOW","PENDING","UNKNOWN")]
    live=[r for r in orders if r["live"] is not None]
    delays=[r["live"]-r["receive"] for r in live]
    return {"submitted_orders":sum(r["send"] is not None for r in orders),"fills":len(rows),"filled_order_count":len({f["client_id"] for f in rows}),"maker_fills":sum(bool(f["maker"]) for f in rows),"maker_notional_usdt":maker_notional,"total_notional_usdt":sum(float(f["qty"])*float(f["price"]) for f in rows),"fees_usdt":fees,"funding_usdt":funding,"gross_trading_pnl_usdt":cash+mark,"net_pnl_usdt":pnl,"end_signed_inventory":inventory,"full_round_trips":len(episodes),"normal_close_rate":sum(not e["forced"] for e in episodes)/len(episodes) if episodes else None,"round_trip_win_rate":sum(e["net_pnl_usdt_ex_funding"]>0 for e in episodes)/len(episodes) if episodes else None,"episodes":episodes,"observed_max_drawdown_usdt":dd,"max_drawdown_note":"Own fill cashflows + sampled target mid marks; funding timing excluded from path, included in net PnL","receive_to_order_live_p95_seconds":float(np.quantile(delays,.95)) if delays else None,"latency_note":"First private OPEN/New receipt or REST active-state observation is a live-time proxy; origin is retained in events; REST polling adds observation delay; exchange network-send timestamp unavailable","calmar":None,"annualized_sharpe":None,"var_es":None,"annualization_note":"30 minutes is insufficient for reliable daily Sharpe, annual return, Calmar, VaR/ES; zero fills cannot validate execution profitability","net_per_submitted_order":pnl/sum(r["send"] is not None for r in orders) if pnl is not None and any(r["send"] is not None for r in orders) else None}


def funding_for(venue,asset,history):
    rows=history.get("funding")
    if not isinstance(rows,list):return None
    if venue=="bybit":
        # Transaction log (unlike execution fee): positive funding is received.
        return sum(float(r.get("funding") or 0) for r in rows if r.get("symbol")==asset+"USDT")
    # GRVT amount is positive when paid, negative when received.
    amounts=[]
    for r in rows:
        if r.get("instrument")==asset+"_USDT_Perp":
            key=next((k for k in ("funding_payment","payment","amount") if k in r),None)
            if key is None:return None
            amounts.append(float(r[key]))
    return -sum(amounts)


def adjust_bh(results):
    items=[(value["p_block_bootstrap_centered"],value) for metric in results.values() for value in metric.values() if value.get("p_block_bootstrap_centered") is not None]
    items.sort(key=lambda r:r[0]); adjusted=1.
    for i in range(len(items)-1,-1,-1):
        p,record=items[i];adjusted=min(adjusted,p*len(items)/(i+1));record["q_bh_exploratory"]=adjusted


def snapshot_size(row, instrument, config, start_equity):
    """Reconstruct the size budget, not the live account/working-order state."""
    d=lambda value:Decimal(str(value))
    raw=min(d(config["unit_equity_fraction"])*d(start_equity)/d(row["mid"]),
            d(config["unit_notional_cap_usdt"])/d(row["mid"]),
            d(config["depth_participation"])*d(row["depth"]))
    lot=d(instrument["lot"])
    quantity=(raw/lot).to_integral_value(rounding=ROUND_FLOOR)*lot
    return float(quantity)


def analyze(directory,output):
    manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    complete=json.loads((directory/"complete.json").read_text(encoding="utf-8"))
    end=json.loads((directory/"account_end.json").read_text(encoding="utf-8"))
    readiness_path=directory.parent/"fv_connection_check"/"readiness.json"
    readiness=json.loads(readiness_path.read_text(encoding="utf-8")) if readiness_path.exists() else {}
    db=sqlite3.connect(directory/"run.sqlite");db.row_factory=sqlite3.Row
    performance,tests={},{}
    for venue in end:
        for sid,(_,asset) in manifest["routes"].items():
            key=venue+":"+sid;model=manifest["models"][key]
            samples=[dict(r) for r in db.execute("SELECT * FROM samples WHERE venue=? AND strategy=? AND phase='evaluation' ORDER BY t",(venue,sid))]
            accounting_samples=[dict(r) for r in db.execute("SELECT * FROM samples WHERE venue=? AND strategy=? ORDER BY t",(venue,sid))]
            orders=[dict(r) for r in db.execute("SELECT * FROM orders WHERE venue=? AND strategy=?",(venue,sid))]
            fills=[dict(r) for r in db.execute("SELECT f.* FROM fills f JOIN orders o ON o.client_id=f.client_id WHERE f.venue=? AND o.strategy=? ORDER BY f.source_ns",(venue,sid))]
            funding=funding_for(venue,asset,end[venue])
            performance[key]={"asset":asset,"model":model,"observed_samples_1s":len(samples),"valid_fv_samples":sum(r["fv"] is not None for r in samples),**trade_metrics(fills,orders,accounting_samples,funding)}
            coefficient=model.get("coefficient") if model.get("valid") else None
            fee_source=manifest.get("fee_budget",{}).get(venue,{}).get(asset) or readiness.get(venue,{}).get("instruments",{}).get(asset,{}).get("fee_budget")
            fees=tuple(map(float,fee_source)) if fee_source else None
            instrument=manifest["instruments"][venue][asset]
            tick=float(instrument["tick"])
            valid_rows=[r for r in samples if r["fv"] is not None]
            ratios=[abs(r["fv"]-r["mid"])/(r["mid"]*sum(fees)+tick+float(model.get("error_allowance") or 0)) for r in valid_rows] if fees else []
            performance[key]["snapshot_economic_gate"]={"fee_budget":fees,"valid_1s_snapshots":len(valid_rows),"snapshots_edge_above_cost":sum(value>1 for value in ratios) if fees else None,"maximum_edge_to_cost_ratio":max(ratios) if ratios else None,"note":"1s diagnostic snapshots, not exhaustive event opportunities or executions; fee budget from recorded preflight or manifest"}
            edge_rows=[row for row,ratio in zip(valid_rows,ratios) if ratio>1]
            sizes=[snapshot_size(row,instrument,manifest["config"],manifest["equity_start"][venue]) for row in edge_rows]
            performance[key]["snapshot_size_budget"]={"edge_above_cost_snapshots":len(edge_rows),"zero_quantity_after_lot_rounding":sum(size==0 for size in sizes),"maximum_rounded_quantity":max(sizes) if sizes else None,"note":"Diagnostic reconstruction using recorded starting equity; live account headroom, private readiness and decision events are not reconstructed"}
            performance[key]["fresh_book_snapshot_fraction"]=min(1,len(samples)/complete["evaluation_elapsed_seconds"])
            tests[key]={str(h):diagnostics(samples,h,coefficient) for h in (10,30,60)}
            latency=performance[key]["receive_to_order_live_p95_seconds"]
            performance[key]["latency_sample_count"]=sum(r["live"] is not None for r in orders)
            if latency is not None:
                times=[r["t"] for r in samples];remaining=[]
                for r in samples:
                    if r["fv"] is None:continue
                    i,j=bisect.bisect_left(times,r["t"]+latency),bisect.bisect_left(times,r["t"]+30)
                    if i<len(times) and j<len(times) and times[i]-r["t"]-latency<=2.5 and times[j]-r["t"]-30<=2.5:
                        remaining.append(np.sign(r["fv"]-r["mid"])*(samples[j]["mid"]-samples[i]["mid"])/r["mid"]*10000)
                tests[key]["remaining_move"]={"measured_p95_seconds":latency,"latency_observations":performance[key]["latency_sample_count"],"n":len(remaining),"signed_mean_bps":float(np.mean(remaining)) if remaining else None,"note":"Descriptive sample quantile; few live orders cannot establish a stable p95 latency distribution"}
            else:tests[key]["remaining_move"]={"reason":"No live orders; latency-adjusted remaining move cannot be measured"}
    adjust_bh(tests)
    output.mkdir(parents=True,exist_ok=True)
    result={"manifest":manifest,"completion":complete,"performance":performance,"hypotheses":tests,"feed_gaps":db.execute("SELECT count(*) FROM events WHERE kind='feed_gap'").fetchone()[0],"private_gaps":db.execute("SELECT count(*) FROM events WHERE kind='private_gap'").fetchone()[0],"account_end":end}
    result["requested_duration_met"]=complete["evaluation_elapsed_seconds"]>=manifest["duration"]
    (output/"results.json").write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    with (output/"performance.csv").open("w",newline="",encoding="utf-8-sig") as f:
        fields=["venue_strategy","asset","submitted_orders","fills","full_round_trips","fees_usdt","funding_usdt","net_pnl_usdt","observed_max_drawdown_usdt","end_signed_inventory"]
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for k,r in performance.items():writer.writerow({"venue_strategy":k,**{x:r.get(x) for x in fields[1:]}})
    title="v4 독립 FV 전략 테스트넷 30분 평가" if result["requested_duration_met"] else "v4 테스트넷 실행 점검 · 조기 종료 기록"
    lines=["# "+title, "", f"기간: {iso(complete['evaluation_start_ns'])} ~ {iso(complete['evaluation_end_ns'])} (KST). 실제 평가 {complete['evaluation_elapsed_seconds']:.2f}초. 요청 기간 충족: {'예' if result['requested_duration_met'] else '아니오 — 최종 30분 평가와 구분'}.","", "S1=BTC, S2=ETH, S3=SOL, S4=XRP. 두 거래소에서 같은 시간에 운용했다. Binance USDT 무기한 선물 BBO를 외부 기준가격으로 사용했다.","", "계수는 별도 사전 수집 구간에서 학습했고, 예측구간을 purge한 이후 구간에서 오차 여유를 보정했다. 평가에서는 계수·basis·오차 여유를 동결했다. 미체결 호가 터치를 수익으로 계산하지 않았다.","", "| 거래소·전략 | 종목 | 제출 | 체결 | 완전 왕복 | 수수료 USDT | funding USDT | 순손익 USDT | 관측 MDD USDT |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    fmt=lambda v:"미측정" if v is None else f"{v:.6f}"
    for k,r in performance.items():lines.append(f"| {k} | {r['asset']} | {r['submitted_orders']} | {r['fills']} | {r['full_round_trips']} | {fmt(r['fees_usdt'])} | {fmt(r['funding_usdt'])} | {fmt(r['net_pnl_usdt'])} | {fmt(r['observed_max_drawdown_usdt'])} |")
    lines += ["", "## 보정 결과와 운용 제한", "", "| 거래소·전략 | 고정 계수 | 신규 FV 가능 | 보정 결과 |", "|---|---:|---|---|"]
    for k,r in performance.items():
        m=r["model"];lines.append(f"| {k} | {fmt(m.get('coefficient'))} | {'예' if m.get('valid') else '아니오'} | {m['reason']} |")
    lines += ["", "| 거래소·전략 | 신선한 호가 표본 비중 | 유효 FV 표본 | edge > C 표본 | 최대 edge / C |", "|---|---:|---:|---:|---:|"]
    for k,r in performance.items():
        gate=r["snapshot_economic_gate"]
        lines.append(f"| {k} | {r['fresh_book_snapshot_fraction']:.1%} | {gate['valid_1s_snapshots']} | {gate['snapshots_edge_above_cost']} | {fmt(gate['maximum_edge_to_cost_ratio'])} |")
    lines += ["", "유효한 FV도 maker 진입·taker 감축 수수료 예산, 1 tick, 사전 구간의 방향별 과대예측 오차 90분위수를 합한 비용 기준을 넘어야 신규 주문을 제출한다. 이 보수적인 초기 실행 정책에서 무주문은 수익성 입증이 아니다. 수수료 예산은 Bybit 계정 조회값, GRVT는 공개 최대 perpetual taker 0.045%와 maker 0%를 사용했다. 실제 체결 수수료·리베이트는 별도 체결 로그의 값으로 계산한다.","", "## 같은 기간의 가설 진단", "", "H=30초가 사전 지정한 주 검증 구간이며 10·60초는 보조 진단이다. 기울기는 가격 단위 신호 x와 이후 mid 이동 y의 절편 없는 관계다. 60초 시간 블록 1,000회 bootstrap을 사용하고, 여러 비교의 p값은 BH로 보정했다. 신호가 0이거나 미래 가격을 확보하지 못한 행은 별도로 구분한다.","", "| 거래소·전략 | H | 유효 쌍 | 비중첩 쌍 | 기울기 | 95% CI | BH q | FV RMSE / mid 유지 RMSE |", "|---|---:|---:|---:|---:|---|---:|---|"]
    for k,metrics in tests.items():
        for h in ("10","30","60"):
            r=metrics[h];ci=r.get("slope_ci95_block_bootstrap");ci_text="미측정" if ci is None else f"[{ci[0]:.4g}, {ci[1]:.4g}]"
            if r.get("inference_sufficient_time_blocks") is False:ci_text=f"표본 부족 ({r['time_blocks_60s']} 블록)"
            rmse=f"{r['forecast_rmse']:.6g} / {r['no_change_rmse']:.6g}" if "forecast_rmse" in r else "배포 가능한 FV 없음"
            lines.append(f"| {k} | {h} | {r.get('n',0)} | {r.get('nonoverlapping_pairs',0)} | {fmt(r.get('feature_slope_zero_intercept'))} | {ci_text} | {fmt(r.get('q_bh_exploratory'))} | {rmse} |")
    lines += ["", "30분 테스트넷 자료로 수익성·인과관계·지속 가능한 알파를 확정하지 않는다. 서로 다른 종목을 배정했으므로 이 손익을 전략 순위로 해석할 수 없다. 가격이 움직이지 않은 시장의 beta=0, CI=[0,0]은 정보가 없다는 결과이며 강한 검증으로 취급하지 않는다. 10개 미만의 시간 블록에서는 추론을 보류한다. 연환산 Sharpe·Calmar·VaR/ES와 체결 없는 정상 청산률은 미측정으로 남긴다.","",f"공개 feed 재연결/공백: {result['feed_gaps']}건, private feed 공백: {result['private_gaps']}건. 종료 시 계정별 잔여 포지션·주문은 account_end.json 및 results.json을 확인한다.","", "receive→live 지연은 native private 활성 주문 알림 또는 REST 활성 상태를 처음 관측한 시각의 대용치다. REST 관측에는 조회 간격이 포함된다. 관측 주문이 없으면 지연 차감 후 잔여 이동 검증도 미측정이다. 관측이 하나이면 p95는 그 한 값이며 안정적인 지연 분포를 뜻하지 않는다. 계정 총 equity 차이는 본 전략의 체결 손익으로 대체하지 않았다.", "", "원본: run.sqlite, manifest.json, complete.json, account_end.json. 가공 결과: results.json, performance.csv."]
    notes=output/"REPORT_NOTES_KO.md"
    if notes.exists():lines.extend(["",notes.read_text(encoding="utf-8").strip()])
    (output/"REPORT_KO.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    db.close()
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("directory",type=Path);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();result=analyze(args.directory,args.output)
    print(json.dumps({"cleanup_complete":result["completion"]["complete"],"requested_duration_met":result["requested_duration_met"],"performance":{k:{f:v[f] for f in ('submitted_orders','fills','net_pnl_usdt')} for k,v in result['performance'].items()}},indent=2))
