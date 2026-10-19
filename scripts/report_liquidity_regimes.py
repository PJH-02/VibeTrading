"""Merge research partitions; scientific price plots; Korean follow-up report."""
import json,hashlib,re
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path('docs/evidence/fv_regime_20261005')
LABELS={'l1_obi':'L1 OBI','l2_obi':'L2 OBI','ofi':'OFI','trade_imbalance':'Trade imbalance',
        'signed_trade_flow':'Signed trade flow','replenishment':'Visible replenishment','aggressive_weak_refill':'Aggressive flow x weak refill'}
BASE=['l1_obi','l2_obi','ofi','trade_imbalance','replenishment','aggressive_weak_refill']
HS=[5,25,60,180]
def table(d):
    cols=list(d.columns);lines=['| '+' | '.join(map(str,cols))+' |','| '+' | '.join(['---']*len(cols))+' |']
    for row in d.itertuples(index=False,name=None):
        lines.append('| '+' | '.join('미측정' if pd.isna(x) else f'{x:.3f}' if isinstance(x,(float,np.floating)) else str(x) for x in row)+' |')
    return '\n'.join(lines)
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def plotline(ax,s,label,color,style='-'):
    ax.plot(np.r_[0,s.index.to_numpy()],np.r_[0,s.to_numpy()],label=label,color=color,ls=style)
def main():
    frames={}
    for name in ('volatility','price_curves','signal_deciles','signal_strength','onset_curves','event_examples'):
        frames[name]=pd.concat([pd.read_csv(p) for p in (ROOT/'parts').glob('*/'+name+'.csv')],ignore_index=True)
        frames[name].to_csv(ROOT/(name+'.csv'),index=False,encoding='utf-8-sig')
    v=frames['volatility'];p=frames['price_curves'];s=frames['signal_strength'];e=frames['onset_curves']
    inf=pd.read_csv(ROOT/'inference.csv');l1inf=pd.read_csv(ROOT/'l1_h25_inference.csv');l1inf['kind']='l1_quartile_contrast'
    inf=pd.concat([inf,l1inf],ignore_index=True);finite=inf.p_centered.notna();order=inf.loc[finite,'p_centered'].sort_values()
    q=1.
    for rank in range(len(order)-1,-1,-1):q=min(q,order.iloc[rank]*len(order)/(rank+1));inf.loc[order.index[rank],'q_bh_exploratory']=q
    inf.to_csv(ROOT/'all_inference.csv',index=False,encoding='utf-8-sig')
    primary=v[((v.source=='production')&(v.definition=='quantity_buffer_seconds'))|((v.source=='pilot')&(v.definition=='notional_buffer_seconds'))]
    contrast=primary.pivot_table(index=['source','venue','asset','window','horizon'],columns='regime',values='mean_realized_vol_bps')
    contrast['high_low_ratio']=contrast.high/contrast.low
    ratio_table=contrast.groupby(['source','window','horizon']).high_low_ratio.median()
    fig,ax=plt.subplots(figsize=(11,5),layout='constrained')
    for source,color in [('production','#21618c'),('pilot','#d07824')]:
        for w,style in [(5,'-'),(60,'--')]:
            g=ratio_table.loc[source,w];ax.plot(g.index,g.values,color=color,ls=style,label=f'{source}, W={w}s')
    ax.axhline(1,color='gray',lw=1);ax.set(xlabel='Future horizon (s)',ylabel='High-ratio / low-ratio mean realized volatility',
        title='Future volatility: train-fixed liquidity quartiles, active trade windows only')
    ax.legend();fig.savefig(ROOT/'liquidity_future_volatility.png',dpi=160);plt.close(fig)
    z=frames['signal_deciles'];z=z[(z.source=='production')&z.decile.notna()]
    fig,axes=plt.subplots(3,2,figsize=(14,10),layout='constrained')
    for ax,name in zip(axes.flat,BASE):
        g=z[z.indicator==name].groupby(['decile','horizon']).mean_bps.median().unstack().reindex(index=range(1,11),columns=range(1,181))
        im=ax.imshow(g.to_numpy(),origin='lower',aspect='auto',extent=[.5,180.5,.5,10.5],vmin=-1,vmax=1,cmap='RdBu_r')
        ax.set(title=LABELS[name],xlabel='Future seconds',ylabel='Train-fixed signed signal decile');ax.set_yticks([1,5,10])
    fig.colorbar(im,ax=axes.ravel().tolist(),label='Observed mean future mid move (bp), equal-route median\nColors saturate at +/-1 bp; empty tie bins omitted',shrink=.8)
    fig.savefig(ROOT/'signal_strength_price_surface.png',dpi=160);plt.close(fig)
    matched=pd.read_csv(ROOT/'l1_matched_curves.csv');matched['contrast']=matched.high_mean_bps-matched.low_mean_bps
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout='constrained')
    for source,color,style in [('production_full','#21618c','-'),('production_excluding_pilot','#2d9b87','--'),('pilot','#d07824','-')]:
        g=matched[matched.source==source].groupby('horizon').contrast.median();plotline(axes[0],g,source,color,style)
    axes[0].set(title='L1 OBI: matched 12 routes, old 25/75% definition',xlabel='Future seconds',ylabel='High - low mean mid move (bp)');axes[0].legend(fontsize=8)
    for group,color in [('high','#c03c44'),('low','#21618c')]:
        q=p[(p.source=='production')&(p.indicator=='l1_obi')&(p.group==group)]
        plotline(axes[1],q.groupby('horizon').mean_bps.median(),group+' mean',color)
        plotline(axes[1],q.groupby('horizon').median_bps.median(),group+' median',color,'--')
    axes[1].set(title='L1 OBI: raw future move, new 10/90% tails',xlabel='Future seconds',ylabel='Future mid move (bp)');axes[1].legend(fontsize=8)
    for ax in axes:ax.axhline(0,color='gray',lw=.7)
    fig.savefig(ROOT/'l1_actual_price_responses.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(3,2,figsize=(13,10),layout='constrained')
    for ax,name in zip(axes.flat,BASE):
        for source,color in [('production','#21618c'),('pilot','#d07824')]:
            part=e[(e.source==source)&(e.indicator==name)]
            plotline(ax,part.groupby('horizon').mean_bps.median(),source+' mean',color)
            if source=='production':plotline(ax,part.groupby('horizon').median_bps.median(),source+' median',color,'--')
        ax.axhline(0,color='gray',lw=.7);ax.set(title=LABELS[name],xlabel='Seconds since threshold crossing',ylabel='Signal-aligned realized move (bp)');ax.legend(fontsize=7)
    fig.suptitle('Onset event study: 180s cooldown; pilot has few separated events',fontsize=14)
    fig.savefig(ROOT/'onset_actual_moves.png',dpi=160);plt.close(fig)
    eg=frames['event_examples'];eg=eg[(eg.source=='production')&(eg.venue=='bybit')&(eg.asset=='BTC')&(eg.indicator=='l1_obi')]
    fig,ax=plt.subplots(figsize=(11,5),layout='constrained')
    for _,part in eg.groupby('event_t'):ax.plot(np.r_[0,part.horizon],np.r_[0,part.aligned_move_bps],color='#8f9ba4',alpha=.45,lw=.8)
    part=e[(e.source=='production')&(e.venue=='bybit')&(e.asset=='BTC')&(e.indicator=='l1_obi')]
    ax.plot(np.r_[0,part.horizon],np.r_[0,part.mean_bps],color='#21618c',label='All events: mean',lw=2)
    ax.plot(np.r_[0,part.horizon],np.r_[0,part.median_bps],color='#237e64',label='All events: median',lw=2)
    ax.axhline(0,color='gray',lw=.7);ax.set(title='Bybit BTC L1 OBI: 12 events selected by event index, never by future result',xlabel='Seconds after onset',ylabel='Signal-aligned actual mid move (bp)');ax.legend()
    fig.savefig(ROOT/'individual_event_paths.png',dpi=160);plt.close(fig)

    vol_table=ratio_table.loc[(slice(None),[5,60],HS)].reset_index()
    control=pd.read_csv(ROOT/'past_vol_matched.csv')
    controlled_routes=control[control.source=='production'].groupby(['venue','asset','window','horizon']).future_rv_high_low_ratio.median()
    ctrl_table=controlled_routes.groupby(['window','horizon']).median().unstack()
    incremental=pd.read_csv(ROOT/'volatility_incremental.csv');increment_table=incremental.groupby(['source','window','horizon']).vol_mae_reduction_percent.median().unstack()
    direction=pd.read_csv(ROOT/'regime_direction.csv');direction=direction[(direction.source=='production')&(direction.strength_bucket=='all')&(direction.window==5)&direction.horizon.isin([5,25,60])]
    dt=direction.groupby(['indicator','regime','horizon'])[['mean_aligned_bps','mean_aligned_over_future_rv','balanced_accuracy_moving']].median().reset_index()
    dt=dt[dt.regime.isin(['high','low'])];dt['balanced_accuracy_moving']*=100
    ranges=pd.read_csv(ROOT/'ranges.csv')
    if 'anchor_stride' not in ranges:ranges['anchor_stride']=60;ranges.to_csv(ROOT/'ranges.csv',index=False,encoding='utf-8-sig')
    rt=ranges[(ranges.band=='past_minmax')&(ranges.eligibility=='outer_half_entry')&(ranges.horizon.isin([180,1800]))].groupby(['lookback','regime','horizon'])[['n','median_halfwidth_bps','toward_center_mean_bps','p_center_before_exit','p_exit_before_center','p_neither','median_future_center_shift_halfwidth']].median().reset_index()
    rt=rt[rt.regime.isin(['high','low','all'])]
    nonoverlap=pd.read_csv(ROOT/'range_nonoverlap.csv');nt=nonoverlap[(nonoverlap.band=='past_minmax')&(nonoverlap.eligibility=='outer_half_entry')&(nonoverlap.lookback==900)&(nonoverlap.horizon==1800)].groupby('regime')[['n','toward_center_mean_bps','p_center_before_exit','p_exit_before_center']].median().reset_index()
    st=s[(s.source=='production')&(s.horizon==25)].pivot_table(index=['venue','asset','indicator'],columns='strength_quintile',values='mean_bps')
    st=st.dropna(subset=[1,5])
    st['strong_minus_weak']=st[5]-st[1];strength_table=st.groupby('indicator')[[1,5,'strong_minus_weak']].median().reset_index()
    onset_table=e[(e.source=='production')&e.horizon.isin(HS)].groupby(['indicator','horizon'])[['n','mean_bps','median_bps','p_flat']].median().reset_index()
    overlay=pd.read_csv(ROOT/'l1_overlap_audit.csv');l1_table=matched[matched.horizon.isin(HS)].groupby(['source','horizon']).contrast.median().unstack().reset_index()
    thresholds=pd.read_csv(ROOT/'ratio_thresholds.csv')
    ratio_coverage=thresholds[((thresholds.source=='production')&(thresholds.definition=='quantity_buffer_seconds'))|((thresholds.source=='pilot')&(thresholds.definition=='notional_buffer_seconds'))].groupby(['source','window']).size().rename('available_routes').reset_index()
    l1_ci=inf[(inf.kind=='l1_quartile_contrast')&(inf.source=='production_excluding_pilot')][['venue','asset','mean_high_minus_low','ci_low','ci_high','q_bh_exploratory']]
    def vr(w,h):return float(ratio_table.loc['production',w,h])
    def l1(source,h):return float(matched[(matched.source==source)&(matched.horizon==h)].contrast.median())
    total_stats=sum(len(x) for x in frames.values());methods=json.loads((ROOT/'methods.json').read_text())
    qsig=inf[(inf.q_bh_exploratory<.05)&(inf.kind=='ratio_future_vol')&(inf.mean_high_minus_low<0)]
    summary={'response_rows':total_stats,'inference_tests':int(finite.sum()),'future_vol_negative_q05_cells':len(qsig),
             'production_W5_future5_vol_high_low':vr(5,5),'production_W60_future60_vol_high_low':vr(60,60),
             'shared_l1_correlation_median':float(overlay.obi_correlation.median()),
             'input_and_methods':methods}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    body=f'''# 유동성 국면·범위 평균회귀·수급 신호 이후 실제 가격 이동

분석 기준: 2026-10-05 KST. 기존 2026-10-04 고정 자료를 사용했으며 새 주문이나 모의 체결을 만들지 않았다. 이전 [FV 보고서](../fv_research_20261004/REPORT_KO.md)의 원본과 결과는 변경하지 않았다.

## 1. 네 질문에 대한 판단

1. **횡보 범위와 중심을 이용한 평균회귀는 가능한 전략 가설이다. 하지만 범위 안에 있었다는 사실이 중심을 미래 FV로 만들지는 않는다.** 이번 자료에서 과거 밴드의 중심 복귀보다 밴드 이탈이 먼저 나타나는 경우가 많았다. 높은 유동성 비율만으로 grid 국면을 확정할 근거는 부족했다.
2. **높은 유동성 비율과 낮은 미래 변동성의 관계는 확인됐다.** 운영 W=5초에서 H=5초의 high/low 미래 실현변동성 비율 중앙값은 {vr(5,5):.3f}, W=60초→H=60초는 {vr(60,60):.3f}였다. 그러나 **낮은 비율에서 방향 정확도가 일관되게 더 높지는 않았다.** 변동성 상태 판별과 방향성 edge는 별개의 조건이다.
3. **L1 OBI는 이후 짧은 가격 움직임과 조건부 관계가 있다.** pilot 기간을 뺀 운영 자료에서도 25초 후 high-low 평균 이동 차이는 {l1('production_excluding_pilot',25):.3f}bp였다. 다만 두 곡선의 유사성 자체는 독립 재현의 증거가 아니다. 겹치는 구간의 L1 OBI 상관 중앙값은 {overlay.obi_correlation.median():.6f}였다.
4. **가격 움직임 자체로 평가했고 아래에 제시한다.** 모든 초의 실제 mid 이동, 신호 강도별 평균·중앙값·25/75분위수, 상승/하락/무변화 비율, 신호 시작 후 경로와 유리/불리 이동을 CSV에 공개한다. 1bp=0.01%이며 거래 수익률·체결 승률과 다르다.

## 2. 원글과 이번 검증이 다른 점

사용자가 제시한 [퀀트대디의 개인 분석](https://blog.naver.com/quantdaddy/223200751708)을 확인했다. 원글은 S&P 500 선물의 틱 자료를 일별로 집계하고 20일 이동평균한 공급/수요 비율과 실현변동성의 관계를 설명한다. 이번 자료는 암호자산의 약 22~68시간이고, 1~60초 신호와 이후 1~180초를 본다. **원글의 일·월 단위 결과를 그대로 복제한 검증은 아니다.** 같은 기간의 상관과 미래에 남는 정보도 구분한다.

남아 있는 L1 명목 잔량 `S=b×q_bid+a×q_ask`는 공급의 **현재 stock**이고, 기간 내 체결대금 `T=sum(price×quantity)`는 소비의 **flow**다. 잔량을 호가 메시지마다 다시 더하면 같은 주문을 반복해서 세며 메시지 빈도에 의존한다. 따라서 주 비율은 다음과 같다.

`R_notional(W)=integral_(t-W)^t S(u)du / T(W) = 평균 L1 명목 잔량 / 초당 체결대금`

단위는 **초**이며, 지금 속도로 체결이 계속될 때 표시 잔량이 몇 초 분량인지 나타내는 proxy다. 실제 고갈 시간은 취소·재공급 때문에 이 값과 같지 않다. 무체결은 infinity로 만들어 high로 분류하지 않고 **inactive**로 분리했다.

pilot SQLite에는 실제 체결 가격과 변경 BBO가 있으므로 명목금액 비율을 정확한 수신 시각으로 적분했다. 운영 장기 집계는 가격×수량의 곱을 보존하지 않아 `R_quantity=integral(q_bid+q_ask)du / sum(trade_quantity)`를 사용했다. 이를 원글의 정확한 명목 비율이라고 부르지 않는다. pilot에서 두 정의의 순위 상관은 거의 1이고 상대 차이 중앙값은 W=5초 약 0.0011%, W=60초 약 0.0039%였다. 하지만 다른 시장·시간축에서도 같다는 증거는 아니다.

변경 BBO의 명목 잔량을 이벤트마다 더한 비율도 별도 계산했다. 이는 **모든 native quote tick**의 합과 같은 정의가 아니며 이벤트 수의 영향을 받는다. `volatility.csv`의 `quote_event_stock_sum`으로 시간 적분 비율과 구분한다. 공개 BBO 잔량은 모든 신규 지정가 제출량이나 숨은 유동성도 아니다.

## 3. 자료와 통계 방법

- 운영: Binance/Bybit/Bitget BTC·ETH·SOL 약 2.84일, Arcus/GRVT 약 22시간. 앞 60% 학습·뒤 40% 평가, 180초 label purge와 60초 embargo, 평가 anchor 5초 간격.
- pilot: 원래 10분 학습과 이후 30분 평가를 분리, anchor 1초 간격. XRP는 pilot에만 있다. 운영과 pilot의 일부 시간은 겹친다.
- 호가 정상 조건은 이전 보고서와 같고, **과거 60초~미래 180초 전체가 정상**인 창만 사용한다. 체결 방향이 unknown인 창은 방향 지표에서 제외한다. 총 체결량을 사용하는 비율은 unknown 방향도 포함한다.
- 각 거래소·종목·W별 학습 비율 25/75분위수로 low/high를 동결했다. 미래 변동성은 `sqrt(sum(미래 1초 mid 수익률bp²))`, 가격 반응은 `10000×(mid(t+h)/mid(t)-1)`다. 두 값은 다르다.
- 명시한 H=5/25/30/60/180의 조건부 평균 차이는 길이 max(60,2H)초의 시간 block bootstrap 1,000회로 진단했다. 10개 미만 block 또는 어느 그룹이 30개 미만이면 추론을 보류했다. 표시한 p 전체를 탐색적 BH 보정했다. 연구를 설계하기 전에 같은 자료를 이미 보았으므로 새로운 확증 실험은 아니다.
- 아래 요약은 **사용 가능한 경로 통계의 동일 가중 중앙값**이다. 서로 다른 시장을 독립 표본으로 합산하지 않는다. 커버리지·미측정 사유·그룹별 n을 모두 제공한다. 신호 관계를 인과효과로 판정하지 않는다.

비율 학습과 평가에 충분한 활성 체결 창이 남은 경로 수는 아래와 같다. 운영 W=5초는 12개, pilot W=5초는 11개다. **GRVT는 비율을 학습할 체결 자료가 부족해 비율 국면 결과에 포함되지 않는다.** pilot Arcus XRP도 W에 따라 활성 창이 부족하다. 호가만 필요한 OBI 분석의 커버리지와 구분한다.

{table(ratio_coverage)}

## 4. 유동성 비율은 미래 변동성을 구분하는가

다음 값은 각 경로의 high 그룹 평균 RV / low 그룹 평균 RV를 계산한 후 중앙값을 낸 것이다. 1보다 작으면 high에서 미래 변동성이 낮았다. inactive는 들어가지 않는다.

{table(vol_table)}

![미래 변동성과 유동성 비율](liquidity_future_volatility.png)

위 관계는 단순히 동일 시점의 변동성을 비교한 결과가 아니다. 다만 이미 조용한 구간이 계속 조용해지는 효과를 포함한다. **과거 변동성의 같은 학습 분위수 안에서 비교**하면 운영 자료의 high/low 비율은 다음과 같이 가까워진다. 사용 가능한 과거 RV 분위수의 비율 중앙값을 경로별로 먼저 구한 뒤 경로 중앙값을 냈다.

{table(ctrl_table.reset_index())}

또한 과거 RV와 체결 유량으로 미래 RV를 예측하는 고정 ridge 모형에 log(R)를 추가했다. 학습에서만 표준화·clip·계수를 정했다. 아래는 미래 RV의 **MAE 감소율(%)**이다. 가격 FV의 RMSE가 아니다.

{table(increment_table.reset_index())}

국면 간 변동성 차이가 커도 이미 관측한 변동성·활동량을 넘어서는 추가 예측 개선은 작거나 음수일 수 있다. 비율은 위험 상태를 요약하는 후보지만 그 숫자 하나가 충분한 국면 모델이라는 결론은 아니다. 음의 차이를 보이고 탐색적 q<0.05인 미래 RV 비교 셀은 {len(qsig)}개이며, 인접 W/H를 독립 검증 개수로 세지 않는다.

## 5. 비율이 낮을 때 방향성 betting이 더 유리한가

W=5초의 각 원시 지표 방향에 따라 다음 가격 이동의 부호를 정렬했다. 양수는 시그널 방향으로 움직였다는 뜻이다. `mean_aligned_over_future_rv`는 **관측한 미래 RV로 나눈 사후 진단**이며 거래 시점 입력이나 거래 크기 결정에 쓰지 않았다. balanced accuracy는 실제로 가격이 움직인 표본에서 상승·하락 recall의 평균이다. 무변화는 분모에서 제외한다.

{table(dt)}

낮은 비율에서 OFI의 짧은 평균 이동은 커지는 경우가 있지만, 방향 정확도가 함께 개선되는 것은 아니다. L1/L2 OBI의 비영 방향 정확도는 오히려 high에서 더 높은 경우가 많았다. **낮은 R → 큰 변동성 → 방향을 알 수 있음**은 성립하지 않는다. 부호 없는 비율은 규모 정보를 담을 수 있지만 매수·매도 방향을 직접 주지 않는다.

전체 표에는 신호 절댓값의 학습 강도 분위수를 맞춘 비교도 포함한다(`regime_direction.csv`). 서로 다른 강도의 시그널을 비교한 것과 같은 강도에서의 국면 효과를 구분해야 한다. 평균 bp가 커졌다는 사실만으로 고변동성 구간의 risk-adjusted edge가 커졌다고 말하지 않는다.

공격 체결×약한 refill은 `signed_flow×(1-소진 측 관측 보충률)`로 정의했다. buy일 때 ask, sell일 때 bid를 보며 해당 가격 측 활동이 없으면 약화를 추측하지 않고 제외했다. 0이 아닌 값의 방향은 체결 방향을 유지하므로, 단순히 sign을 사용할 때 체결 불균형과 같은 방향을 갖는다. 활동이 없거나 보충률이 1인 표본의 제외 때문에 평가 집단은 달라질 수 있다. 추가 정보는 **강도 분류와 onset 선택**에서 평가한다. 공개 top10 공통 가격의 순잔량 변화만 사용해 완전한 소진·gross refill을 포착한 것은 아니다.

## 6. 과거 횡보 밴드의 중심을 FV로 볼 수 있는가

센터를 FV로 사용할 조건은 과거 범위 안의 가격 분포가 보였다는 것보다 강하다. 미래에도 중심과 범위가 안정적이어야 하고, 중심에서 벗어난 가격이 되돌아오는 경향이 있어야 하며, 왕복 이동폭이 거래 비용보다 커야 한다. 비대칭·추세·재고 위험이 있으면 범위의 중간은 기대가격이나 경제적 FV와 같지 않다. 평균회귀 거래 연구에서도 되돌림 과정과 비용·손절 제약을 따로 설정한다. [평균회귀와 거래 비용 연구](https://arxiv.org/abs/1411.5062).

**이번 가격 경로 검사:** t 직전 15분 또는 60분의 min/max로 하단·상단을 정하고, 그 중간을 center로 **고정**했다. 이후 가격을 보며 밴드를 다시 맞추지 않았다. 내부에 있고 중심에서 밴드 반폭의 50% 이상 벗어난 `outer_half_entry`에서 중심 복귀와 바깥 밴드 이탈의 순서를 측정했다. R은 60초 창의 학습 threshold다. 이 장기 검사에는 과거 15/60분~미래 30분 전체 정상 경로와 test 경계 이후 lookback 확보를 요구했다.

{table(rt)}

표의 `p_center_before_exit`는 중심을 먼저 친 비율, `p_exit_before_center`는 밴드를 먼저 이탈한 비율이다. 같은 1초 관측에서 둘 다 도달하면 순서를 지어내지 않고 `p_same_second_ambiguous`로 따로 기록한다. 미래 30분 후 center 이동/기존 반폭이 1 이상이면 과거의 FV center도 한 반폭 넘게 이동한 셈이다.

매분 anchor는 30분 경로가 겹치므로 독립 거래 표본이 아니다. **30분 이상 떨어진 anchor**의 결과도 확인했다.

{table(nt)}

high/low의 독립 episode 수가 작으므로 grid 국면 효과를 확정할 수 없다. 밴드 폭·진입 z도 국면별로 달라 비교는 인과효과가 아니다. 다만 높은 R이 ‘중심 회귀를 보장한다’는 단순 가정은 이 자료로 지지되지 않는다.

**±1% 밴드:** 같은 과거 center에 반폭 100bp를 둔 검사에서는 `|z|>=0.5`인 진입 표본이 없었다. 넓은 밴드 안에 30분 동안 머물렀다는 관측은 많지만, 50bp 이상 중심에서 벗어나 돌아오는 기회를 검증하지 못한 것이다. 이 상태에서 고정 ±1%가 유효한 FV 구간이라고 선언하거나 grid 이익을 주장하지 않는다.

Grid는 작은 왕복을 여러 번 잡을 수도 있으므로 중심 완전 복귀가 필수 체결 조건은 아니다. 이 검사는 **밴드 중심을 FV로 놓는 가설의 진단**이며 실제 grid backtest가 아니다. 호가 터치를 체결로 가정하지 않았다. 실제 평가에는 grid 간격·재고 상한·범위 이탈시 MTM·수수료·취소 지연·대기열 체결이 필요하다. 낮은 변동성은 손실 위험뿐 아니라 거래 기회도 줄인다.

## 7. L1 OBI 두 곡선이 25초까지 닮은 이유

원래 그림은 모든 anchor를 학습 분위수로 나눈 **조건부 평균 차이**였다. 독립 신호가 새로 시작한 시점만 고른 그림도 아니고, 한 거래의 수익 경로도 아니었다. 동일 종목이지만 서로 다른 종목 구성·threshold·일부 겹치는 데이터가 사용됐다.

이번에는 Binance와 XRP를 빼고 **동일한 12개 거래소·종목**을 비교했다. 공유 정상 초에서 mid 차이 중앙값은 0bp이고, L1 OBI 상관 중앙값은 {overlay.obi_correlation.median():.6f}였다. 운영 평가 anchor 중 pilot 시간과 겹친 비율은 경로별 중앙값 약 {overlay.fraction_full_test_anchors_overlapping_pilot.median()*100:.2f}%다. 따라서 원래 두 선이 비슷하다는 사실만으로 독립 재현이라고 부를 수 없다.

{table(l1_table)}

pilot과 겹친 시간 및 이후 label까지 제외한 운영 자료에서도 25초 high-low 차이는 {l1('production_excluding_pilot',25):.3f}bp였고, pilot은 {l1('pilot',25):.3f}bp였다. **짧은 이후 움직임과 OBI의 관계가 있다는 증거**로는 읽을 수 있다. 그러나 이 차이는 두 집단 평균의 차이이며 high 신호 매수 한 번에 그만큼 번다는 뜻이 아니다. 개별 경로 CI·q와 무변화 비율을 확인해야 한다.

25초의 경로별 평균 차이와 시간 block 95% 구간은 다음과 같다. 12개 경로 모두 구간의 하단이 양수다. q는 전체 탐색 비교군에서 보정한 값이며, 같은 자료를 이미 관찰한 뒤 만든 분석이므로 새로운 기간의 확증이나 인과검정으로 해석하지 않는다.

{table(l1_ci)}

![L1 실제 이동과 기간 중복 제외](l1_actual_price_responses.png)

## 8. 신호 이후 실제 가격과 신호 강도

각 지표 W=5초의 학습 10/90분위수를 동결하고, 가격 이동을 **매 초 1~180초** 직접 측정했다. 다음 그림은 학습 signed decile별 실제 평균 가격 이동이다. 양·음 부호 모두 원시 가격 변화로 보여주며, discrete 신호는 같은 값이 몰려 일부 decile이 비어 있다. 경로 평균의 중앙값이므로 한 종목의 실제 전형적인 가격 경로와 구분한다.

![강도별 실제 가격 이동](signal_strength_price_surface.png)

절댓값이 큰 신호가 더 큰 방향 이동을 보였는지 보려면 방향을 정렬한 뒤 비교해야 한다. 다음은 H=25초에서 학습 절댓값 강도 하위·상위 20%의 평균 이동이다. 두 강도 bin이 모두 있는 경로만 비교한 값이다. 값이 같은 체결 불균형처럼 강도 수준이 적은 지표는 비어 있는 bin이 있을 수 있다.

{table(strength_table)}

**새 신호 발생(onset):** 시그널이 학습 10/90분위수 밖으로 처음 넘어갈 때만 사건으로 잡고, 매수·매도 양방향 합쳐 180초 cooldown을 적용했다. 지속되는 같은 상태를 수천 개 독립 사건으로 세지 않았다. 아래 mean은 시그널 방향으로 정렬한 평균 이동이며 median과 p_flat도 함께 본다.

{table(onset_table)}

![onset 이후 실제 가격 이동](onset_actual_moves.png)

pilot의 비중첩 onset은 경로별 대체로 7~9건에 불과하다. 이후 180초 결과가 크게 달라질 수 있으며 별도 확증으로 쓰기 부족하다. 운영 onset은 종목·신호별 수백 건이지만 시간 의존성은 남는다. 0초 원점은 가격 변화 0이고 예측 성능 점수로 계산하지 않는다.

예를 들어 운영 L1 OBI onset의 25초 방향 정렬 평균 이동은 경로 중앙값 약 +0.430bp(0.0043%)지만, 가격 이동 중앙값의 경로 중앙값은 +0.083bp다. 5초에는 평균이 +0.210bp이고 중앙값은 0이다. 평균 반응이 있다는 사실과 개별 가격을 정밀하게 맞춘다는 것은 다르다.

![선택 규칙을 고정한 개별 실제 경로](individual_event_paths.png)

회색은 Bybit BTC L1의 사건 순서에 따라 고른 12개 경로다. 좋은 결과를 골라낸 것이 아니다. 두꺼운 선은 그 종목의 전체 사건 평균·중앙값이다. **평균은 양수인데 많은 사건은 움직이지 않거나 반대로 움직일 수 있다.** 이것이 RMSE, 평균 반응, 방향 적중과 실제 수익을 구분해야 하는 이유다.

## 9. 다음 전략 설계에 대한 시사점

이번 결과는 ‘R high면 grid, R low면 방향성’이라는 두 갈래 자동 전환을 확정하지 않는다. R은 변동성 위험 필터 후보이고, 평균회귀 여부와 방향성 edge는 따로 검증해야 한다. 구체적인 후보는 **① 거래 활동이 충분한지, ② center·band가 안정적인지, ③ 되돌림/지속성 중 어느 쪽이 보이는지, ④ 도착 뒤 이동폭이 비용을 넘는지**의 조합이다. 새 시기에 소수의 규칙을 동결해 검증해야 한다.

OFI와 시장 깊이의 동시 가격 영향은 기존 연구와 양립하지만, 이후 예측·체결 수익은 추가 조건이다. [OFI·시장 깊이의 가격 영향 연구](https://arxiv.org/abs/1011.6402). 공개 net refill은 전체 취소·추가를 완전하게 분해하지 못한다. 이번 분석에서는 실제 grid 주문·손익·수익성 검증을 실행하지 않았다.

## 10. 전체 실측과 재현 자료

- [유동성 국면별 매 초 미래 RV·절대 이동](volatility.csv), [비율 threshold](ratio_thresholds.csv), [정확 명목/수량 비율 비교](ratio_quality.csv)
- [과거 변동성을 맞춘 비교](past_vol_matched.csv), [기존 RV·활동량 대비 추가 예측](volatility_incremental.csv)
- [low/high의 방향·강도 통제 비교](regime_direction.csv)
- [동결 밴드 가격 경로 검사](ranges.csv), [비중첩 검사](range_nonoverlap.csv)
- [신호 양끝 집단의 실제 가격](price_curves.csv), [모든 signed decile](signal_deciles.csv), [절댓값 강도](signal_strength.csv)
- [발생 후 경로](onset_curves.csv), [개별 원시 경로 예시](event_examples.csv)
- [L1 동일 경로·중복 제외 비교](l1_matched_curves.csv), [중복 데이터 감사](l1_overlap_audit.csv)
- [block CI·탐색 BH](all_inference.csv), [coverage](coverage.csv), [미측정 사유](unavailable.csv), [명세](methods.json), [집계](summary.json), [원본·코드 검증](integrity.json)

표의 원시 수치 단위는 bp 또는 확률(0~1)이며 방향 BA만 %로 변환했다. 결측 없는 기간만 선택한 조건부 결과라 장애·변동성 큰 시기의 선택 편향이 남을 수 있다. 결과를 전체 시장·다른 자산·일별 시간축으로 일반화하지 않는다.
'''
    (ROOT/'REPORT_KO.md').write_text(body,encoding='utf-8')
    inputs=[Path('docs/evidence/fv_research_20261004/integrity.json'),Path('runtime_data/fv_regime_20261005/notional_audit.json')]
    input_manifest=json.loads(inputs[0].read_text(encoding='utf-8'))
    for x in input_manifest['closed_second_series']:
        path=Path(x['path'])
        if digest(path)!=x['sha256']:raise ValueError('Immutable input changed: '+str(path))
    for x in input_manifest['pilot_raw_inputs']:
        path=Path(x['path'])
        if digest(path)!=x['sha256']:raise ValueError('Pilot source changed')
    scripts=[Path('scripts')/x for x in ('extract_pilot_liquidity_notional.py','analyze_liquidity_regimes.py','evaluate_regime_direction.py','control_liquidity_volatility.py','compare_l1_price_responses.py','check_nonoverlap_ranges.py','report_liquidity_regimes.py','evaluate_fv_indicators.py','fv_research_extract.py')]+[Path('tests/test_liquidity_regimes.py')]
    artifacts=[p for p in ROOT.iterdir() if p.is_file() and p.name!='integrity.json']
    def info(p):return {'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':digest(p)}
    suites=ET.parse(ROOT/'tests.xml').getroot().findall('.//testsuite')
    test_counts={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    if test_counts['tests']<7 or test_counts['failures'] or test_counts['errors']:raise ValueError('Regression tests incomplete or failing')
    proof={'original_input_proof_sha256':digest(inputs[0]),'original_inputs_unchanged':True,'original_pilot_raw_unchanged':True,
           'external_order_submissions':0,'source':[info(p) for p in scripts],'inputs':[info(p) for p in inputs],
           'new_notional_series':[info(p) for p in Path('runtime_data/fv_regime_20261005').glob('*notional.parquet')],
           'artifacts':[info(p) for p in artifacts],'regression_tests':test_counts,
           'blog_capture':info(Path('runtime_data/liquidity_regime_blog.html')),
           'report_links_missing':[x for x in re.findall(r'\]\(([^)]+)\)',body) if not x.startswith('http') and not (ROOT/x).exists() and x!='integrity.json']}
    if proof['report_links_missing']:raise ValueError('Missing report links: '+str(proof['report_links_missing']))
    (ROOT/'integrity.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(ROOT/'REPORT_KO.md'),**summary}))
if __name__=='__main__':main()
