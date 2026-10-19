"""Scientific plots and Korean report from the complete reproducible grid."""
import json,csv
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path('docs/evidence/fv_research_20261004')
LABELS={'l1_obi':'L1 OBI','l2_total_obi_10':'L2 OBI (top 10)','ofi':'OFI / depth (asinh)',
        'replenishment':'Net visible additions','trade_imbalance':'Trade imbalance','microprice':'Weighted BBO mid premium',
        'depletion':'Net visible depletion','signed_trade_flow':'Signed trade / depth (asinh)',
        'ofi_trade_joint':'OFI + signed trade','replenishment_rate_imbalance':'Replenishment rate imbalance',
        'trade_x_replenishment':'Trade x replenishment','tracked_depth_obi':'All tracked price levels'}
BASE=list(LABELS)[:6]
HS=[1,5,10,30,60,120,180]

def table(frame):
    cols=list(frame.columns);lines=['| '+' | '.join(map(str,cols))+' |','| '+' | '.join(['---']*len(cols))+' |']
    for row in frame.itertuples(index=False,name=None):
        lines.append('| '+' | '.join('미측정' if pd.isna(x) else format(x,'.4f' if 'q' in str(col) else '.3f' if '%p' in str(col) else '.2f') if isinstance(x,(float,np.floating)) else str(x) for col,x in zip(cols,row))+' |')
    return '\n'.join(lines)

def plot_grid(df,source,path):
    d=df[(df.source==source)&(df.label=='future')&df.indicator.isin(BASE)]
    fig,axes=plt.subplots(3,2,figsize=(14,10.5),layout='constrained')
    for ax,name in zip(axes.flat,BASE):
        g=d[d.indicator==name].groupby(['window','horizon']).rmse_reduction_percent.median().unstack()
        z=g.reindex(index=[1,5,10,30,60],columns=range(1,181)).to_numpy()
        im=ax.imshow(z,origin='lower',aspect='auto',extent=[.5,180.5,-.5,4.5],vmin=-5,vmax=5,cmap='RdYlGn')
        ax.set_yticks(range(5),['1s','5s','10s','30s','60s']);ax.set_xticks([1,30,60,90,120,150,180])
        ax.set_title(LABELS[name],loc='left');ax.set_xlabel('Future horizon (s)');ax.set_ylabel('Trailing signal window')
    fig.colorbar(im,ax=axes.ravel().tolist(),label='Median held-out RMSE reduction vs unchanged mid (%)\nColors saturate at +/-5; complete values in CSV',shrink=.8)
    fig.suptitle(f'{source}: all 180 future horizons calibrated independently\nEqual-weight median across available venue/asset routes; H=0 excluded',fontsize=15)
    fig.savefig(path,dpi=160);plt.close(fig)

def main():
    df=pd.read_csv(ROOT/'indicator_metrics.csv');summary=json.loads((ROOT/'summary.json').read_text(encoding='utf-8'))
    original=json.loads(Path('docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json').read_text(encoding='utf-8'))
    audit=json.loads((ROOT/'original_formula_audit.json').read_text(encoding='utf-8'))
    audited_samples=sum(r['samples'] for r in audit.values())
    mismatches=sum(r['signal_mismatches']+r['fv_mismatches']+r['reason_availability_mismatches'] for r in audit.values())
    original_rows=[]
    for route,p in original['performance'].items():
        h=original['hypotheses'][route]['30'];quality=original['data_quality'][route]
        rmse=h.get('forecast_rmse');base=h.get('no_change_rmse')
        original_rows.append({'경로':route,'종목':p['asset'],'동결 계수':p['model'].get('coefficient'),
                              'FV 가능':p['model']['valid'],'30초 RMSE 감소(%)':100*(1-rmse/base) if rmse is not None and base else None,
                              '원 주검정 q':h.get('q_bh_exploratory'),'1초 표본 최대 edge/C':quality.get('maximum_edge_to_cost_ratio'),
                              '의도':p['order_intents'],'체결':p['hypothetical_fills']})
    original_table=pd.DataFrame(original_rows);original_table.to_csv(ROOT/'original_16_routes.csv',index=False,encoding='utf-8-sig')
    for s in ('production','pilot_test'):plot_grid(df,s,ROOT/(s+'_window_horizon.png'))
    responses=pd.read_csv(ROOT/'response_curves.csv')
    fig,axes=plt.subplots(3,2,figsize=(13,10),layout='constrained')
    for ax,name in zip(axes.flat,BASE):
        for source,color in [('production','#28678d'),('pilot_test','#c77935')]:
            r=responses[(responses.source==source)&(responses.indicator==name)&(responses.window==5)&(responses.high_n>=30)&(responses.low_n>=30)].copy()
            r['contrast']=r.high_mean_response_bps-r.low_mean_response_bps
            g=r.groupby('horizon').contrast.median();ax.plot(g.index,g.values,label=source,color=color)
        ax.axhline(0,color='#9ba4ac',lw=.8);ax.set_title(LABELS[name],loc='left')
        ax.set_xlabel('Seconds after signal');ax.set_ylabel('High - low future response (bp)');ax.legend(fontsize=8)
    fig.suptitle('Observed conditional response: W=5s, thresholds fixed at training quartiles\nEqual-route median; descriptive association, no fill assumptions',fontsize=14)
    fig.savefig(ROOT/'conditional_response.png',dpi=160);plt.close(fig)
    # Fixed 5s window, no best-window selection.
    tables={}
    direction_tables={}
    for source in ('production','pilot_test'):
        s=df[(df.source==source)&(df.label=='future')&(df.window==5)&df.horizon.isin(HS)&df.indicator.isin(LABELS)]
        t=s.groupby(['indicator','horizon']).rmse_reduction_percent.median().unstack().reindex(list(LABELS)).round(2)
        t.index=[LABELS.get(i,i) for i in t.index];t=t.reset_index().rename(columns={'index':'지표'})
        tables[source]=table(t)
        direction=s.groupby(['indicator','horizon']).balanced_accuracy_moving.median().unstack().reindex(list(LABELS))*100
        direction.index=[LABELS.get(i,i) for i in direction.index]
        direction_tables[source]=table(direction.reset_index().rename(columns={'index':'지표'}))
    cov=pd.DataFrame(summary['coverage']);cov['기간(KST)']=pd.to_datetime(cov.start,unit='s',utc=True).dt.tz_convert('Asia/Seoul').dt.strftime('%m-%d %H:%M')+' ~ '+pd.to_datetime(cov.end,unit='s',utc=True).dt.tz_convert('Asia/Seoul').dt.strftime('%m-%d %H:%M')
    cov['정상 시간(h)']=cov.good_seconds/3600
    coverage_table=table(cov[cov.source=='production'][['venue','asset','기간(KST)','정상 시간(h)','gap_free_60_back_180_forward_anchors']])
    d0=df[(df.label=='contemporaneous_past_window')&(df.window==5)].groupby(['source','indicator']).rmse_reduction_percent.median().unstack().reindex(columns=BASE)
    d0=d0.rename(columns=LABELS).reset_index()
    # Inspect all selection results without promoting them to confirmation.
    future=df[(df.label=='future')&df.indicator.isin(LABELS)]
    reliable=future[(future.horizon.isin([1,2,5,10,30,60,120,180]))&(future.time_blocks>=10)]
    passing=reliable[(reliable.q_loss_gain_bh_exploratory<.05)&(reliable.rmse_reduction_percent>0)&(reliable.rmse_reduction_vs_training_drift_percent>0)]
    best=reliable.sort_values('rmse_reduction_percent',ascending=False).head(12)
    columns=['source','venue','asset','indicator','window','horizon','n','moving_pairs','zero_pairs','rmse_reduction_percent','rmse_reduction_vs_training_drift_percent','balanced_accuracy_moving','median_abs_forecast_bps','q_loss_gain_bh_exploratory']
    best=best[columns].copy();best['indicator']=best.indicator.map(LABELS)
    best.to_csv(ROOT/'top_exploratory_cells.csv',index=False,encoding='utf-8-sig')
    readable_best=pd.DataFrame({'자료':best.source.map({'production':'운영','pilot_test':'pilot'}),
                               '경로':best.venue+':'+best.asset,'지표':best.indicator,
                               'W→H(초)':best.window.astype(str)+'→'+best.horizon.astype(str),
                               '평가/비영 쌍':best.n.astype(str)+'/'+best.moving_pairs.astype(str),
                               'RMSE 감소(%)':best.rmse_reduction_percent,
                               'drift 대비(%)':best.rmse_reduction_vs_training_drift_percent,
                               '방향 BA(%)':best.balanced_accuracy_moving*100,
                               '예측폭 중앙(bp)':best.median_abs_forecast_bps,
                               'q':best.q_loss_gain_bh_exploratory})
    counts=future.groupby('source').apply(lambda g:pd.Series({'model_cells':len(g),'positive_vs_mid':int((g.rmse_reduction_percent>0).sum()),'positive_vs_mid_and_drift':int(((g.rmse_reduction_percent>0)&(g.rmse_reduction_vs_training_drift_percent>0)).sum())}),include_groups=False) if tuple(int(i) for i in pd.__version__.split('.')[:2])>=(2,2) else future.groupby('source').apply(lambda g:pd.Series({'model_cells':len(g),'positive_vs_mid':int((g.rmse_reduction_percent>0).sum()),'positive_vs_mid_and_drift':int(((g.rmse_reduction_percent>0)&(g.rmse_reduction_vs_training_drift_percent>0)).sum())}))
    def fixed(source,name,h,column='rmse_reduction_percent'):
        values=future[(future.source==source)&(future.indicator==name)&(future.window==5)&(future.horizon==h)][column]
        return f'{values.median():.2f}'
    raw0=df[(df.source=='pilot_test')&(df.label=='contemporaneous_past_window')&(df.window==5)&(df.indicator=='ofi')].rmse_reduction_percent.median()
    matched=future[(future.window==5)&future.horizon.isin(HS)].pivot_table(
        index=['source','venue','asset','horizon','n'],columns='indicator',values='rmse_reduction_percent')
    joint_rows=[]
    for source in ('production','pilot_test'):
        for h in HS:
            m=matched.loc[(matched.index.get_level_values('source')==source)&(matched.index.get_level_values('horizon')==h)]
            pair=m[['ofi','ofi_trade_joint']].dropna()
            interaction=m[['signed_trade_flow','trade_x_replenishment']].dropna()
            joint_rows.append({'자료':source,'H(초)':h,'OFI 결합 비교 경로':len(pair),
                               '결합-OFI 개선 차이(%p)':(pair.ofi_trade_joint-pair.ofi).median(),
                               '체결×보충 비교 경로':len(interaction),
                               '상호작용-체결 개선 차이(%p)':(interaction.trade_x_replenishment-interaction.signed_trade_flow).median()})
    pd.DataFrame(joint_rows).to_csv(ROOT/'matched_combination_comparison.csv',index=False,encoding='utf-8-sig')
    (ROOT/'report_statistics.json').write_text(json.dumps({'exploratory_pass_cells':len(passing),'counts':counts.to_dict(),'best_rows':best.fillna('').to_dict('records')},ensure_ascii=False,indent=2),encoding='utf-8')
    body=f'''# 네 가지 FV 전략 실패 진단과 기초 지표의 시간축별 실증 보고서

데이터 고정 기준일: 2026-10-04 KST. 실거래를 추가하거나 전략을 다시 운용하지 않고, 기존 원본을 읽기 전용으로 분석했다. 원래 운용 결과와 이번 탐색 연구의 결과를 구분한다.

## 1. 결론과 판단 범위

기존 네 전략은 **운용 가능한 수익성을 입증하지 못했다.** 그러나 체결 0건을 네 수급 가설 자체의 전면 반증으로 해석할 수는 없다. ① 예측 이동폭이 작거나 불안정한 모델, ② FV를 계산하지 못한 경로, ③ 비용·수량 조건에 막힌 경로, ④ 도착 전 취소되는 실행 경로가 함께 있었다. 아래에서 구현 결함, 명세·모형의 한계, 실측으로 확인한 현상과 미확인 원인을 나눈다.

이번 지표 연구에서는 계산 창 1·5·10·30·60초와 **미래 1~180초의 매 1초**를 교차 평가했다. 0초는 신호 창과 같은 구간의 이미 발생한 가격 변화를 설명하는 별도 검사다. 0초 가격 차이를 0으로 놓고 성능을 높이는 방식은 사용하지 않았다. 전체 수치 {summary['metrics_rows']:,}행과 반응 곡선 {summary['response_rows']:,}행을 제공한다. 정확한 미래의 ‘진짜 FV’가 관측되는 것은 아니므로, 실현된 미래 mid를 대용 관측값으로 삼았다.

핵심 실측은 다음과 같다.

- **단기 방향 정보는 있다.** 운영 자료의 고정 W=5초에서 OFI의 H=5초 RMSE 감소 중앙값은 {fixed('production','ofi',5)}%, L1 OBI의 H=30초는 {fixed('production','l1_obi',30)}%였다. 전체 이동 크기를 설명하는 힘은 작고 비용 후 수익성은 확인되지 않았다.
- **같은 구간의 충격과 미래 지속은 다르다.** pilot의 W=5초 OFI는 이미 발생한 가격 변화의 RMSE를 {raw0:.2f}% 줄였지만, 미래 5초는 {fixed('pilot_test','ofi',5)}%였다. 기존 30초 FV 실패를 OFI와 가격의 관계 자체가 없다는 판정으로 확대하지 않는다.
- **산식 구현은 기록과 일치했다.** {audited_samples:,}개 기록 표본을 독립 검산해 불일치 {mismatches}건이었다. S2의 추정 목표·계수 해석 불일치, 짧은 학습과 계수 이동, 체결 방향/활동 부족, 의도의 도착 전 취소를 별도 원인으로 확인했다.

## 2. 이미 수행한 운용의 실측

| 구분 | 시간·범위 | 주문·체결 | 손익과 해석 |
|---|---|---|---|
| Bybit·GRVT 테스트넷 | 10-04 15:37:41~16:07:42 KST, 8개 경로 | Bybit S4 주문 1건, 최종 체결 0 | 최종 30분 손익 0; 실제 시장과 다른 테스트넷 가격을 시장 가설의 검증 자료로 사용하지 않음 |
| 실제 공개 호가 pilot | 10-04 19:21:55~19:51:55 KST, 16개 경로 | 기본 지연 의도 15건(Arcus S3 14, Bitget S1 1), 활성 기록 6건, 가정 체결 0 | 가정 수수료·손익·종료 재고 0; 2배 지연에서도 체결 0 |
| 원래 FV 평가 | 사전 10분 학습, 이후 30분 평가, H=30초 | 12개 FV 가능, 4개 배포 차단 | 원 보고서 최대 RMSE 감소 약 2.71%; 오차 대부분 남음. 주 검정 BH q<0.05 통과 0개 |

원래 경로는 S1=BTC, S2=ETH, S3=SOL, S4=XRP였다. 종목이 다르므로 원래 손익을 전략 간 순위로 해석할 수 없다. 이 보고서의 기초 지표는 같은 종목에서 여러 지표를 비교한다. 중단된 이전 GRVT 부분 시험의 +0.113898 USDT는 최종 30분 결과와 합산하지 않는다.

{table(original_table)}

이 표의 RMSE·q는 **원래 30분 보고서의 목표 표본 정의** 그대로다. 이번 raw as-of 재복원·고정 창·새 H별 계수 결과와 직접 같은 수치가 아니다. edge/C는 1초 관측이라 순간 주문 기회를 모두 포함하지 않는다. 모든 1초 표본이 비용 미달이어도 표본 사이에서 의도가 생길 수 있다.

원 주검정 q는 원시 시그널과 이후 가격 이동의 기울기 검정에 대한 값이다. FV를 배포하지 못한 S2의 q도 원시 시그널 진단이며 FV 오차 검정이 아니다. 이번 지표 grid의 q는 동결 drift 대비 MSE 개선 검정으로 검정 대상이 다르다.

**0건 체결의 직접 확인:** 의도 15건 모두 가정 주문 도착 전에 취소 요청이 생겼다. 생성→취소 중앙값 3.468ms, 범위 0.136~151.635ms다. Arcus·Bitget에 사용한 도착 지연은 GRVT 테스트넷의 1,080.451ms 대용값이며 각 거래소의 mainnet 실측이 아니다. 마지막 결정은 비용 미달 10건, 체결 이력 부족 2건, 여전히 eligible인 가격·수량 변경 3건이었다. 같은 가격에서 수량 1.236674→1.236695 SOL의 변화만으로 0.150ms 후 취소한 사례도 있다.

활성화된 Arcus 5건의 기록상 유지 시간은 3.839~26.095ms였고 이때 native 체결은 없었다. Bitget 1건은 152.487ms 동안 유지됐지만, 수신한 체결 13건 중 해당 한도까지 도달한 반대 방향 체결은 없었다. 시뮬레이터가 한 처리 주기에 도착·취소 효력 시각이 모두 지난 경우 취소부터 처리하는 한계도 있다. 활성 기록 없는 주문이 실제 거래소에서 한 순간도 살아 있지 않았다고 단정하지 않는다. **손익 0은 손실을 피했다는 성과나 수익성 부재의 통계적 증거가 아니다.** 체결 조건부 손익은 미측정이다.

## 3. 전략별: 논리 부재인가, 계산 문제인가

| 전략 | 실측·코드에서 확인한 내용 | 판정 |
|---|---|---|
| S1 순유동성 OFI | price-aware BBO OFI와 체결을 중복 합산하지 않는 식은 v4 명세와 일치한다. nu=1 활동량 창에서 절댓값(OFI/D)≤1이라 입력 이동폭은 spread 이내다. Bybit의 사전 계수 -1.934와 평가 원시 기울기 +22.291의 부호가 달랐다. Bitget의 연속 snapshot마다 이력을 초기화하던 결함은 최종 pilot 전에 수정됐다. | **OFI 논리 자체가 반증된 것은 아니다.** 과거 창의 동시 가격 충격을 이후 가격에 옮기는 지속성 가설은 별도다. 짧은 학습·현재 깊이 의존 창·spread 스케일·계수 불안정은 모형 한계이며, 원래 OFI 부호 식이 뒤집힌 계산 버그라는 증거는 없다. |
| S2 Binance anchor | 사전 lambda는 Arcus -0.291, Bybit 1.112, Bitget 1.744로 명세의 (0,1] 조건을 벗어나 차단됐다. Binance ETH는 pilot 중 200,595개 변경 BBO, 기준가 누락 0, 순번·source 시각 역행 0이었다. | **수집기 중단이 원인은 아니다.** target 전체 이동에 대한 회귀 계수를 순수 괴리 수렴율로 해석한 명세·목표의 불일치가 있다. 구현은 v4의 회귀·차단 조건을 따랐으므로 단순 코딩 오타로 분류하지 않는다. 고정 basis와 USD/USDT·계약 차이는 추가 가정이다. |
| S3 공격 체결 지속 | 활동량 창 중앙값 Arcus 55.75초, Bybit 8.51초, Bitget 20.15초로 달랐다. Arcus 사전 beta -4.013에서 평가 기울기 +2.797, Bitget 사전 +2.449에서 평가 -3.129로 바뀌었다. GRVT 사전 현재 체결 활동이 부족해 FV를 배포하지 못했다. | **단독 체결 지속 모델이 안정적이지 않았다는 증거**다. 물량이 나와도 반대 호가 재공급이 흡수할 수 있다. GRVT는 학습 불가능과 논리 실패를 구분해야 한다. native 방향이 없는 체결을 추측하지 않는 것은 올바른 처리다. |
| S4 BBO OBI | 현재 L1 잔량 비율×spread 식은 명세대로다. 이전 동결 30초 계수 진단에서 Bybit XRP H=5초의 RMSE 감소 4.12%, 이동 표본 balanced accuracy 약 65.7%였지만 예측 이동 중앙값은 약 0.187bp였다. | **‘잔량 비대칭에 방향 정보가 전혀 없다’는 결론은 지지되지 않는다.** 다만 몇 초 뒤 방향과 30초 전체 이동폭은 다른 목표이고, 순간 잔량·깊은 호가·지속성·소진 속도를 구분하지 않은 모형은 제한적이다. |

활동량 창 S1·S3은 고정 시간 창 지표와 동일한 모델이 아니다. 이번 연구 결과로 기존 계수를 소급 수정하거나 원래 성과를 바꾸지 않는다. 관측된 입력 부호 변화는 이번 표본의 불안정성 증거이며, 장기간 언제나 반전한다는 뜻이 아니다.

**계산식의 독립 검산:** 전략 클래스와 runtime 활동량 창을 가져오지 않고, 수신 순서의 호가·실시간으로 받아들인 체결·동결 계수만으로 시그널과 FV를 다시 계산했다. 16개 경로의 기록 표본 {audited_samples:,}개에서 시그널/FV/계산 가능 여부 불일치는 {mismatches}건이었다. 이것은 명세대로 계산했다는 검증이며 명세 자체의 타당성을 증명하지 않는다. L1 깊이·순번·창·시간 가중·미래 구간 결측 제외의 별도 회귀 시험도 수행했다.

**입력 검증의 별도 결함:** Arcus BTC raw L2에는 crossed/locked 상태 857회(19개 초)가 있었고, BBO 변경 원장에는 비정상 상태 또는 그 이전 상태를 포함한 OFI 전이 179회가 있었다. runtime이 현재 FV를 만들 때만 book을 검사하고 OFI 이력 삽입 전에는 검사하지 않는 경로를 확인했다. 다만 이번 기록 FV 표본 중 그 전이를 활동량 창에 사용한 표본은 **0개**였다. 따라서 수정할 잠재 결함이지만 이번 관측 실패의 직접 원인으로 수치상 귀속하지 않는다. 이번 기초 지표 연구에서는 비정상 상태를 가로지르는 창 전체를 제외했다.

**S2의 사후 항등식:** `target 이동 = anchor 이동 + (현재 gap - 미래 gap)`. 같은 208개 사전 학습 쌍에서 전체 계수를 분해하면 다음과 같다. 미래 Binance는 이 사후 분해의 관측값으로만 사용했고 예측 입력에 넣지 않았다.

| 거래소 | 전체 lambda | 괴리 축소 성분 | anchor 미래 이동 성분 |
|---|---:|---:|---:|
| Arcus | -0.291 | +0.512 | -0.803 |
| Bybit | +1.112 | +0.750 | +0.362 |
| Bitget | +1.744 | +0.977 | +0.767 |
| GRVT | +0.142 | +0.081 | +0.061 |

따라서 범위 밖 전체 lambda만으로 괴리가 전혀 줄지 않는다고 말할 수 없다. 반대로 이 분해가 경제적 수렴이나 실행 가능한 차익을 증명하지도 않는다. pilot 보정 gap 중앙값의 크기 약 0.30~0.85bp는 maker 진입+taker 감축 기본 수수료 2.25/7.5/8/4.5bp보다 작았다. lambda를 강제로 자르거나 차단을 없애는 것만으로 비용 문제가 해결되지 않는다.

## 4. 원본 범위와 결측 제외

실험 원장 외에 운영 서버의 현시점 active catalogue에 등록된 공개 perpetual 호가·체결 원본 전체를 고정 목록으로 읽었다. Ncloud 보관본 또는 로컬 원본을 파일 크기·SHA-256으로 검증했다. 데이터 수집·정리 프로세스는 그대로 운영했고 분석은 별도 CPU·메모리 제한 프로세스에서 실행했다. 새로 도착하는 자료는 고정 catalogue 뒤의 추가 데이터이므로 이번 평가에 포함하지 않았다.

검증한 운영 원본은 **38,326개 파일·64,304,311개 이벤트**(약 13.64GB)다. Binance/Bybit/Bitget 약 2.84일, Arcus/GRVT 약 22시간의 원본 중 아래 정상 구간만 사용했다. 파일 전체를 검증했다는 것과 모든 초가 분석 가능하다는 것은 다르다.

{coverage_table}

운영 대상은 BTC·ETH·SOL이며 XRP는 별도 pilot에만 있다. Binance 운영 L2도 지표 기준선을 위한 별도 거래소로 포함했다. USD Arcus와 USDT 거래소의 가격을 합쳐 가격 단위 오차를 계산하지 않고, 각 종목 mid 대비 bp로 평가했다. 운영 데이터와 pilot은 같은 시간대가 일부 겹치므로 독립 실험으로 합산하지 않고 따로 보여준다.

정상 구간의 조건은 snapshot으로 초기화된 book, 거래소별 순번 연결, 유효한 양쪽 최소 10단계, 양의 잔량, bid<ask, 수신 시간의 순방향과 공개 연결의 연속성이다. collector outage 원장과 venue 공개 연결의 3초 초과 무수신 구간도 제외했다. 개별 종목의 조용한 호가를 결측으로 처리하지 않았다. snapshot 경계의 첫 60초와 목표 180초를 확보하지 못하는 끝 구간도 제외했다. **모든 모델의 표본은 과거 60초~미래 180초 전체 경로가 정상인 공통 지지 구간**으로 제한했다. 결측을 가격 보간으로 메우지 않았다.

pilot의 feed-gap 로그 0건과 모든 raw book이 정상이라는 말은 다르다. L2 복원에서 일시적인 비정상 book 상태가 나오면 그 상태를 가로지르는 창을 제외했다. source 시각이 없는 Arcus 호가는 거래소 시각을 만들어 넣지 않고 서버 수신 시각만 사용했다. 거래가 없는 초는 자동으로 누락이 아니다. source보다 수신이 10초 이상 늦은 초기 과거 체결은 실시간 흐름에서 제외했다.

과거 삭제된 세대·비활성 중복 보관본은 current active 원본 범위에 포함하지 않는다. 로컬 2026-09-26 capacity replay 조각은 호가 약 2.92초뿐이라 60초 관측·180초 반응이나 별도 학습/평가를 만들 수 없어 제외했다. 테스트넷, 10초 smoke, 중단된 수집은 정상 mainnet 예측 검증에 합치지 않았다. 공개 L2로 네이티브 feed가 전달하지 않은 개별 주문·취소를 완전하게 감사할 수는 없다.

## 5. 기초 지표의 정확한 계산

| 지표 | 이 보고서의 정의·한계 |
|---|---|
| L1 OBI | (best bid 수량-best ask 수량)/(합계). W 동안 상태의 **시간 가중 평균**. 이벤트가 많은 순간에 평균이 치우치지 않음. |
| L2 total OBI | 양쪽 **가까운 10개 가격 단계 총수량**의 비대칭. BBO로 L2를 대체하지 않음. 비교 가능한 고정 깊이가 주 지표. |
| 전체 추적 깊이 OBI | 실제 유지된 모든 관측 가격 단계 합계의 비대칭. 원래 snapshot 깊이·공급 수준·깊은 단계의 관측 절단이 달라 비교 가능성이 제한되어 보조 지표로만 사용. |
| OFI | BBO 가격 이동을 고려한 Cont 형식 signed quantity 변화의 W 합계. W 시간평균 BBO 깊이로 나누고 asinh 변환. 현재 한 순간의 깊이에 나눈 무제한 곱셈 폭증을 줄임. |
| Liquidity replenishment | 이전·현재 top10에 **함께 존재한 같은 가격**의 순표시 잔량 증가. (bid 증가-ask 증가)/(증가 합계). 소진 지표는 (ask 감소-bid 감소)/(감소 합계). 가격 단계가 순위 밖으로 나간 것을 취소로 세지 않음. |
| 보충률 비대칭 | bid 증가/(bid 증가+bid 감소) - ask 증가/(ask 증가+ask 감소). 위의 관측 순변화로 계산하는 보조 지표이며 gross 제출률이 아님. |
| 체결 불균형 | 실제 aggressor buy/sell volume의 (buy-sell)/(buy+sell). 거래가 없으면 0, 방향 unknown이 존재한 창은 제외. |
| Signed 체결 흐름 | asinh((buy-sell)/W의 시간평균 BBO 깊이). OFI와 물리적으로 합산하지 않음. |
| microprice 대용 | `weighted_mid=(ask×bid_qty+bid×ask_qty)/(bid_qty+ask_qty)`. (weighted_mid-mid)의 bp 프리미엄을 W 시간평균. **Stoikov의 상태전이 기반 full microprice를 구현한 것은 아님.** OBI×반spread와 수학적으로 연결되어 완전히 독립된 정보가 아님. |
| 복합 지표 | OFI+signed 체결의 별도 ridge 계수. 체결×보충률도 보조 비교. 동일 체결을 OFI에 다시 더하는 방식이 아님. |

순표시 변화는 체결·취소·새 주문의 합성 결과다. 한 업데이트 사이의 매도 100 체결과 매도 100 재공급은 book에 net 0으로 남을 수 있다. 따라서 이 지표를 전체 공급 제출량이나 실제 취소량이라고 부를 수 없다. 공개 book와 trade의 주문 ID 연결이 없어 보충·흡수의 인과적 식별은 제한된다.

또한 위 보충·소진의 보수적 정의는 이전·현재 top10에 공통으로 남은 가격만 포함하므로 **완전히 사라진 가격 단계의 소진이나 새 가격 단계의 삽입은 제외**한다. 순위 경계 효과를 줄인 대신 완전 소진을 포착하지 못한다. 이 제한된 지표의 약한 성능만으로 실제 전체 호가 소진·재공급 가설을 반증할 수 없다. 거래 방향 unknown인 창은 trade 관련 모델에서 추가 제외하므로 그 모델의 표본 수는 호가 지표와 다를 수 있다.

운영 Arcus 수집 코드는 원본 side가 있어도 aggressor를 UNKNOWN으로 저장한 경로가 있다. 그 과거 방향은 복구하여 상상하지 않으며 운영 Arcus 체결 지표를 측정 가능으로 만들지 않았다. pilot은 native side를 보존했으므로 별도 평가가 가능하다. GRVT 사전 pilot의 현재 체결 활동은 부족했다. 또한 운영 selector와 pilot selector가 달라 운영 trade 연속성·activity를 동일하다고 가정하지 않는다. 미측정 사유는 [unavailable.csv](unavailable.csv), 추출의 과거 체결 제외·순번·호가 오류는 감사 JSON에 남긴다.

운영 GRVT 원본 체결은 BTC 107/ETH 104/SOL 105건이었으며, source→수신 10초 초과의 초기 과거 체결을 빼면 각각 **2/0/1건**만 남았다. 이 극소수도 초기 snapshot의 최근 체결일 수 있어 연속 live trade feed를 증명하지 않는다. 이 자료에서 체결 지표를 학습할 정보량이 충분하다고 보지 않는다. 학습 1/99% clip 뒤 상수인 지표도 FV 모델 성능으로 표시하지 않고 미측정으로 제외했다.

추가 공개 GRVT 진단에서는 bare selector와 `@50` selector를 동시에 120초 관측했다. 양쪽 모두 BTC/ETH/SOL의 초기 과거 체결을 각각 50건씩 받았고 현재 체결은 0건이었다. ACK의 sequence 카운터는 달랐지만 오류는 없었다. **이 결과로 suffix 변경이 현재 체결 누락을 해결했다고 주장할 수 없다.** 공식 selector 규격과 최근 실제 거래 활동을 함께 확인해야 한다. 이 후속 짧은 진단은 모델 평가 자료에 넣지 않았다.

## 6. 미래 FV 예측 평가 방법

복합 모델은 **각 입력이 학습 구간과 clip 이후 모두 변동해야** 측정 가능으로 인정한다. 체결 입력이 상수여서 OFI만 재현한 모델을 결합 효과로 세지 않는다. 방향 unknown인 운영 Arcus와 활동이 부족한 GRVT의 결합 경로는 이 기준에 따라 제외된다.

운영 원본은 시간순 앞 60%로 학습하고 뒤 40%로 평가한다. 학습 label이 경계를 넘지 않게 180초 purge, 평가 신호 창이 학습 구간에 걸치지 않게 60초 embargo를 적용했다. 신호 anchor는 운영에서 5초 간격, pilot에서는 1초 간격이며 호가·흐름의 집계 해상도는 1초다. pilot은 별도의 10분 원장을 학습에, 30분 원장을 평가에 사용한다.

H별 목표는 `y_H=10000×(mid(t+H)/mid(t)-1)`이다. t+H까지 수신한 book 상태를 사용한다. 목표 이후의 다음 호가를 가져오지 않는다. 추정 FV는 `mid(t)×(1+예측bp/10000)`이다. 각 W·H·거래소·종목·지표마다 학습 구간의 1/99% winsor 범위와 표준화를 동결하고 ridge=10으로 계수·절편을 보정한다. 평가 기간으로 계수·clip 범위를 맞추지 않았다. 기초 지표 성능을 비교하는 규칙을 고정한 것이며, 기존 네 전략 코드의 원래 FV 식과 같은 결과가 아니다.

두 기준선을 함께 제공한다. 첫째 **현재 mid 유지**(예측 이동 0), 둘째 학습 구간의 평균 H초 이동을 그대로 예측하는 **동결 drift**다. mid 유지보다 좋아도 drift보다 못하면 그 개선을 해당 지표의 정보라고 부르기 어렵다. RMSE 감소율은 100×(1-RMSE모델/RMSE기준)이며 음수는 기준보다 악화다. MAE·실제 비영 이동에서의 balanced accuracy·무변화 수·예측 이동 중앙값도 기록한다. 방향 정확도에서 무변화 제외 분모를 CSV에 공개했다. 거래 승률이 아니다.

추론은 사전 정한 보조 anchor H=1/2/5/10/30/60/120/180초에만 수행했다. block 길이는 max(60초, 2×max(W,H)), 1,000회 centered bootstrap으로 **동결 drift 대비 MSE 개선**을 검사한다. 10개 미만 block은 추론을 보류한다. 자료·거래소·종목·지표·W·8개 H 전체 family를 BH 보정했다. bootstrap p 해상도는 1/1001이며 극소 p의 정밀도를 주장하지 않는다. 1~180초의 모든 예측 성능은 측정하지만 모든 초에 독립적인 유의성 주장을 하지 않는다. 이미 일부 기간을 본 뒤 설계한 연구이므로 BH 통과도 새로운 독립 확인이 아니다.

## 7. 실측 지표 결과: 고정 W=5초

아래는 각 지표의 사용 가능한 거래소×종목별 **RMSE 감소율의 동일 가중 중앙값**이다. 좋은 창을 사후 골라 만든 표가 아니다. 서로 다른 경로의 손익을 합산한 값이나 pooled RMSE가 아니다. 미측정 경로는 평균에 0으로 넣지 않으며 coverage·unavailable에서 확인할 수 있다.

### 운영 데이터

{tables['production']}

고정 W=5초에서 운영 OFI의 H=5/30/180초 RMSE 감소 중앙값은 각각 **{fixed('production','ofi',5)} / {fixed('production','ofi',30)} / {fixed('production','ofi',180)}%**다. 같은 조건의 OFI+체결은 **{fixed('production','ofi_trade_joint',5)} / {fixed('production','ofi_trade_joint',30)} / {fixed('production','ofi_trade_joint',180)}%**다. 조합 지표는 체결 방향을 사용할 수 있는 경로만 포함하므로 전체 OFI 중앙값과 완전히 같은 구성은 아니다. 따라서 결합 효과의 엄밀한 비교는 CSV에서 같은 거래소·종목·W/H를 짝지어 해야 한다.

![운영 데이터: 계산 창과 미래 시간축](production_window_horizon.png)

동일 W=5초의 **비영 가격 이동에서의 방향 balanced accuracy(%)**는 다음과 같다. 각 경로의 상승·하락 recall을 평균한 뒤 경로별 중앙값을 계산했다. 비교 기준은 50%이며 무변화는 제외한다. 방향 정확도가 높더라도 예측폭·비용·체결은 별도다.

{direction_tables['production']}

### 기존 pilot 구간

{tables['pilot_test']}

pilot W=5초 OFI의 H=1/5/30/180초 중앙값은 **{fixed('pilot_test','ofi',1)} / {fixed('pilot_test','ofi',5)} / {fixed('pilot_test','ofi',30)} / {fixed('pilot_test','ofi',180)}%**다. 이미 발생한 5초 가격 충격을 설명한 H=0 감소율 **{raw0:.2f}%**와 큰 차이가 난다. 이 자료에서는 ‘수급과 동시 가격 변화의 관계’보다 ‘그 관계가 미래에도 같은 크기로 지속’하는 가정이 약했다. 장기 H의 악화에는 짧은 학습의 절편·평균 이동을 평가 구간으로 옮기는 문제도 포함되며, drift 대비 열로 추가 정보를 구분한다.

![pilot: 계산 창과 미래 시간축](pilot_test_window_horizon.png)

pilot의 동일 방향 지표(%)도 별도로 제시한다.

{direction_tables['pilot_test']}

### 같은 창의 가격 충격과 미래 예측의 차이

아래 H=0 표는 endpoint 시그널로 이미 발생한 W=5초 가격 변화를 설명한 별도 out-of-sample 진단이다. 미래 예측·진입 수익으로 해석하지 않는다.

{table(d0)}

### 결합 지표가 단독 지표보다 좋아졌는가

아래는 W=5초의 같은 자료·거래소·종목·H·평가 표본 수에서 두 모델을 짝지은 비교다. 양수는 결합 모델의 RMSE 감소율이 단독보다 크다는 뜻이다. 개별 표본 수가 다른 경로는 짝에 넣지 않는다. 차이의 단위는 %p이며 수익률이 아니다. 단순한 두 변수 ridge와 한 개 상호작용을 검사한 결과로 모든 체결·흡수 결합 모델의 가능성을 판정하지 않는다.

{table(pd.DataFrame(joint_rows))}

### 탐색적 상위 셀과 한계

전체 비교를 보고 선택한 다음 표는 후보 발견을 위한 것이다. 적어도 10개 추론 block이 있는 지정 H만 표시했다. 더 긴 H에서 좋은 셀이 있어도 새 구간의 검증 없이 운용 후보로 확정하지 않는다. 모든 나쁜 셀도 원자료 표에 보존했다.

{table(readable_best)}

현재 mid와 동결 drift 둘 다 개선하면서 탐색적 BH q<0.05인 지정-H 셀은 **{len(passing):,}개**다. 같은 모델·같은 데이터의 인접 W/H는 서로 강하게 의존하므로 이를 독립 성공 전략 개수로 세지 않는다. 방향 balanced accuracy가 0.5여도 한 방향으로만 작은 이동을 예측하여 RMSE가 좋아질 수 있으며, 높은 비영 방향 정확도가 작은 예측폭·다수의 무변화 표본과 함께 나타날 수도 있다. 이때 수익성은 별도다.

### 원시 수급 시그널 이후의 가격 반응

학습 구간에서 정한 25/75분위수로 낮은/높은 시그널을 나누고, 평가 구간에서 이후 평균 가격 반응의 차이를 측정했다. 아래는 W=5초이며 각 그룹 30개 이상인 경로의 중앙값이다. 0초의 반응 곡선 원점은 가격 변화 0으로 표시하지만, 이를 예측 점수로 계산하지 않는다. 양수라면 높은 시그널에서 이후 가격 반응이 상대적으로 높았다는 뜻이고, 음수·감쇠는 반전 또는 빠른 반영과 양립한다. 모델이 음의 계수를 학습해 얻은 예측 개선과 ‘양의 수급 압력이 상승을 지속시킨다’는 원래 방향 가설을 구분할 수 있다. 분위수 분류도 인과관계 식별은 아니다.

![원시 지표 이후 0~180초 반응](conditional_response.png)

## 8. 무엇을 확인했고 무엇을 아직 확인하지 못했는가

결측 없는 구간만 고르는 조건은 요청에 맞춘 것이지만, 장애·비정상 호가가 변동성 큰 시간에 더 많다면 표본 선택 편향이 남는다. 이 결과를 모든 시장 상태로 확대하지 않는다. 집계 해상도는 1초라서 10~500ms 반응이나 네트워크 도착 뒤 남는 edge는 이번 grid에서 새로 측정하지 않았다. L2 총깊이와 보충·소진도 앞서 설명한 공개 호가의 관측 범위에 한정된다.

이번 표면은 ‘30초보다 짧거나 길게 보면 달라지는가’에 대한 실측 답이다. 미래 H가 달라지면 계수·오차·방향 성능도 달라진다. 따라서 기존 최대 2.71%만으로 모든 시계·지표·종목의 예측력이 0이라고 선언할 수 없다. 반대로 수십만 비교에서 가장 좋은 숫자 하나만 보고 FV 모델이 유효하다고 선언하지 않는다. 경로 전체 중앙값, 다른 창, drift 기준, block 불확실성 및 보지 않은 다음 구간이 필요하다.

OFI는 본래 같은 구간의 가격 충격을 설명하는 근거가 강하며, 그 영향이 이미 끝났다면 미래 예측은 약하다. OBI 연구의 ‘다음 가격 변화 방향’과 고정 30초 수익률도 같은 목표가 아니다. weighted BBO mid와 full microprice를 구분해야 한다. 본 연구와 주식 시장 연구의 차이를 인정한다. [OFI 원 논문](https://arxiv.org/abs/1011.6402), [Queue imbalance 원 논문](https://arxiv.org/abs/1512.03492), [Stoikov microprice](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694).

OFI와 체결을 함께 보면 소진·흡수 구분을 도울 가능성은 있지만, 양쪽 모두 같은 이미 반영된 정보를 반복하거나 공급의 재유입을 관측하지 못하면 개선이 없다. 기존 무제한 depth 정규화×상호작용 실험의 폭증은 계산 표현의 안정성 문제였고, 이번 분석에서는 W 평균 깊이·asinh·학습 구간 clip을 사용해 그 문제를 분리했다. 이는 바뀐 특징 표현의 탐색 실험이며 기존 전략 성과 개선을 소급 주장하지 않는다.

수익성 검증은 별도 체결 표본이 필요하다. 이번 0체결 pilot의 수수료 후 기대손익, 체결 조건부 역선택, 실제 대기열 위치·취소 효과·mainnet 주문 p95는 미측정이다. FV 오차 개선만으로 maker 체결 후 이익이 생긴다고 가정하지 않는다. 통화·basis·거래소 지연과 가격의 반응 완료 시점도 같이 검증해야 한다.

## 9. 수정 우선순위

1. **실행 수명:** tick/lot 기준 가격·수량 변화 허용 범위와 최소 유지·재평가 조건을 정하고, 타이머를 사건 시각 순서로 처리해 도착 전 취소·활성 구간 누락을 검증한다. 실제 latency를 각 거래소에서 따로 측정하기 전에는 대용값을 실측 mainnet 지연으로 부르지 않는다.
2. **수집·식별:** Arcus native side 보존, GRVT trade selector·현재 체결 수신·과거 snapshot 구분을 검증한다. 이미 사라진 과거 side를 채우지 않는다. Binance pilot의 정상 기준가를 이유 없이 재보정하지 않는다. [GRVT 공식 trade selector](https://api-docs.grvt.io/market_data_streams/).
3. **S2 목표 분리:** 괴리 수렴과 공통 anchor 이동·최근 lead/lag를 나눠 설명하고, USD/USDT·basis 안정성과 도착 뒤 잔여 edge를 검증한다. 비제약 lambda를 기계적으로 0~1로 자르는 방식은 피한다.
4. **S1/S3/S4 목표·안정성:** 동일 종목·동일 H·동일 비용으로 비교하며, 시간/활동량 창의 분포와 깊이 정규화 극단값을 관리한다. 가격 변화 유무·다음 변화 방향·조건부 이동 크기를 구분하는 후보도 별도 확인한다.
5. **새 검증:** 이번 전체 grid를 본 뒤 정한 소수 후보의 규칙·H를 동결하고, 겹치지 않는 새 정상 기간에서 확인한다. 그 뒤 모의 체결 수명을 개선한 원장에서 비용 후 손익을 평가한다.

## 10. 재현 자료

- [전체 지표 성능: 모든 W/H](indicator_metrics.csv)
- [탐색적 상위 셀의 전체 수치](top_exploratory_cells.csv)
- [원래 16개 경로의 운용·예측 수치](original_16_routes.csv)
- [학습 분위수별 미래 반응 곡선](response_curves.csv)
- [결합·단독 지표의 짝지은 비교](matched_combination_comparison.csv)
- [정상 구간·대상 범위](coverage.csv), [미측정 사유](unavailable.csv)
- [결측을 제외한 연속 구간 목록](valid_segments.csv)
- [계산·분할·다중 비교 명세](summary.json), [보고서 집계 수치](report_statistics.json)
- [학습에서 동결한 모델·정규화·계수](calibrated_models.json)
- [원본 catalogue·추출·코드 해시](integrity.json)
- [원래 계산식의 독립 검산](original_formula_audit.json)
- 이전 [30분 pilot 보고서](../fv_pilot_mainnet_20261004/final_30min/REPORT_KO.md), [주문 수명 감사](../fv_pilot_mainnet_20261004/final_30min/ORDER_LIFECYCLE_AUDIT_KO.md), [S2·이전 시계 진단](../fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md)

이번 보고서는 원래 운용 원장과 전략 파일을 변경하지 않는다. 데이터 복원·시간 가중 계산·순번 단절·미래 구간 결측 배제·train-only 정규화의 회귀 시험 결과도 integrity에 기록한다.
'''
    (ROOT/'REPORT_KO.md').write_text(body,encoding='utf-8')
    print(json.dumps({'report':str(ROOT/'REPORT_KO.md'),'pass_cells':len(passing)}))

if __name__=='__main__':main()
