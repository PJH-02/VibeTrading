"""Report completed evidence only. Source ledgers are opened read-only."""
import argparse,json,sqlite3,hashlib,math
from pathlib import Path
from collections import defaultdict
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(str(x) for x in row)+' |\n' for row in rows)


def fmt(x,n=4):return '—' if x is None else f'{x:.{n}f}'


def main(args):
    out=args.output;replay=out/'replay_sealed'
    summary=json.loads((replay/'summary.json').read_text(encoding='utf-8'))
    models=json.loads((replay/'models.json').read_text(encoding='utf-8'))
    server=json.loads((out/'server_final/server_verification.json').read_text(encoding='utf-8'))
    before=json.loads((out/'server_before.json').read_text(encoding='utf-8'))
    after=json.loads((out/'server_post_verify.json').read_text(encoding='utf-8'))
    if server['tests_exit'] or server['benchmark_exit']:raise ValueError('Server verification failed')
    tests=ET.parse(out/'tests.xml').getroot()
    suites=list(tests.iter('testsuite'));test_count=sum(int(s.get('tests',0)) for s in suites)
    if any(int(s.get(k,0)) for s in suites for k in ('failures','errors')):raise ValueError('Local tests failed')
    c=sqlite3.connect((replay/'run.sqlite').resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    marks=[dict(r) for r in c.execute("SELECT o.scenario,CASE WHEN o.role IN ('STATE_QUOTE','STATIC') THEN 'ENTRY' ELSE 'EXIT' END fill_role,m.horizon,m.status,count(*) n,sum(CAST(m.qty AS REAL)) qty,sum(CAST(m.qty AS REAL)*m.markout_bps) numerator,sum(CAST(m.qty AS REAL)*(m.markout_bps-m.fee_bps)) net_numerator FROM maker_markouts m JOIN pilot_orders o ON o.id=m.order_id GROUP BY o.scenario,fill_role,m.horizon,m.status")]
    c.close()
    assert len(marks)==len({(r['scenario'],r['fill_role'],r['horizon'],r['status']) for r in marks})
    assert sum(r['n'] for r in marks if r['horizon']==1)==sum(r['fill_events'] for r in summary['results'])
    observed={(r['scenario'],r['horizon']):r for r in marks if r['status']=='OBSERVED' and r['fill_role']=='ENTRY'}
    exit_observed={(r['scenario'],r['horizon']):r for r in marks if r['status']=='OBSERVED' and r['fill_role']=='EXIT'}
    base=[r for r in summary['results'] if r['case'].startswith('C1.') and '.ablation.' not in r['case']]
    ablation=[r for r in summary['results'] if r['case'].startswith('C1.ablation.')]
    executions={r['scenario']:r for r in summary['execution']}
    def stats(r):
        e=executions.get(r['case'],{'intents':0,'filled_orders':0});n=e['intents'];filled=e['filled_orders'] or 0
        return [r['venue'],r['case'].rsplit('.',1)[-1],r['asset'],n,filled,r['fill_events'],fmt(r['gross_mtm_pnl']),fmt(r['maker_fees']),fmt(r['net_mtm_pnl']),fmt(r['end_inventory'],8)]
    bench=server['benchmark'];num_fills=sum(r['fill_events'] for r in summary['results'])
    train_audit,test_audit=summary['training_audit'],summary['test_audit']
    missing=sum(r['n'] for r in marks if r['status']!='OBSERVED');labels=sum(r['n'] for r in marks)
    text=f'''# 전략·주문 엔진 수정 및 서버 적합성 검토 — 2026-10-07

네 전략을 상태 기반 후보로 수정하고 maker 주문 수명·도착/취소 순서·queue·부분 체결·재고 감축을 함께 고쳤다. **코드와 격리 서버 검증은 완료했지만 신규 전략의 수익성은 확보되지 않았다. 현재2vCPU 서버에16조합을 추가로 상시 운용할 충분한 CPU 여유도 확인하지 못했다.** 생산 collector와 실제 주문 운용은 이번 변경으로 자동 활성화하지 않았다.

근거 문서는 사용자의 첨부 피드백과 기존 실증 보고서다. 첨부의 신규 아이디어는 검증 전 가설로 다루고, 종전 “strategy 폴더에서 전략별 Python 파일 하나” 요구를 유지했다. 기존 원시 데이터와 보고서의 내용을 덮어쓰지 않았다.

## 1. 전략 논리 판정과 실제 수정

| 후보 | 논리 판정 | 이번 구현 | 검증되지 않은 부분 |
|---|---|---|---|
| S1 OFI | 단기 방향/quote skew 후보로 가능. 30초 정확 FV와는 다른 가설 | 가격 변화 포함 OFI, 시간가중 depth 정규화, onset·지속·가속, train q90 | 수수료를 넘는 edge와 onset의 독립적인 예측력 |
| S4 OBI | 가장 근거가 있는 방향 후보. 이전 L1 반응은 계속 연구할 이유 | L1을 주 상태로, L2 top10 보조, strength/neutral gate | 유사 production/pilot graph는 독립 재현이 아님. maker adverse selection 후 수익 |
| S2 cross-venue | local lag와 local shock를 분리하면 검증 가능한 구조 | log basis−causal median, origin, source freshness/skew, ADF+KPSS·drift·비용 gate, 실제 로컬 이익을 요구하는 TP1/TP2 | spread 수렴만으로 로컬 수익을 주장할 수 없음. origin은 휴리스틱이고 동시 hedge는 없음 |
| S3 flow/supply | 공격자 압력과 공급의 소진 순서를 함께 보는 가설로 가능 | 압력→흡수→공격자 소진/공급 소진; buy/sell 대칭 episode, 실제 체결 후 누적 | public L2 net refill proxy와 hidden liquidity 사이 차이; 충분한 독립 episode |
| R·jump·inventory | 방향보다 위험·크기·감축 정책으로 타당 | no-trade R=None, thin/jump gate, 비대칭 maker 감축 | high R이면 grid 중심으로 복귀한다는 근거는 확보되지 않음 |

현재 Direction은 관측 지표를 동결한 규칙으로 분류한다. Magnitude의 FLAT/SMALL/ACTIVE는 **과거 창 RV 상태**다. 미래 변화 크기·확률을 학습한 완성 모델로 포장하지 않았다. 가중치·tick skew·상태 문턱은 연구 초기값이고 수익 최적값이 아니다.

원래 구현의 세부 계산 오류도 구분했다. S2의 “현재 gap에 회귀하여 lambda∈(0,1]만 허용” 대신 basis/origin을 사용한다. S3의 trade sign만 보는 식 대신 episode 이후 압력·refill·가격 진행을 본다. R은 메시지마다 재고량을 중복 합산하지 않고 stock×time/실제 체결 notional로 계산한다. OFI와 L2 net 변화는 각각 정의에 맞춰 계산한다. 구체식은 [전략 명세](../../mm_state/STRATEGY_SPEC.md)에 있다.

## 2. 주문 엔진의 수정

- 기본 정상 진입·청산은 maker. 도착 시 crossing 주문은 PostOnly reject, inside-spread 개선 주문은 허용한다. [Bybit 공식 주문 명세](https://bybit-exchange.github.io/docs/v5/order/create-order).
- heap의 유효 시각으로 arrival/trade/cancel을 처리한다. 이미 지난 arrival을 나중 호가로 재판정하지 않는다. Cancel 요청 이후 effective cancel 전 부분 체결을 허용한다.
- ACTIVE 최소1.5초·TTL15초·reprice2ticks·resize20%로 미세 변화 취소를 줄였다. 방향 반전·위험·entry/reduce 역할 변경은 예외다.
- queue ahead, actual opposite trade quantity, leaves, reduce-only를 사용한다. touch로 fill하지 않고, 같은 계좌의 두 주문에 거래량을 재사용하지 않는다. 관측 depth 밖 queue는 unknown이다.
- maker fees를 실제 체결 notional마다 반영한다. shutdown 잔여 재고는 MTM이며 가짜 청산을 만들지 않는다. emergency taker는 기본 false다.
- 실제 Runner에는5초 ACK timeout/UNKNOWN 예약 보존, reject·cancel rate·불리한 maker markout 회로를 추가했다. maker-only는 즉시 hard stop을 보장하지 않는다.
- WS queue8·호가 side4,096 level·feature/markout/order 보관 상한을 두었다. 실시간 source→local wall clock의3초 지연 또는1초 초과 미래 timestamp는 gap으로 차단해 backlog를 새 데이터로 취급하지 않는다.

이전 GRVT latency를 다른 venue에 대입한 profile은 새 state pilot에서 거부한다. 이번 연구의 entry/cancel은 Arcus100ms, Bybit25ms, Bitget50ms, GRVT75ms이며 모두 **configured sensitivity, 미실측**이다. 최신 testnet/실계좌의 실제 latency라고 주장하지 않는다.

전략 전용 testnet feed에도 full L2/실제 trade price를 전달하고 GRVT trade selector `@50`을 적용했다. public training은 각 거래소의 인증 없는 instrument metadata에서 tick을 가져온다. [GRVT selector 공식 명세](https://api-docs.grvt.io/schemas/ws_trade_feed_selector_v1/). 이번 작업에서 새 공개 수집을 장시간 실행하거나 로그인·주문 제출로 이 수정의 live 연결을 재확인한 것은 아니다.

## 3. 원시 이벤트 재생 결과

이미 탐색했던2026-10-04 자료에서 training10분→evaluation30분 단일 chronological split로 검증했다. 원본은 read-only다. 학습 event {train_audit['events']:,}개, 평가 event {test_audit['events']:,}개를 재생했고, 평가 invalid book {test_audit['invalid_books']:,}개를 gap으로 처리, 과거 trade {test_audit['historical_trades']:,}개를 제외했다. sequence gap {test_audit['sequence_gaps']:,}, duplicate trade {test_audit['duplicate_trades']:,}개다. invalid book 주변은 window/queue/labels를 끊으며30분 전체가 무결측이라는 의미는 아니다.

16단독 경로+동일 Bybit BTC7개 ablation ×3개의 queue/latency 설정=69개의 독립 가상 계좌다. 모든 케이스 합계의 partial 포함 **fill event {num_fills:,}개**는 시뮬레이터 작동의 증거다. 69개 계좌의 수익을 합산해 실제 수익으로 부르지 않는다. 외부 주문 제출은0건이다.

### Conservative1x 단독16경로

금액은 해당 venue의 quote currency(Arcus USD, 나머지 USDT)이며 서로 환산·합산하지 않는다. Filled orders는 주문 수, fill events는 부분 체결을 포함한 기록 수다. fees 전후·final residual inventory를 모두 표시한다.

'''
    text+=table(['Venue','전략','종목','의도','체결 주문','Fill event','Gross MTM','Fees','Net MTM','최종 재고'],[stats(r) for r in base])
    text+='''
0체결 경로는 수익성·alpha를 검정할 수 없다. **GRVT4경로와Arcus S4는 의도 자체가0건이므로 latency나queue로 그0체결을 설명할 수 없다.** S2의 정상성/비용/origin gate, S3의 training 가용성과 전이, 수량 floor·exchange minimum을 먼저 구분한다. 체결이 생긴 경로의 소액 PnL 역시 충분한 independent sample의 수익성 검정은 아니다. 별도 causal eligibility 진단은 [no_intent_diagnosis.json](no_intent_diagnosis.json)에 기록했다.

GRVT BTC의 해당 기간 가격은85,258~85,398USDT, lot0.001BTC, min notional100USDT다.150USDT 상한에서 floor된0.001BTC는 약85USDT로 최소금액을 못 맞추고, 최소금액을 맞추는0.002BTC는 약171USDT로 상한을 넘는다. 위험 크기 축소 뒤0수량으로 내려간 판정2,125회, 최소금액 불충족174회, jump 차단177회였다. **현재150USDT 초기 예산과GRVT BTC는 동시에 만족할 수 없다.** 체결을 만들기 위해 위험 한도를 자동 상향하지 않았다. 실제 이 경로를 운용하려면 예산·종목 선택을 다시 정하고 재검증해야 한다. Arcus S4는 ready training189개가200개 기준에 미달하여 차단됐다. 이0건들을 alpha 부재로 해석하면 안 된다.

### S2 정상성 및 S3 training 가용성

'''
    diagnostic_rows=[]
    for v in ('arcus','bybit','bitget','grvt'):
        s2=models[v+':S2'];d=s2.get('calibration',{}).get('stationarity',{});s3=models[v+':S3']
        diagnostic_rows.append([v,d.get('samples','—'),fmt(d.get('adf_p')),fmt(d.get('kpss_p')),d.get('blocks','—'),s2.get('reason'),s3.get('valid'),s3.get('samples','—')])
    text+=table(['Venue','S2 연속 표본','ADF p','KPSS p','Blocks','S2 모델 상태','S3 valid','S3 ready 표본'],diagnostic_rows)
    text+='''
ADF와 KPSS를 함께 통과해야 S2 신규 score가 발생한다. `valid`은 training 자료 가용성 필드이고 수익성 인증이 아니다. longest contiguous block이200개 미만이면 S2를 차단한다. ACF/PACF/Ljung-Box·Anderson-Darling·MAD·half-life 진단은 [models.json](replay_sealed/models.json)에 기록됐다. 이 결과가 미래 모든 regime의 stationarity를 보장하지 않는다. GRVT/Arcus production 수집기에서의 과거 aggressor 결측과 별도로 이번 pilot 원본의 native trade side를 사용했다.

### 동일 자산 incremental ablation

'''
    rows=[]
    for r in ablation:
        e=executions.get(r['case'],{'intents':0,'filled_orders':0})
        rows.append([r['case'].split('.')[-1],e['intents'],e['filled_orders'] or 0,r['fill_events'],fmt(r['net_mtm_pnl']),fmt(r['max_drawdown']),fmt(r['end_inventory'],6)])
    text+=table(['Profile','Intents','Filled orders','Fill events','Net USDT','Max DD','End BTC'],rows)
    text+='''
static도 flat 신규 bid/ask와 보유 후 maker 감축에 같은 inventory/holding/DD 제한을 적용한다. static이 unlimited two-sided inventory를 운영하는 기준은 아니다. 다른 모듈들의 neutral/guard 때문에 실행 기회가 달라지므로 “모듈 추가가 PnL을 개선했다”와 “거래를 하지 않았다”를 구분한다. 합성 가중치와 기본 폭의 차이도 이 초기 ablation의 한계다.

### Queue와 지연 민감도

'''
    rows=[]
    for v in ('arcus','bybit','bitget','grvt'):
        for label in ('C1','O1','C4'):
            group=[r for r in summary['results'] if r['case'].startswith(label+'.'+v+'.')]
            rows.append([v,label,sum(r['fill_events'] for r in group),fmt(sum(r['net_mtm_pnl'] for r in group)),fmt(max(r['max_drawdown'] for r in group))])
    text+=table(['Venue','설정','Fill events','4종목 Net 합계','단독 최대 DD'],rows)
    text+='''
C1=cancel queue credit 없음/기본 지연, O1=불확실한 proportional cancellation credit/기본 지연, C4=보수 queue/지연4배다. venue별 합계도 별도 가상 계좌의 진단 합계이며 실계좌의 동시 재고·수익 보장이 아니다. 낙관 queue에서만 유리한 결과는 채택 근거가 아니다.

### Entry fill-conditioned maker markout

'''
    mark_rows=[]
    for r in base+ablation:
        order=[]
        for h in (1,5,30):
            x=observed.get((r['case'],h));order.extend([x['n'] if x else 0,fmt(x['numerator']/x['qty'] if x and x['qty'] else None),fmt(x['net_numerator']/x['qty'] if x and x['qty'] else None)])
        if any(order[j] for j in (0,3,6)):mark_rows.append([r['case'],*order])
    text+=table(['Case','N1s','MO1bp','Fee後1bp','N5s','MO5bp','Fee後5bp','N30s','MO30bp','Fee後30bp'],mark_rows)
    text+='\n이 표와 그래프는 STATE_QUOTE/STATIC **신규 진입 체결만** 사용한다. 재고 감축 주문을 방향 예측 성과에 섞지 않는다. Exit 주문의1초/30초 maker markout은 다음과 같다.\n\n'
    exit_rows=[]
    for r in base+ablation:
        one,thirty=exit_observed.get((r['case'],1)),exit_observed.get((r['case'],30))
        if one or thirty:exit_rows.append([r['case'],one['n'] if one else 0,fmt(one['net_numerator']/one['qty'] if one and one['qty'] else None),fmt(thirty['net_numerator']/thirty['qty'] if thirty and thirty['qty'] else None)])
    text+=table(['Case','N exit1s','해당 fill fee後1bp','해당 fill fee後30bp'],exit_rows)
    text+='\nBitget S3의30초 진입 markout은 gross+2.31bp/entry fee 후+0.31bp였지만9개 partial 포함 기록뿐이고, maker 왕복4bp보다 작다. 같은 경로의 실제 maker exit까지 포함한 Net MTM은 손실이다. 일부 horizon의 양의 반응과 운용 수익성을 구분해야 한다. 현재 flat-only 신규 진입 제한 때문에 inventory ablation은 주로 exit 정책을 바꾸며 volatility와 entry markout 곡선이 동일할 수 있다.\n'
    text+=f'''
表示값은 각 case/horizon의 **fill quantity 가중 평균**이다. N은 label 기록 수이며 독립 주문/episode 수가 아니다. entry fee 후 markout은 exit fee·funding·왕복 realized PnL을 포함하지 않는다. 전체 horizon labels {labels:,}개 중 missing/censored {missing:,}개({missing/labels*100 if labels else 0:.2f}%)는 미래 가격으로 보완하지 않았다.100/250/500ms·2/10초 등 전체 결과와 사유는 [SQLite](replay_sealed/run.sqlite), [markouts.csv](replay_sealed/markouts.csv)에 있다.

![Bybit BTC ablation maker markout](figures/ablation_markouts.png)

기존 RMSE 결과가 좋아졌다고 주장하는 검증은 하지 않았다. 새 목표는 상태·체결 조건부 가격 움직임·수수료 후 수익이다. 더 짧거나 긴 horizon에서 반응이 바뀌는지 볼 수 있지만, 선택 후 최고값만 채택하지 않는다. 새로운 날짜의 purged walk-forward·onset/episode 단위 CI/FDR가 필요하다. 이번69케이스는 단일 분할·이미 본 데이터여서 p-value/Sharpe 우월성 결론을 내리지 않았다.

## 4. 서버 컴퓨팅 적합성

대상은 기존 서버49.50.141.106이다. CPU{after['cpus']}, RAM{after['memory']['MemTotal']/2**30:.2f}GiB, available{after['memory']['MemAvailable']/2**30:.2f}GiB였다. before load(1/5/15분)={before['load']}; after 시각{after['time_utc']} load={after['load']}. load는 CPU utilization 자체가 아니지만2vCPU에 비해 높고 CPU/IO 경쟁을 확인해야 한다. swap 사용도 남아 있다. Read-only [/proc/docker/NATS 최종 자료](server_post_verify.json)에 수집기별 상태가 있다. 순간 측정으로 기존 생산 상태가 계속 정상이라고 단정하지 않는다.

최종 소스를 `{server['remote_source']}`에 격리 복사해 network none, CPU.25, memory256MiB, read-only source로 Linux 테스트{test_count}개를 통과시켰다. 실제 계좌·생산 volume·새 service는 연결하지 않았다. 로컬도{test_count}개 통과했다. [테스트 XML](tests.xml), [서버 결과](server_final/server_verification.json).

| 합성 계산·ledger benchmark | 실측 |
|---|---:|
| Events / routes | {bench['events']:,} / {bench['routes']} |
| CPU seconds / wall seconds | {bench['cpu_seconds']:.3f} / {bench['wall_seconds']:.3f} |
| Events per CPU second | {bench['events_per_cpu_second']:.1f} |
| .25CPU quota에서 wall events/s | {bench['events_per_wall_second']:.1f} |
| event p50 / p99 | {bench['event_p50_us']/1000:.3f} / {bench['event_p99_us']/1000:.3f}ms |
| Peak RSS | {bench['peak_rss_bytes']/2**20:.2f}MiB |
| Fill events / 최대 pending markouts | {bench['fill_events']:,} / {bench['peak_markout_pending']:,} |
| Feature buckets 최대 / 실제 외부 주문 | {bench['max_feature_buckets']} / 0 |

JSON parsing,50level L2,16route 공유 feature+4모듈,10Hz quote policy, lifecycle,1,216회 synthetic fills 및 SQLite/markout 부하를 포함한다. websocket/TLS·전체 native depth·production NATS/storage·raw-frame 지속 기록·실제 주문 API latency를 포함한 end-to-end benchmark는 아니다. 높은 p99에는 .25CPU cgroup scheduling이 포함된다.

이 엔진을 평균750events/s 정도로 처리한다면 계산만 약750/{bench['events_per_cpu_second']:.0f}={750/bench['events_per_cpu_second']:.2f}CPU가 필요하다는 **선형 추정**이다. 이 값은 과거 수집기 전체 약752events/s를 전략 엔진 입력으로 그대로 가져온 가정이며 실제 feed별 event 비율이나 burst와 다르다. 최근 whole-collector 관측과 .25CPU benchmark를 동일 capacity로 비교해 운영 승인을 만들지 않는다.

**현재 판정:** 계산 방식은 저비용 bounded 상태 엔진으로 서버 환경에 맞췄으나, 현재 공유 서버의 CPU 여유까지 확보된 것은 아니다. 모든16개를 별도 websocket·raw journaling으로 추가하고 heavy fitting/replay까지 같은 서버에 실행하는 것은 권하지 않는다. EDA/replay/fit은 로컬에서, 실제 shadow는 CPU 여유가 확보된 뒤 적은 경로로 lag/RSS/NATS backlog를 먼저 확인해야 한다. 기존 collector 자료 공유 또는 CPU 증설 후 burst 포함 end-to-end 측정이 필요하다. RAM만 늘리면 해결된다는 결론은 아니다.

## 5. 파일과 운용 상태

명세: [STRATEGY_SPEC](../../mm_state/STRATEGY_SPEC.md), [EXECUTION_SIM_SPEC](../../mm_state/EXECUTION_SIM_SPEC.md), [STATE_MACHINE_SPEC](../../mm_state/STATE_MACHINE_SPEC.md), [EDA_PLAN](../../mm_state/EDA_PLAN.md), [BACKTEST_PROTOCOL](../../mm_state/BACKTEST_PROTOCOL.md), [RISK_CONTROLS](../../mm_state/RISK_CONTROLS.md).

전략은 `strategy/s1_net_liquidity.py`, `s2_external_anchor.py`, `s3_aggressive_flow.py`, `s4_obi.py`; 공통 state/policy/calibration/lifecycle/analytics/risk는 `vibetrading/mm_state/`; paper engine은 `vibetrading/fv_pilot/simulation.py`; testnet Runner는 `vibetrading/fv_mm/runner.py`다. 새 run 명령과 fresh training/latency profile 조건은 BACKTEST_PROTOCOL에 기록했다.

서버에는 검토 소스만 staged 상태다. 실행용5개 CLI·설정·dependencies 목록·6개 명세까지 추가 복사했고 network-none 환경에서5개 CLI의 import/--help가 성공했다. [완성된 검토 bundle](server_final/operational_bundle.json). `/root/vibetrading`의 생산 서비스·collector를 교체하지 않았고 실제 주문 제출 및 신규30분 운용은0회다. maker emergency는 꺼져 있다. 이번 historical 모델은 fresh operational 모델이 아니어서 모델 age gate가 다음 실제 운용에서 재학습을 요구한다.

추가로 미확인인 것: 신규 날짜 수익·방향/규모 확률 calibration, account-specific mainnet fees, 각 venue 최신 실제 latency, 실제 queue 위치/hidden liquidity, funding/USD-USDT 환산, 새로운 live feed long-run·대표 burst 성능. Grid 전환 가설은 이전 실증에서 지지되지 않아 활성 전략으로 추가하지 않았다.

원시 파일 SHA와 최종 source/report artifact SHA는 [integrity.json](integrity.json)에 기록한다. 이전 FV 수식은 legacy copy로 보존했고 이번 authorized revision의 runtime SHA는 이전 runtime SHA와 달라진다. 중간 replay는 최종 판정에 사용하지 않으며 `replay_sealed`만 기준이다.
'''
    figures=out/'figures';figures.mkdir(exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(9,4.5))
    for r in ablation:
        xy=[(h,observed[r['case'],h]) for h in (.1,.25,.5,1,2,5,10,30) if (r['case'],h) in observed]
        if xy:ax.plot([h for h,x in xy],[x['net_numerator']/x['qty'] for h,x in xy],marker='o',linestyle='--' if r['case'].endswith('inventory') else '-',label=r['case'].split('.')[-1])
    ax.axhline(0,color='#555',lw=.8);ax.set_xscale('log');ax.set_xlabel('Horizon (seconds)');ax.set_ylabel('Entry markout after fee (bp)')
    ax.set_title('Bybit BTC / entry maker markout / configured latency C1');ax.grid(alpha=.2);ax.legend();fig.tight_layout();fig.savefig(figures/'ablation_markouts.png',dpi=170);plt.close(fig)
    text=text.replace('表示값','표시값').replace('](figures/ablation_markouts.png)',']('+str((figures/'ablation_markouts.png').resolve().as_posix())+')')
    (out/'REPORT_KO.md').write_text(text,encoding='utf-8')
    # Seal raw inputs against the pre-revision verified evidence.
    src=ROOT/'runtime_data/fv_pilot/server_evidence/runtime_data'
    raw_expected={'mainnet_training_verified_20261004/run.sqlite':'7e6ec30a29b8ab9b9189a8d7acc761b6666e8f481242a030ac64992b6ca2b3e6',
                  'pilot_30min_20261004/run.sqlite':'f0549e4321b2c640c506de7e4fe8975540ce2282d5a25ac0be063ee10b427c82'}
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for data in iter(lambda:f.read(4*1024*1024),b''):h.update(data)
        return h.hexdigest()
    raw={p:dict(expected=exp,actual=digest(src/p)) for p,exp in raw_expected.items()}
    if any(x['actual']!=x['expected'] for x in raw.values()):raise ValueError('Raw input integrity mismatch')
    source_files=sorted((ROOT/'strategy').glob('*.py'))+sorted((ROOT/'vibetrading/mm_state').glob('*.py'))+sorted((ROOT/'vibetrading/fv_mm').glob('*.py'))+sorted((ROOT/'vibetrading/fv_pilot').glob('*.py'))+sorted((ROOT/'vibetrading/fv_mm/legacy').glob('*.py'))
    source_files += [ROOT/'scripts'/x for x in ('replay_mm_state_revision.py','benchmark_mm_state.py','collect_fv_public.py','collect_fv_mainnet.py','run_fv_pilot.py','run_fv_testnets.py','report_mm_state_revision.py','complete_mm_state_server_review.py','diagnose_mm_state_no_intents.py')]
    source_files += [ROOT/'README.md',ROOT/'config/fv_mm_testnets.json',ROOT/'config/mm_state_latency_scenarios.json',ROOT/'requirements-mm-state-research.txt']
    hashes={p.relative_to(ROOT).as_posix():digest(p) for p in source_files}
    artifact_files=[out/'REPORT_KO.md',out/'tests.xml',out/'server_before.json',out/'server_after.json',out/'server_post_verify.json',out/'server_final/server_verification.json',out/'server_final/operational_bundle.json',out/'no_intent_diagnosis.json',figures/'ablation_markouts.png']
    artifact_files+=list((ROOT/'docs/mm_state').glob('*.md'))+[replay/x for x in ('summary.json','models.json','same_asset_models.json','run.sqlite','markouts.csv')]
    evidence={'schema':'mm-state-revision-integrity-v1','raw_inputs':raw,'source_files':hashes,'source_manifest_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),
              'server_review_source_sha256':server['source_sha256'],'artifacts':{p.relative_to(ROOT).as_posix():digest(p) for p in artifact_files},
              'local_tests':test_count,'external_order_submissions':0,'production_services_modified':False,
              'limitations':'replay is previously seen historical single-fold data; live source freshness/WS limits are adapter safeguards and are not exercised by an offline replay'}
    feedback=Path('C:/Users/박제형/Downloads/crypto_mm_strategy_revision_context_for_codex.md')
    evidence['user_feedback_sha256']=digest(feedback)
    (out/'integrity.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str((out/'REPORT_KO.md').resolve()),tests=test_count,replay_fill_events=num_fills,raw_inputs_unchanged=True)))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'docs/evidence/mm_state_revision_20261007');main(p.parse_args())
