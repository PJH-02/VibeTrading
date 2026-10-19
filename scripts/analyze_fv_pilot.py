"""Paper PnL, native data diagnostics and frozen-FV response tests."""
import argparse
import bisect
from collections import Counter
import csv
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.analyze_fv_run import diagnostics,adjust_bh

KST=timezone(timedelta(hours=9))


def metrics(orders,fills,states,currency):
    inventory,cash,fees=0.,0.,0.;episodes=[];episode=0.;began=None
    by_id={r['id']:r for r in orders}
    for f in sorted(fills,key=lambda r:(r['t'],r['id'])):
        qty,price,fee=map(float,(f['qty'],f['price'],f['fee']));sign=1 if f['side']=='Buy' else -1
        if abs(inventory)<1e-10:episode=0.;began=f['t']
        inventory+=sign*qty;cash-=sign*qty*price;fees+=fee;episode-=sign*qty*price+fee
        if abs(inventory)<1e-8:
            inventory=0.
            episodes.append({'net_ex_funding':episode,'holding_seconds':f['t']-began,'forced':by_id[f['order_id']]['role']=='FORCED_REDUCE'})
    last=states[-1] if states else None;mid=float(last['mid']) if last and last['mid'] else None
    gross=cash+inventory*mid if mid is not None else cash if not inventory else None
    pnl=gross-fees if gross is not None else None
    values=[float(r['pnl_ex_funding']) for r in states]
    peak=dd=0.
    for value in values:peak=max(peak,value);dd=max(dd,peak-value)
    terminal=Counter(o['status'] for o in orders);bases=Counter(f['basis'] for f in fills)
    return {'currency':currency,'order_intents':len(orders),'activated_maker_intents':sum(o['live'] is not None for o in orders),'cancel_intents':sum(o['cancel_request'] is not None for o in orders),'hypothetical_fills':len(fills),'hypothetical_filled_orders':len({r['order_id'] for r in fills}),'maker_fills':sum(bool(r['maker']) for r in fills),'taker_fills':sum(not r['maker'] for r in fills),'hypothetical_notional':sum(float(r['qty'])*float(r['price']) for r in fills),'assumed_fees':fees,'gross_pnl':gross,'pnl_ex_funding':pnl,'funding_pnl':0. if not fills else None,'funding_note':'PnL excludes funding; if no hypothetical fills the funding exposure is zero. Otherwise settled funding is not inferred from current rate.','end_inventory':inventory,'full_round_trips':len(episodes),'normal_close_rate':sum(not e['forced'] for e in episodes)/len(episodes) if episodes else None,'round_trip_win_rate':sum(e['net_ex_funding']>0 for e in episodes)/len(episodes) if episodes else None,'observed_max_drawdown_ex_funding':dd,'episodes':episodes,'terminal_states':dict(terminal),'fill_evidence':dict(bases),'external_order_submissions':0,'annualized_sharpe':None,'calmar':None,'execution_note':'Hypothetical queue/volume fills, not exchange fills. No real order was sent; private fill recognition delay is not separately modeled.'}


def latency_move(rows,latency):
    times=[r['t'] for r in rows];effects=[];blocks=set()
    for r in rows:
        if r['fv'] is None:continue
        i,j=bisect.bisect_left(times,r['t']+latency),bisect.bisect_left(times,r['t']+30)
        if i<len(rows) and j<len(rows) and times[i]-r['t']-latency<=2.5 and times[j]-r['t']-30<=2.5:
            effects.append(float(np.sign(r['fv']-r['mid'])*(rows[j]['mid']-rows[i]['mid'])/r['mid']*10000));blocks.add(int((r['t']-times[0])/60))
    return {'assumed_order_delay_seconds':latency,'n':len(effects),'signed_remaining_move_mean_bps':float(np.mean(effects)) if effects else None,'time_blocks':len(blocks),'note':'Descriptive FV-oriented price move from first 1s sample after assumed delay to 30s target; not a simulated fill or event-level executable edge.'}


def longer_block_check(rows,coefficient):
    """Sensitivity to the activity window's longer dependence, without refitting."""
    times=np.array([r['t'] for r in rows]);pairs=[]
    for r in rows:
        if r['reason'] not in ('ok','unfitted_coefficient','convergence_coefficient_out_of_range_or_unfitted'):continue
        k=int(np.searchsorted(times,r['t']+30))
        if k<len(rows) and times[k]-r['t']-30<=2.5:pairs.append((r['t'],r['x'],rows[k]['mid']-r['mid']))
    if not pairs:return {'n':0,'reason':'no_valid_pairs'}
    a=np.array(pairs);t,x,y=a.T;ids=np.floor((t-t[0])/120).astype(int)
    blocks=[np.where(ids==b)[0] for b in sorted(set(ids))]
    result={'n':len(a),'block_seconds':120,'time_blocks':len(blocks),'method':'H=30; 1000 resamples of 120s blocks; sensitivity only; coefficients remain frozen'}
    if len(blocks)<10:return {**result,'reason':'insufficient_time_blocks','slope_ci95':None,'mse_improvement_ci95':None}
    rng=np.random.default_rng(20261124);slopes=[];gains=[]
    for _ in range(1000):
        ix=np.concatenate([blocks[b] for b in rng.integers(0,len(blocks),len(blocks))]);den=float(x[ix]@x[ix])
        if den>1e-18:slopes.append(float(x[ix]@y[ix]/den))
        if coefficient is not None:gains.append(float(np.mean(y[ix]**2-(y[ix]-coefficient*x[ix])**2)))
    return {**result,'slope_ci95':np.quantile(slopes,[.025,.975]).tolist() if slopes else None,'mse_improvement_ci95':np.quantile(gains,[.025,.975]).tolist() if gains else None}


def analyze(directory,output):
    manifest=json.loads((directory/'manifest.json').read_text());complete=json.loads((directory/'complete.json').read_text())
    db=sqlite3.connect(directory/'run.sqlite');db.row_factory=sqlite3.Row
    performance,stress,tests,data={}, {},{},{}
    bounds=complete['evaluation_start_ns'],complete['evaluation_end_ns']
    native_by={(r['venue'],r['asset']):{k:r[k] for k in ('n','unknown','first_ns','last_ns')} for r in db.execute("SELECT venue,asset,count(*) n,sum(side='Unknown') unknown,min(source_ns) first_ns,max(source_ns) last_ns FROM public_trades WHERE wall_ns BETWEEN ? AND ? GROUP BY venue,asset",bounds)}
    books_by={(r['venue'],r['asset']):{k:r[k] for k in ('n','unknown_source')} for r in db.execute("SELECT venue,asset,count(*) n,sum(source_ns=0) unknown_source FROM public_frames WHERE kind='book' AND wall_ns BETWEEN ? AND ? GROUP BY venue,asset",bounds)}
    for venue in manifest['venues']:
        for sid,(_,asset) in manifest['routes'].items():
            key=venue+':'+sid;m=manifest['models'][key];instrument=manifest['instruments'][venue][asset]
            samples=[dict(r) for r in db.execute("SELECT * FROM samples WHERE venue=? AND strategy=? AND phase='evaluation' ORDER BY t",(venue,sid))]
            decision_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pilot_decisions'").fetchone()
            decisions=[dict(r) for r in db.execute("SELECT * FROM pilot_decisions WHERE venue=? AND strategy=? AND wall_ns BETWEEN ? AND ? ORDER BY t",(venue,sid,complete['evaluation_start_ns'],complete['evaluation_end_ns']))] if decision_table else []
            for scenario,target in (('measured',performance),('stress2x',stress)):
                orders=[dict(r) for r in db.execute('SELECT * FROM pilot_orders WHERE scenario=? AND venue=? AND strategy=? ORDER BY born',(scenario,venue,sid))]
                fills=[dict(r) for r in db.execute('SELECT * FROM pilot_fills WHERE scenario=? AND venue=? AND strategy=? ORDER BY t,id',(scenario,venue,sid))]
                states=[dict(r) for r in db.execute('SELECT * FROM pilot_states WHERE scenario=? AND venue=? AND strategy=? ORDER BY t',(scenario,venue,sid))]
                target[key]={'asset':asset,'model':m,**metrics(orders,fills,states,instrument['currency'])}
            tests[key]={str(h):diagnostics(samples,h,m.get('coefficient') if m.get('valid') else None) for h in (10,30,60)}
            for record in tests[key].values():
                record['interpretation']='exploratory mainnet public-data pilot; overlapping labels and short history; association is not causation'
            tests[key]['remaining_move_measured']=latency_move(samples,manifest['latencies'][venue]['entry_seconds'])
            tests[key]['remaining_move_stress2x']=latency_move(samples,2*manifest['latencies'][venue]['entry_seconds'])
            tests[key]['longer_block_sensitivity']={"30":longer_block_check(samples,m.get('coefficient') if m.get('valid') else None)}
            fee=float(instrument['maker_fee'])+float(instrument['taker_fee']);tick=float(instrument['tick']);allowance=float(m.get('error_allowance') or 0)
            valid=[r for r in samples if r['fv'] is not None]
            ratios=[abs(r['fv']-r['mid'])/(r['mid']*fee+tick+allowance) for r in valid]
            native=native_by.get((venue,asset),{'n':0,'unknown':0,'first_ns':None,'last_ns':None})
            book=books_by.get((venue,asset),{'n':0,'unknown_source':0})
            data[key]={'samples_1s':len(samples),'valid_fv_samples':len(valid),'fresh_snapshot_fraction':min(1,len(samples)/complete['evaluation_elapsed_seconds']),'maximum_edge_to_cost_ratio':max(ratios) if ratios else None,'edge_above_cost_1s_snapshots':sum(r>1 for r in ratios),'changed_quote_decision_rows':len(decisions),'decision_reasons':dict(Counter(r['reason'] for r in decisions)),'native_live_trades':native,'native_books':book,'note':'Decision reasons count changed quote states, not time fractions or every event; 1s observations are not exhaustive quote opportunities.'}
    adjust_bh(tests)
    result={'manifest':manifest,'completion':complete,'requested_duration_met':complete['requested_duration_met'],'performance':performance,'stress_performance':stress,'hypotheses':tests,'data_quality':data,'feed_gaps':[dict(r) for r in db.execute("SELECT venue,count(*) n FROM events WHERE kind='feed_gap' GROUP BY venue")],'external_order_submissions':0,'interpretation':'Hypothetical mainnet paper trading. USD and USDT are kept separate. No causal/profitability proof from 30 minutes.'}
    output.mkdir(parents=True,exist_ok=True);(output/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    with (output/'performance.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['scenario','venue_strategy','asset','currency','order_intents','activated_maker_intents','hypothetical_fills','full_round_trips','assumed_fees','pnl_ex_funding','end_inventory','observed_max_drawdown_ex_funding']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for scenario,records in (('measured',performance),('stress2x',stress)):
            for key,r in records.items():w.writerow({'scenario':scenario,'venue_strategy':key,**{field:r[field] for field in fields[2:]}})
    fmt=lambda value:'미측정' if value is None else f'{value:.6f}'
    iso=lambda ns:datetime.fromtimestamp(ns/1e9,KST).isoformat(timespec='seconds')
    lines=['# 실거래 호가 기반 4개 거래소 × 4개 전략 모의 거래','',f"평가: {iso(complete['evaluation_start_ns'])} ~ {iso(complete['evaluation_end_ns'])}, {complete['evaluation_elapsed_seconds']:.2f}초. 요청 기간({manifest['duration']}초) 충족: {'예' if complete['requested_duration_met'] else '아니오'}. 외부 주문 제출: **0건**.",'','S1=BTC, S2=ETH, S3=SOL, S4=XRP. 실제 공개 mainnet 호가·공격 체결·Binance USDT perp 기준가격을 서버에서 수신했다. 별도 사전 구간에서 보정한 계수와 basis를 평가 중 동결했다.','', '## 지연과 체결 가정','', '| 거래소 | 주문 지연 | 취소 지연 | 근거 |','|---|---:|---:|---|']
    for venue,budget in manifest['latencies'].items():lines.append(f"| {venue} | {budget['entry_seconds']*1000:.1f}ms | {budget['cancel_seconds']*1000:.1f}ms | {'Bybit/GRVT 실측의 대용값' if budget.get('proxy') else 'testnet 실측 관측 지연'}; 표본 {budget['entry_samples']}개 |")
    lines+=['','Bybit 주문/취소는 표본 각각 1개, GRVT 주문은 3개다. 안정적인 mainnet p95를 측정한 결과가 아니다. GRVT 취소 지연도 주문 지연으로 대용했다. 2배 지연 시나리오는 같은 공개 데이터와 동결 계수를 사용했다.','', '지연 후 관측한 호가가 주문 가격과 교차하면 PostOnly 의도를 거절한다. Maker는 활성화 때 표시 잔량 전부를 앞선 대기열로 두며, 이후 확인한 반대 방향 거래량만 사용한다. 호가 터치·표시 잔량 감소를 체결로 취급하지 않고 취소량에 따른 queue 개선도 가정하지 않는다. 알려진 가격을 실제 거래가 넘어가면 가격 우선순위를 적용하되 해당 거래량까지만 가정 체결한다. 관측 깊이 밖은 빈 queue로 처리하지 않는다. IOC 감축은 지연 후 관측 L2 깊이를 보호 가격까지 소진하고 부분 체결을 허용한다. 동일 book 버전의 소진량을 재사용하지 않는다.','', '모의 주문은 실제 호가창에 존재하지 않으므로 외부 잔량에서 빼지 않는다. public 체결 수신 때 모의 재고를 갱신하며 private 체결 통지 지연은 별도 모델링하지 않았다. 공개 데이터와 실제 주문이 서로 영향을 주지 않는다는 가정이 포함된다.','', '## 기본 지연의 모의 성과','', '| 거래소·전략 | 통화 | 의도 | 활성 maker | 가정 체결 | 완전 왕복 | 가정 수수료 | 손익(funding 제외) | 종료 재고 |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for key,r in performance.items():lines.append(f"| {key} | {r['currency']} | {r['order_intents']} | {r['activated_maker_intents']} | {r['hypothetical_fills']} | {r['full_round_trips']} | {fmt(r['assumed_fees'])} | {fmt(r['pnl_ex_funding'])} | {fmt(r['end_inventory'])} |")
    lines+=['','Arcus는 USD, 다른 거래소는 USDT다. 환산하지 않고 별도 표시한다. 수수료는 공개 기본 요율 또는 보수적 최대 요율을 적용한 가정값이다. funding 제외 손익이며, 체결이 있는 경우 실제 정산 funding을 0으로 간주하지 않는다. 미청산 재고가 있으면 마지막 관측 mid로 평가하며 결과 JSON에 남긴다.','', '## 2배 지연 비교','', '| 거래소·전략 | 기본 의도 / 체결 | 2배 의도 / 체결 | 기본 손익 | 2배 손익 |','|---|---:|---:|---:|---:|']
    for key,r in performance.items():
        s=stress[key];lines.append(f"| {key} | {r['order_intents']} / {r['hypothetical_fills']} | {s['order_intents']} / {s['hypothetical_fills']} | {fmt(r['pnl_ex_funding'])} | {fmt(s['pnl_ex_funding'])} |")
    lines+=['','## 모델 보정과 데이터','', '| 거래소·전략 | 고정 계수 | 신규 FV | 1초 표본 | 최대 edge/C | live 거래 표본 | 보정 결과 |','|---|---:|---|---:|---:|---:|---|']
    for key,r in performance.items():
        m=r['model'];d=data[key];lines.append(f"| {key} | {fmt(m.get('coefficient'))} | {'가능' if m.get('valid') else '차단'} | {d['samples_1s']} | {fmt(d['maximum_edge_to_cost_ratio'])} | {d['native_live_trades']['n']} | {m['reason']} |")
    lines+=['','S2의 lambda가 (0,1]을 벗어나거나 입력 활동량·보정 표본이 부족하면 진입을 차단했다. 유효한 신호도 수수료·1 tick·사전 과대예측 오차 90분위수의 비용 기준과 수량 한도를 통과해야 한다. 원시 신호·후보 bid/ask와 차단 사유는 pilot_decisions, 경제적으로 가능한 의도는 pilot_orders, 가정 체결은 pilot_fills에 구분했다.','', '## 같은 기간의 미래 가격 검증','', 'H=30초를 주 구간으로 정했고 10·60초는 보조 진단이다. 60초 시간 블록 bootstrap 1,000회와 BH 다중 비교 보정을 적용했다. 10개 미만의 블록은 추론을 보류한다. 모의 손익과 원시 신호의 상관관계를 구분한다.','', '| 거래소·전략 | H=30 쌍 / 비중첩 쌍 | 기울기 | 95% 블록 CI | BH q | 동결 FV RMSE / mid 유지 RMSE |','|---|---:|---:|---|---:|---|']
    for key,records in tests.items():
        r=records['30'];ci=r.get('slope_ci95_block_bootstrap');text=f'[{ci[0]:.4g}, {ci[1]:.4g}]' if ci else '미측정'
        if r.get('inference_sufficient_time_blocks') is False:text='시간 블록 부족'
        rmse=f"{r['forecast_rmse']:.6g} / {r['no_change_rmse']:.6g}" if 'forecast_rmse' in r else '배포 가능한 FV 없음'
        lines.append(f"| {key} | {r.get('n',0)} / {r.get('nonoverlapping_pairs',0)} | {fmt(r.get('feature_slope_zero_intercept'))} | {text} | {fmt(r.get('q_bh_exploratory'))} | {rmse} |")
    lines+=['',f"공개 피드 공백/재연결: {sum(r['n'] for r in result['feed_gaps'])}건. 원본 연결·source timestamp 누락·과거 snapshot 체결 제외 기록은 run.sqlite에 있다. 지연 차감 뒤 잔여 방향 이동은 results.json의 remaining_move 항목이며 1초 표본 진단이어서 실제 체결 가격과 같지 않다.",'','30분 결과로 지속적인 알파·인과관계·실제 체결 수익성을 확정하지 않는다. 종목이 서로 다르므로 전략 순위로 해석하지 않는다. 연환산 Sharpe·Calmar는 미측정으로 둔다.','', '원본: manifest.json, complete.json, run.sqlite. 결과: results.json, performance.csv, 네 거래소의 FV 검증 그림.']
    lines+=['','활동량 창의 긴 의존성을 고려한 H=30·120초 블록 민감도 CI도 results.json의 longer_block_sensitivity에 기록했다. 이 보조 검사는 계수 보정·주문 조건을 바꾸지 않는다.']
    notes=output/'REPORT_NOTES_KO.md'
    if notes.exists():lines+=['',notes.read_text(encoding='utf-8')]
    (output/'REPORT_KO.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');db.close();return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=analyze(a.directory,a.output)
    print(json.dumps({'duration_met':r['requested_duration_met'],'external_order_submissions':r['external_order_submissions'],'performance':{k:{x:v[x] for x in ('order_intents','hypothetical_fills','pnl_ex_funding')} for k,v in r['performance'].items()}},indent=2))
