"""Build one retrospective catalogue from immutable strategy/evidence records."""
from __future__ import annotations
import csv,hashlib,json,re,sqlite3,sys
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/all_strategies_20261012'
PDF=ROOT/'output/pdf/crypto_strategy_complete_report_20261012.pdf'
E=ROOT/'docs/evidence'
VENUES=('arcus','bybit','bitget','grvt')
SID={'S1':'순유동성 OFI','S2':'외부 기준가격','S3':'공격 체결 흐름','S4':'L1 OBI'}
ASSET={'S1':'BTC','S2':'ETH','S3':'SOL','S4':'XRP'}
HS=(1,5,10,30,60,120,180)
SOURCES={
 'L01':('v1 구현·운영 명세','docs/LIQUIDITY_MM_BYBIT_TESTNET.md'),
 'L02':('v1 setup·shadow·지연 replay 근거','docs/evidence/liquidity-mm-setup-20261003.json'),
 'V04':('v4 공통 실행 명세','docs/FV_MM_V4_TESTNETS.md'),
 'T01':('조기 종료된 실제 테스트넷 점검','docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json'),
 'T02':('최종 30분 실제 테스트넷 결과','docs/evidence/fv_mm_v4_20261004/final_30min/results.json'),
 'P01':('최초 16경로 public pilot 결과','docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json'),
 'P02':('원형 pilot 주문 수명 감사','docs/evidence/fv_pilot_mainnet_20261004/final_30min/order_lifecycle_audit.json'),
 'D01':('시계·복합 입력·S2 계수 분해 진단','docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/REPORT_KO.md'),
 'D02':('동일 BTC 초기 3모델·4시계 비교','docs/evidence/fv_pilot_mainnet_20261004/prediction_diagnosis/combination_metrics.csv'),
 'F01':('기초 지표 연구 1차 보고서','docs/evidence/fv_research_20261004/REPORT_KO_1st.md'),
 'F02':('기초 지표 전체 성능','docs/evidence/fv_research_20261004/indicator_metrics.csv'),
 'F03':('원형 공식 독립 검산','docs/evidence/fv_research_20261004/original_formula_audit.json'),
 'R01':('국면·grid·실제 가격 경로 2차 보고서','docs/evidence/fv_regime_20261005/REPORT_KO_2nd.md'),
 'R02':('국면별 방향 반응 전체 수치','docs/evidence/fv_regime_20261005/regime_direction.csv'),
 'R03':('onset 가격 반응 전체 수치','docs/evidence/fv_regime_20261005/onset_curves.csv'),
 'M01':('상태 기반 개정·성과·서버 검토','docs/evidence/mm_state_revision_20261007/REPORT_KO.md'),
 'M02':('최종 개정 69케이스 재생 결과','docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json'),
 'M03':('최종 개정 maker fill·markout 원장','docs/evidence/mm_state_revision_20261007/replay_sealed/run.sqlite'),
 'M04':('주문 의도 0건의 원인 진단','docs/evidence/mm_state_revision_20261007/no_intent_diagnosis.json'),
 'M05':('상태 전략 및 공통 feature 명세','docs/mm_state/STRATEGY_SPEC.md'),
 'M06':('서버 격리 검증','docs/evidence/mm_state_revision_20261007/server_final/server_verification.json'),
 'M07':('운영 위험·수신 지연 제한','docs/mm_state/RISK_CONTROLS.md'),
 'C01':('원형 OFI 전략 코드','vibetrading/fv_mm/legacy/s1_net_liquidity.py'),
 'C02':('v1 flow 모델 코드','vibetrading/liquidity_mm/strategy.py'),
 'C03':('상태 기반 OFI 코드','strategy/s1_net_liquidity.py'),
 'C04':('상태 기반 basis 코드','strategy/s2_external_anchor.py'),
 'C05':('상태 기반 flow 코드','strategy/s3_aggressive_flow.py'),
 'C06':('상태 기반 OBI 코드','strategy/s4_obi.py'),
 'C07':('7개 통합 비교 정책','vibetrading/mm_state/coordinator.py'),
 'C08':('Turtle 프로토타입','strategies/turtle_breakout.py'),
 'C09':('단일 캔들 돌파 예제','strategies/example_signal.py')}
SOURCES.update({'C10':('원형 anchor 코드','vibetrading/fv_mm/legacy/s2_external_anchor.py'),
                'C11':('원형 공격체결 코드','vibetrading/fv_mm/legacy/s3_aggressive_flow.py'),
                'C12':('원형 OBI 코드','vibetrading/fv_mm/legacy/s4_obi.py')})
SOURCES.update({
 'I01':('상태 개정 초기 소스 스냅샷','docs/evidence/mm_state_revision_20261007/server_review/084ae2bf7e402373.tar.gz'),
 'I02':('통합 생성자 수정 전 소스 스냅샷','docs/evidence/mm_state_revision_20261007/server_review/646c9f6a48f4a3bf.tar.gz'),
 'I03':('최종 상태 개정 소스 스냅샷','docs/evidence/mm_state_revision_20261007/server_final/692487af63298f4a.tar.gz'),
 'I04':('중간 v2 재생 결과 - 최종 결과와 구분','docs/evidence/mm_state_revision_20261007/replay_v2/summary.json'),
 'I05':('첫 재생 원장 - 완성된 성과 없음','docs/evidence/mm_state_revision_20261007/replay/run.sqlite'),
 'I06':('중간 final 재생 원장 - 최종 sealed와 구분','docs/evidence/mm_state_revision_20261007/replay_final/run.sqlite')})


def read_json(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))
def f(x,n=3):return '미측정' if x is None or pd.isna(x) else f'{float(x):.{n}f}'
def tab(headers,rows):return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(str(x).replace('|','/') for x in r)+' |\n' for r in rows)+'\n'
def refs(keys):return ', '.join(f'[{k}]({(ROOT/SOURCES[k][1]).as_posix()})' for k in keys)
def clean_text(text):
    from polish_strategy_report import polish
    text=text.replace('\r\n','\n').replace('\u2014','-').replace('\u2013','-').replace('\u2011','-').replace('\u2212','-')
    text=text.replace('MA라이번','MA라 이번').replace('selledgebid','sell episode의 bid').replace('registry.json','catalogue.json')
    text=re.sub(r'(?<=[가-힣])(?=[A-Za-z0-9])',' ',text)
    text=re.sub(r'(?<=[A-Za-z])(?=[가-힣])',' ',text)
    return polish(text)


def recover_specs():
    """Read only this chat's recorded markdown stdout; never copy other outputs."""
    session=next(Path('C:/Users/박제형/.codex/sessions').rglob('*01a100b1*.jsonl'))
    found=[]
    for line in session.open(encoding='utf-8'):
        try:r=json.loads(line)
        except ValueError:continue
        q=r.get('payload',{});item=q.get('item',{})
        if not isinstance(item,dict):continue
        command=str(item.get('command',''))
        if 'Get-Content' not in command:continue
        if not any(s in command for s in ('universal_liquidity_mm_strategy','crypto_mm_strategy_revision_context')):continue
        text=item.get('stdout','')
        if isinstance(text,str) and text:found.append((command,text))
    out=OUT/'source_recovery';out.mkdir(parents=True,exist_ok=True)
    result={}
    for version,title in [('v1','# 잔여 주문흐름·유동성 소진'),('v3','# 단순한 가격 근거를 쓰는 FV'),('v4','# MM 전략 후보별 FV'),('feedback','# Crypto MM Strategy Revision Context')]:
        candidates=[]
        for command,text in found:
            start=text.find(title)
            if start<0:continue
            part=text[start:]
            # The combined v3/v4 original read has both complete documents.
            if version=='v3':part=part.split('# MM 전략 후보별 FV')[0]
            part=re.split(r'\n\s*Path\s*\n|\n\s*Name\s+Source\s*\n',part)[0]
            part=re.split(r'\n(?:vibetrading|tests|scripts|strategy|config|shared)[\\/].+\.py:\d+:',part)[0]
            candidates.append(part)
        if not candidates:raise ValueError('Original strategy capture unavailable: '+version)
        chosen=max(candidates,key=lambda s:len(s)) if version=='feedback' else max(candidates,key=lambda s:s.count('\n## '))
        chosen=chosen.replace('\r\n','\n')
        path=out/(version+'_recorded_original.md');path.write_text(chosen,encoding='utf-8')
        result[version]={'path':path.relative_to(ROOT).as_posix(),'chars':len(chosen),'sections':chosen.count('\n## '),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'provenance':'original attachment text recovered from this chat command stdout; not a byte-identical original file hash'}
        SOURCES[version.upper()]=('대화 기록에서 회수한 '+version+' 원문',path.relative_to(ROOT).as_posix())
    (out/'recovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True);recovered=recover_specs()
    test=read_json('docs/evidence/fv_mm_v4_20261004/final_30min/results.json')
    interrupted=read_json('docs/evidence/fv_mm_v4_20261004/interrupted_execution_check/results.json')
    pilot=read_json('docs/evidence/fv_pilot_mainnet_20261004/final_30min/results.json')
    modern=read_json('docs/evidence/mm_state_revision_20261007/replay_sealed/summary.json')
    model=read_json('docs/evidence/mm_state_revision_20261007/replay_sealed/models.json')
    metrics=pd.read_csv(E/'fv_research_20261004/indicator_metrics.csv')
    combos=pd.read_csv(E/'fv_pilot_mainnet_20261004/prediction_diagnosis/combination_metrics.csv')
    onset=pd.read_csv(E/'fv_regime_20261005/onset_curves.csv')
    direction=pd.read_csv(E/'fv_regime_20261005/regime_direction.csv')
    modern_map={r['case']:r for r in modern['results']}
    audit=read_json('docs/evidence/mm_state_revision_20261007/no_intent_diagnosis.json')
    registry=[]
    def add(identifier,title,family,status,logic,signals,entry,exit,eda,interpretation,limits,sources,extra=''):
        registry.append(dict(id=identifier,title=title,family=family,status=status,logic=logic,signals=signals,entry=entry,exit=exit,eda=eda,interpretation=interpretation,limits=limits,sources=sources,extra=extra))

    # Original execution and inventory proposals are individually registered.
    add('A00','잔여 매수량을 ask depth에 투영하는 최초 가격 아이디어','초기 flow','v1이 교정한 전제/독립 운용 없음',
        '공개 체결에서 남은 공격 buy를 추정하고 반대 ask 누적 depth를 소진한 가격을 FV로 사용하는 초기 생각.',
        '잔여 buy/sell, 표시 depth, flow 지속성. 원문은 arcus_mm_context(1).md를 입력으로 언급하지만 그 파일 자체는 이 대화에서 확보하지 않았다.',
        '도달 예상 가격보다 유리한 maker entry를 하려는 후보이며 별도 동결 문턱은 미확인.',
        '도달 가격의 반대 호가에 exit하는 생각. v1은 가격 touch와 내 queue 전량 체결이 다름을 교정했다.',
        '고유 실제 매매·EDA·PnL 없음. v1 원안 표의 교정 전 생각을 별도로 등록한다.',
        '물량의 조건부 도달 가격, 미래 기대 mid, 내 실행 가능한 청산 가격은 다르다. 원형 직투영을 그대로 수익모델로 승인하지 않았다.',
        '실제 모주문 잔여량·공급 재유입·hidden queue는 public feed로 직접 알 수 없다.', ['V1'])
    add('A01','v1 원안 - 잔여 공격 주문·유효 공급·청산 서비스량','초기 flow','원안/일부 구현',
        '같은 방향의 아직 남은 공격 주문이 반대 호가의 순재공급보다 크고 내 정상 exit queue까지 소모할 수 있을 때만 참여한다. 가격 도달과 내 전량 청산을 별도로 예측한다.',
        'B/S 유량, neutral 배경, episode age별 생존, 가격별 순공급, 소진율, 반대편 흡수·회복. P_struct/P_reach/P_exit의 세 가격을 구분.',
        '30초 지속 episode 및 소비 측 공급 고갈·방어 회복, 보수적 exit 서비스량·경제적 edge·수량·위험을 모두 통과한 한 방향 maker 후보.',
        '승인한 목표의 reduce-only maker; soft invalidation은 entry 취소와 짧은 감축 유예, hard 위험/120초 보유 만료는 제한 IOC 및 대사.',
        '원안은 실제 성과를 제공하지 않은 연구 명세. 구현 준비와 짧은 shadow를 수행했고 원안의49일 walk-forward·정상 청산률 신뢰구간은 확보하지 않았다.',
        '경제적 목적은 내 물량을 청산할 수 있는 잔여 흐름이며, 이미 본 총 체결량을 미래 서비스량으로 재사용하면 과대평가된다. 여러 조건의 AND는 기회 부족과 원인 식별 어려움을 만들 수 있다.',
        '모주문 직접 식별 불가, public L2 순공급은 gross 공급이 아님, survival·공급·fill 모델의 공동 오차. v4·상태 모델과 다른 모델이다.', ['V1','L01','C02'])
    add('A02','v1 구현 - residual-flow-bybit-v1.0','초기 flow','Bybit testnet shadow·진단 replay',
        'v1을 SOLUSDT의 선택적 한 방향 maker로 구체화. EWMA 유량과 이산 hazard로 남은 물량을 projected depth에 통과시킨다.',
        '완료5초 bin6개: 우세 volume>=65%, 우세 bin>=4, 거래 bin>=4, trade ID>=20; 최근10초 우세>=55%. EWMA half-life10/30/120초, Beta(1,1) age5구간 survival.',
        '120초 warmup·5초 판단. bid 또는 bid-1tick / ask 또는 ask+1tick. 최근3개 bin 중2개에서 front 공급/소비<.7; 반대 체결 후10초 회복2회; 방어 depth50% haircut. flow×.75, supply×1.5, uncertainty10%. 33초 지연 이후 exit service와 maker 왕복+2bp latency+2bp model+1tick gate.',
        '정상 target PostOnly; target이 이미 marketable이면 reduce-only IOC. 가장 오래된 fill부터120초, soft2회/10초 유예, hard IOC최대3회·30초 cooldown. 최대3단위0.3SOL/90USDT.',
        '150.06초 shadow: 주문/체결0, warmup6·stale_mark3·stale_book19·no_episode2. 5/10/20초 diagnostic replay 모두 후보0. 관련70 tests 통과, 전체288 pass/2실패는 기존 배포 기대값 불일치.',
        '기록·연결·예약·정리 경로는 확인했지만 수익 평가 표본이 없다. strict gate와 짧은 warmup 이후 관측이 원인 후보이며 alpha 부재 판정은 불가.',
        'entry/cancel/exit5초·private3초는 보수 설정이지 실측 p95가 아니다. equity 변화는 다른 계정 요인일 수 있어 자체 trading PnL로 사용하지 않는다.', ['L01','L02','C02'])
    add('A03','v1 calibrated mode - episode 증거로 거래 승인','초기 flow','구현/적격 증거 없음',
        '같은 가격 규칙을 쓰되 독립 episode의 비용 후 손익과 정상 exit 확률 하한이 양수인 cell만 승인한다.',
        'direction×entry offset×age bucket cell, frozen survival·fee·config hash, out-of-sample 선언과 block bootstrap.',
        'cell별 episode>=100, net_pnl lower95>0, normal_exit lower95>=max(.70,break-even+.05); 적격 evidence file과 fee/config 정합성 필요.',
        'A02와 동일한 예약·maker/위험 IOC exit이며 calibrated hazard는 실행 중 재학습하지 않음.',
        '적격 calibrated 파일과14일 train+7일 calibration+7일 test/4fold 자료를 만들지 않았다. 승인된 calibrated 운용 성과 없음.',
        'gate는 좋은 원칙이지만 필요 표본을 확보하지 않은 상태에서 거래가 차단된 것을 전략 성공·실패로 세지 않는다.',
        '짧은 replay로 evidence file을 만들어 수익성 승인을 받을 수 없다.', ['V1','L01','L02'])
    add('A04','원형100/100 양방향 호가·체결 후 재제출','재고·주문 후보','제안/기본 비활성',
        'bid q와 ask q로 spread 왕복을 반복하고 bid fill 뒤 ask를 늘리고 bid를 다시 넣는 단순 MM 루프.',
        'BBO 및 fill, q/q 주문, 별도 검증된 방향별 fill edge가 원래 루프에는 없다.',
        '평탄 상태 bid100+ask100은 설명용 수량이며 실제 구현의 권장 수량이 아니다. 한쪽 fill 뒤 동일단위 재제출을 가정.',
        '기존 ask100에 ask100을 추가하면 재고100에 sell200이 되어 숏으로 넘어갈 수 있다. reduce-only와 전체 미확정 exit 예약이 필수.',
        '해당 순진한 루프의 별도 실제 운용/왕복 PnL은 확인되지 않는다. v1원문이 위험을 지적해 기본 비활성화했다.',
        '양방향 거래의 경제적 우위와 재고 회수는 자동 보장되지 않는다. 기존 ask가 exit로 바뀌는 의미와 새로운 short entry를 분리해야 한다.',
        'quote touch를 fill로 세거나 buy/sell 잔량을 상쇄하면 손익과 위험이 왜곡된다.', ['V1'])
    add('A05','수정100/100 루프 - 조건부 추가 진입·최대3단위','재고·주문 후보','v1 구현 정책',
        'q0를 늘리지 않고 매 fill 뒤 남은 긍정적 edge와 청산 capacity를 재확인하여 최대3단위까지 쌓는다.',
        '실제 재고, 아직 체결 가능한 entry/exit leaves, 최초 episode deadline, 보수 exit 서비스량.',
        'long q fill 뒤 reduce-only ask 총q와 조건부 bid q; I=3q에서 buy중단. 가격 하락이나 손실 회복만으로 추가하지 않음.',
        'exit 합계<=실제 재고-기존 미확정 exit; 최초 fill의 보유 deadline을 추가 fill로 연장하지 않음. 반전 전에 감축·cooldown.',
        'A02의 shadow·0후보 replay만 있어 추가 진입/다단 재고의 성과는 미측정.',
        '원형 A04의 과청산과 martingale 위험을 줄인 실행 개정이며 새로운 alpha를 만든 것은 아니다.',
        '실제 fill·취소 race의 장기 성과와 capacity는 별도 검증 필요.', ['V1','L01','C02'])
    add('A06','v1 2단계 ladder - 0.5q+0.5q','재고·주문 후보','확장 제안/미실행',
        '한 entry 가격을 두 수준으로 분할해 가격/체결 기회를 비교하되 총 위험 예산을 유지한다.',
        '같은 flow alpha, 두 가격의 queue·체결·잔여 exit capacity.',
        '전체 q0 안에서0.5q0+0.5q0. 최소 lot·동시 예약·같은 public volume 배분 필요. 세부 가격 간격은 미동결.',
        '실제 체결된 합계만 reduce-only로 감축하며 두 entry의 미확정 잔량까지 노출 예약.',
        '원안의 후속 확장으로만 제시; 실제 grid/ladder 운용 및 PnL 없음.',
        '두 가격은 총위험을 늘리지 않는 실행 변형이다. 거래 기회 증가와 우위 증가는 같은 말이 아니다.',
        '가격 간격·예약 race·queue 경쟁의 실측 없음.', ['V1'])
    add('A07','v1 양쪽 fill edge를 별도 승인하는 양방향 MM','재고·주문 후보','선택 확장/미실행',
        '공통 FV가 mid 부근이라는 이유 대신 bid-fill·ask-fill 각각의 비용 후 이익을 요구한다.',
        '방향별 보수 FV, 실제 buy/sell fill-conditioned PnL·error buffer.',
        'b+cost+error<=FV_long와 a-cost-error>=FV_short를 각각 충족하고 양쪽 체결 증거가 있을 때 q/q.',
        '재고 후 반대 주문을 EXIT로 전환하고 수량 제한. atomic변경 불가 시 cancelpending까지 직렬 제어.',
        '별도 양방향 적격 증거/실제 운용 성과 없음. 초기 v1과v4는 한쪽 entry만 사용.',
        '대칭 가격만으로 양쪽 alpha를 승인하는 오류를 피하려는 제안이다.',
        '공통 static ablation과 같은 정책이 아니므로 static PnL을 성과로 대입하지 않음.', ['V1','V3','V04'])
    add('A08','v2 언급 전신 - 방향성·횡보·공급충격 선분류','문서 전신','간접 언급/별도 실험 없음',
        'v3에서 제거한다고 명시한, 국면을 먼저 나누고 각 모듈의 quote를 선택하는 이전 구조.',
        'v3의 비교 설명에서 매수압력·공급부족·반대흐름·호가복귀 및 방향/횡보/충격 분류를 확인.',
        'v2 원문과 고유 문턱은 제공/회수되지 않아 특정 규칙을 재구성하지 않음.',
        'v2 고유 청산 규칙은 미확인. v1·v3 규칙을 임의 전용하지 않음.',
        '독립 코드·운용 원장·성과 없음. v3가 대체한 설계로 등록한다.',
        '출처가 부족한 전신도 목록에서 누락하지 않되 실제 시도한 실행 전략이라고 과장하지 않는다.',
        '단일 v2 원안의 전체 전략 목록은 확인 불가이며 이 한계를 명시한다.', ['V3'])
    add('A09','v1 종목 회전·예약자금 대비 기회 선택','capacity·선택 정책','원안 확장/미운용',
        'raw abs(FV-mid)가 큰 종목 대신 보수적 filled 기대손익을 예약자금과 cycle 시간으로 나눈 기회를 고른다.',
        'Score=P(fill)*lowerBound(E[netPnL|fill])/(reservedNotional*max(E[cycle],10s)); portfolio 및 sizing을 먼저 통과.',
        '하한이 양수인 후보에 새 capacity를 배치. 넓은 spread·큰 tick·빈 book을 큰 alpha로 착각하지 않음.',
        '기존 재고는 다른 종목 score가 높다는 이유로 강제 청산하지 않고 원래 가설·deadline·위험으로 관리.',
        '구현은 첫 SOL 단일 시장이었다. 다종목 rotation/capacity의 실측 성과는 없다.',
        '효율적인 후보 선택은 기존 거래 alpha와 별개다. fill 확률·손익 하한·점유시간이 미측정이면 score도 인증되지 않는다.',
        '여러 alt의 같은 방향 위험을 독립 분산으로 보지 않으며 symbol 확대를 용량 검증으로 대신하지 않는다.', ['V1','L01'])
    add('A10','v1 후속 cross-venue anchor 강화','초기 flow 확장','후속 제안/미실행',
        '잔여-flow 모델에 외부 동일 자산 기준가격을 더해 가격 근거를 강화하려는 후속 확장.',
        'v1 flow/supply와 동등 계약의 reference/basis. 결합 가중치·gate는 동결하지 않았다.',
        '별도 성과가 확인된 뒤에만 확장하며 v1 진입의 필수 AND로 추가하지 않음.',
        '기존 v1 실제 exit capacity/queue를 유지한다는 제안; 고유 청산 성과 없음.',
        'v3/v4의 독립 S2 및 새 state S2는 후속 구현이지만 이 v1 overlay를 실제 운용한 기록은 아니다.',
        '외부 anchor를 읽는 것과 동시에 양 거래소를 헤지하는 것은 다르다.',
        '독립 S2 수치를 결합 overlay의 성과로 복제하지 않는다.', ['V1','V3','V4'])
    add('A11','v1 후속 RWA overlay','원문 기타 확장','언급만/코인 MM 실험 외',
        '원문이 후속 대상으로 제시한 RWA 가격·기준시장 특성을 적용하는 overlay.',
        'RWA 현물/기준시장·거래시간·stale oracle 등은 필요하지만 이 대화의 독립 자료·구현은 없다.',
        '고유 진입 규칙·threshold 미확인. crypto perpetual 규칙을 자동 전용하지 않음.',
        '고유 청산·hedge·휴장 정책 미확인.',
        '원문 확장 항목만 확인; 수행된 RWA EDA·주문·PnL 없음.',
        '문서에서 언급된 확장도 누락하지 않고 코인 실험과 범위를 분리해 등록한다.',
        '이 항목을 시도된 crypto alpha나 성공/실패 건수로 세지 않는다.', ['V1','V3'])
    add('A12','Fee0 본체와3bp 비용·인센티브 분리 시나리오','비용·환경 변형','원안 조건/일부 실제 요율 관측',
        'points/rebate 없이 자체 trading edge를 먼저 요구하고 비용이 있는 환경의 순익을 따로 평가한다.',
        '동일 alpha·maker/taker 실제 fee·funding·points/rebate 별도 장부. Fee0은 당시 연구 시나리오이며 모든 venue 현재 fee의 주장 아님.',
        '원래 flow gate에 비용을 반영한다. points를 받기 위해 음의 기대 거래를 승인한 구현은 없다.',
        '실제 종료/청산 cost와 인센티브 수입을 따로 계산하는 제안.',
        '원형·개정의 venue별 fees는 기록됐다. 별도 동결3bp subset 또는 인센티브 포함 수익성 시험은 수행되지 않았다.',
        'Arcus의개정기본S1은 기록maker fee0에서도gross손실이었다. 모든 손실을 수수료만으로 설명할 수 없다.',
        '이것은 비용 조건 변형이고 새로운 directional alpha가 아니다. 미래 프로그램의 유지 의무·points 수익도 미확인.', ['V1','V3','M01','M02'])
    add('A13','온라인 stochastic queue simulator 확장','체결 모델 변형','v1 후속 언급/미구현',
        'queue state에 따른 주문/취소/체결 확률을 확률적으로 재생해 실제 fill 불확실성을 표현하는 후속 실행 모델.',
        '가격·시간 우선순위, queue intensity·취소 위치·hidden liquidity. 원문은 해당 확장을 추후 성과 확인 뒤로 미뤘다.',
        '기존 alpha의 entry intent를 받아 실행하는 후보이며 독립 진입 alpha 없음.',
        '기존 실제 재고/exit를 연결해야 하나 고유 확률 모수·live 검증 없음.',
        '실제로 수행한 것은 FIFO/보수 queue 및 비례 cancel credit 민감도다. 그것을 stochastic simulator 완성으로 부르지 않는다.',
        'queue 관측의 확률 모델과 deterministic execution replay를 구분한다.',
        '개별 order ID 없는 L2에서 정확한 앞 취소 위치를 알 수 없다.', ['V1','M01'])
    # v3 documents and v4 implemented descendants are separate entries.
    base_formulas={
     'S1':('순호가 압력의 일부가 미래에도 남는다.','price-aware u/v; OFI=sum(u-v), activity=sum(|u|+|v|)가nu*D에 도달하는 trailing 창; FV=m+beta*spread*OFI/D.'),
     'S2':('정상 basis를 제거한 같은 자산 가격차가 축소된다.','anchor=Binance perp mid*exp(frozen median log(local/ref)); gap=anchor-m; FV=m+lambda*gap,0<lambda<=1.'),
     'S3':('공격 buy/sell의 순물량과 분할 집행이 이후 가격 이동을 남긴다.','실제 aggressor buy-sell; 총 체결량nu*D의 trailing 창. FV=m+beta*spread*(buy-sell)/D. unknown 방향 제외.'),
     'S4':('best queue 비대칭이 다음 소진/가격 방향에 정보를 갖는다.','OBI=(Qb-Qa)/(Qb+Qa); FV=m+beta*spread*OBI. 기본은 현재 L1 잔량.')}
    common_old_entry='FV 유효·|FV-mid|>C. h=max(spread/2,C); bid=min(bestbid,floor_tick(FV-h)), ask=max(bestask,ceil_tick(FV+h)). 단독 구현에서는 예측 방향 entry 한쪽만; lot/depth/equity/예약 한도 적용.'
    common_old_exit='long은현재FV의ask,short은bid의 reduce-only. 모델 무효·손실·120초·shutdown 유예 이후 marketable risk감축. maker 중심이지만 IOC 가능; 원형 비용 예산은maker entry+taker reduction+tick+학습 adverse error q90.'
    for index,sid in enumerate(SID,1):
        logic,signal=base_formulas[sid]
        add('B0'+str(index),'v3 '+SID[sid]+' 작은 FV 가격식','v3 문서','명세/독립 실행 없음',logic,signal,
            common_old_entry+' v3는 후보를 AND/투표로 결합하지 않으며 가격 공급기와 공통 엔진을 분리한다.',
            common_old_exit+' 원안에서는 양쪽 fill edge가 검증된 경우 q/q 및재고 J에 따른q_b=q+max(-J,0),q_a=q+max(J,0)도 허용.',
            'v3만을 별도 계수로 운용한 결과는 없다. 구현 수식 기준은 후보별 재구성된v4를 선택했다. v4결과는 후손 증거로만 참조.',
            'v1의 잔여 미래물량·다중 gate를 작은 한 변수 가격식으로 줄인 개정. 그 단순화가 성과 개선을 입증한 것은 아니다.',
            'v3와v4가 비슷해도 문서 버전·정책 차이를 보존한다. 별도 PnL을 복제하지 않는다.', ['V3','V4','V04'])
    add('B05','v3 과거 가격 anchor 복원 후보','v3 문서','비교 후 독립 코어에서 제외',
        '일시적 압력이 끝나면 과거 가격으로 되돌아온다는 평균회귀 가정.',
        '과거 anchor와 현재 mid 차이. anchor의 현재 유효성을 독립적으로 확인해야 함.',
        '유효한 과거 가치 기준과 비용을 넘는 괴리가 있을 때의 후보이며 구체 lookback/threshold는 당시 미동결.',
        'anchor 회복 또는근거무효/손실·보유 한도; 구체 주문 정책은 미동결.',
        'v3는 과거 가격의 FV 정당성이 부족해 독립 코어에서 제외. 후속 grid 가격경로 연구와 별개로 실제 체결 실험 없음.',
        '평평한 과거 mid나OFI0은 앞으로 같은 가격대가 유지된다는 증거가 아니다.',
        '후속15/60분 band 검사 결과를v3실제매매성과로 대체하지 않음.', ['V3','R01'])
    add('B06','LP/MM 호가 재가격화 후보','v3 문서','메커니즘 비교/OFI에 포함',
        '공급자의 추가·철수·price improvement가 새로운 예약가격을 나타낼 수 있다.',
        'bid/ask 순공급·가격 개선·철수. v3는 이를price-aware OFI에 이미 포함한다고 명시.',
        '별도 alpha 점수로 다시 더하지 않는다. 독립 매매 문턱 미정.',
        '독립 exit 정책 없음;OFI 후손과 공통 엔진에서만 사용.',
        '독립 재가격화 전략의 실측 PnL 없음.',
        '관측된 재가격화가 현재 가격에 이미 반영됐는지를 구분해야 한다. OFI와 중복 계수로 집계하면 입력의 독립성이 깨진다.',
        '공급자 identity·inventory·의도를public L2로 직접 식별하지 못함.', ['V3'])
    def old_table(sid):
        rows=[]
        for v in VENUES:
            key=v+':'+sid;p=pilot['performance'][key];h=pilot['hypotheses'][key]['30'];m=p['model']
            imp=100*(1-h['forecast_rmse']/h['no_change_rmse']) if h.get('forecast_rmse') is not None and h.get('no_change_rmse') else None
            rows.append([v,ASSET[sid],f(m.get('coefficient'),4),m['valid'],p['order_intents'],p['hypothetical_fills'],f(imp),f(h.get('q_bh_exploratory'),4)])
        return tab(['Venue','종목','계수','FV valid','의도','Fill','30s RMSE감소%','q'],rows)
    diagnoses={
      'S1':'Bybit의사전 beta=-1.934와평가 raw slope=+22.291은부호가 달랐다. nu=1 창에서|OFI/D|<=1이므로 x가spread내로 제한된다. 동시가격충격을 미래30초 지속으로 옮긴 가정과 짧은 계수 보정의 안정성이 약했다.',
      'S2':'Arcus/Bybit/Bitget의lambda=-.291/1.112/1.744로차단. 사후분해에서gap수렴성분은+.512/+.750/+.977이나anchor이동성분이-.803/+.362/+.767이었다. 전체target이동계수를순수수렴율로해석한 목표 불일치다. Binance누락0건으로수집중단은원인이아니다.',
      'S3':'Bybit mainnet30s RMSE감소2.71%,q=.0506. Arcus beta=-4.013→평가slope+2.797,Bitget+2.449→-3.129로부호이동. episode미래지속과반대공급흡수를trade량만으로구분하지못했다.',
      'S4':'Bybit testnet사전beta=-1.374와평가30s slope+1.386의부호반대. mainnetBybit30s RMSE+1.52%,q=.0506. fixed계수의추가as-of시계검사에서5s+4.12%,비영BA65.7%지만forecast중앙폭.187bp로비용보다작았다.'}
    for index,sid in enumerate(SID,1):
        logic,signal=base_formulas[sid]
        special=' 조기종료GRVT S2 216.78초 점검에서4orders/2fills/1왕복,fee.066502,net+.113898USDT가 있었다. 최종30분과분리하며한왕복의alpha인증이아니다.' if sid=='S2' else ''
        add('C0'+str(index),'v4 독립 '+SID[sid]+' FV MM','v4 실행','실제 testnet·mainnet public pilot',logic,signal,
            common_old_entry,common_old_exit,
            'testnet최종30분전체8경로fill0,BybitS4order1. mainnet기본16경로의도15/fill0. '+diagnoses[sid]+special,
            '독립공식검산27,456표본불일치0은명세일치확인이다. 체결0·계수차단·비용미달·수량0·실행수명결함을가설전면실패와분리한다.',
            '서로다른종목배정이며testnet가격은production과다름. 10분train/30분eval,이미본자료의탐색비교;OOS장기수익증거없음.', ['V4','V04','T01','T02','P01','P02','F01','F03',{'S1':'C01','S2':'C10','S3':'C11','S4':'C12'}[sid]],old_table(sid))
    add('C05','v4 선택형 OBI 지속성 확인','v4 확장','문서 후보/독립 운용 없음',
        '순간 잔량이 잠깐 나타났다 사라지는 것을 줄이기 위해 OBI가 유지된 뒤의 남은 움직임을 본다.',
        '현재 OBI 및선택5초/30초 지속;기본S4는순간OBI로독립실험.',
        '지속확인시각부터예측·주문을시작해야하며확인전의가격이동을future성과에넣지않음.별도문턱미동결.',
        'FV 공통 감축·risk limits를 적용하는 제안;별도 정책 성과 없음.',
        '고정W의시간평균OBI·onset연구는있지만,엄격한지속dwell 조건의독립체결실험은없다.',
        '지속확인은잡음과기회를함께줄이며정보가이미소진될수있다.',
        '시간평균·강도onset과dwell확인은다른변수다.', ['V3','V4','F01','R01'])
    add('C06','v3/v4 q/q와재고에따른q/2q 호가 수량 정책','재고·주문 후보','명세/초기 실행은 축소',
        '재고를가격FV왜곡대신수량비대칭으로다루되양방향신규alpha가각각필요하다.',
        '확정signed재고J,기본q,모든pendingnew/cancel/unknown leaves.',
        'q_b=q+max(-J,0),q_a=q+max(J,0). +q재고에서q/2q는매도2q중q가새short가능성이므로headroom과별도alpha필요.',
        '신규alpha없으면기존재고감축q만;reduce-only와새반대entry를분리한다.',
        '원형실행은한쪽OPEN및실제재고REDUCE로제한했다. q/2q동시양방향 신규의별도성과는없다.',
        '수량규칙이수익신호를대체하지않으며매수/매도예약을상쇄하면위험을누락한다.',
        'A05의총exit<=재고정책과동일하지않다. q/2q는새반대노출가능성을명시적으로포함한다.', ['V3','V4','V04'])

    # The first combination experiment is kept distinct from the repaired EDA.
    combo_labels={'OFI5':'D01','OFI5_TFI5':'D02','combined8':'D03'}
    for group,identifier in combo_labels.items():
        cols={'OFI5':'5초OFI/현재BBO깊이','OFI5_TFI5':'5초OFI/현재깊이와signed trade/현재깊이의별도두계수',
              'combined8':'OFI,TFI,OBI,동일BBO순증가/감소,trade intensity,OFI*TFI,TFI*OBI'}[group]
        d=combos[(combos.group==group)&(combos.horizon_seconds==5)]
        summary=tab(['Venue','Train','Test','5s RMSE감소%','예측중앙폭bp','최대eval 표준화'],[[r.venue,int(r.training_pairs),int(r.n),f(r.rmse_reduction_percent),f(r.median_absolute_forecast_move_bps,4),f(r.max_absolute_eval_standardized_feature,1)] for r in d.itertuples()])
        add(identifier,'초기고정5초 '+group+' ridge 모델','초기 복합 EDA','예측진단만/주문 없음',
            '같은BTC에서정보결합이원형단일입력보다미래가격을더잘설명하는지평가.',cols+';별도10분train표준화와ridge10,평가에서재학습하지않음.',
            '회귀예측부호로방향을해석했으나실제entry·fee·inventory gate를동결해운용하지않았다.',
            'H=1/5/30/60초는관측label이며청산규칙이아니다.별도exit성과없음.',
            '4venues*3groups*4horizons=48비교. 초기표현은instantdepth로정규화하고clip없는곱셈을써얕은book에서폭증했다.',
            'combined8의Bybit/Bitget5s RMSE감소는약-2463%/-10192%. 추가입력의경제적무효만으로귀속할수없고표현외삽의수치불안정을확인했다.',
            '후속asinh·창평균depth·trainclip 모델과같은모델이아니다. 모든음수결과를보존한다.', ['D01','D02'],summary)

    # Twelve corrected, train-frozen indicator models; every indicator has a card.
    indicator_defs=[
      ('l1_obi','L1 OBI 시간평균','TWMean((Qb-Qa)/(Qb+Qa))','잔량비대칭이후가격방향과규모정보.','작은이동·무변화가많고지속상태표본은독립신호가아니다.'),
      ('l2_total_obi_10','L2 top10 total OBI','TWMean((sumTop10Bid-sumTop10Ask)/(합계))','여러관측queue의대칭성.','깊은호가가실제로가격결정이나exit까지유효한지는별도.'),
      ('tracked_depth_obi','전체 추적 depth OBI','실제로유지한모든관측가격단계총수량의TW imbalance','넓은공급분포가미래방향정보를추가하는지.','snapshot깊이·레벨절단차이와먼가격의무관한큰주문에민감.'),
      ('ofi','OFI 창평균depth·asinh','asinh(sum(priceAwareOFI)/TWMeanL1depth)','순공급변화의동시충격과미래지속을분리.','H0충격설명은미래수익예측이아니다.'),
      ('replenishment','동일가격 net refill imbalance','(bidNetIncrease-askNetIncrease)/(increase합계)','매수측재공급/매도측재공급의방향정보.','공통top10가격만계산하므로완전소진·신규level제외;grossrefill아님.'),
      ('depletion','동일가격 net depletion imbalance','(askNetDecrease-bidNetDecrease)/(decrease합계)','한쪽표시queue소진압력.','소멸한level전체와숨은물량제외;취소와체결분해불가.'),
      ('trade_imbalance','공격 체결 volume imbalance','(buyVolume-sellVolume)/(buyVolume+sellVolume)','같은방향수요의정보.','거래없음0,unknown방향있는창제외;공급흡수없음.'),
      ('signed_trade_flow','signed trade 창평균depth·asinh','asinh((buy-sell)/TWMeanL1depth)','trade강도를깊이에비례한척도로비교.','원형S3 volume clock과다름;여기서는fixedtimeW.'),
      ('microprice','Weighted BBO mid premium 대용','weightedMid=(ask*bidQty+bid*askQty)/(qty합계);TWMean(1e4*(weightedMid/mid-1))','queue가중가격의mid대비premium.','fullStoikovmicroprice의상태전이모델아님;OBI*halfspread와수학적으로연결.'),
      ('ofi_trade_joint','안정화 OFI + signed trade ridge','OFI와signedtrade를별도두회귀계수로fit;OFI에tradevolume직접더하지않음','호가와체결정보의추가가치.','두입력변동이모두필요. 상수trade가OFI만재현한결과는결합성과에서제외.'),
      ('replenishment_rate_imbalance','보충률 비대칭','bidAdd/(bidAdd+bidRemove)-askAdd/(askAdd+askRemove)','방향별재공급대소진의비율.','공급/취소grossrate아님;활동없는분모는0처리한proxy.'),
      ('trade_x_replenishment','signed trade x 보충률 차이','signedFlow*(askRefillRate-bidRefillRate)','trade방향과공급흡수의상호작용.','부호가방향을반전할수있는곱셈이다. 이후aggressiveWeakRefill의방향보존곱과다름.')]
    def indicator_table(name):
        z=metrics[(metrics.indicator==name)&(metrics.window==5)&(metrics.horizon.isin(HS))]
        rows=[]
        for source in ('production','pilot_test'):
            g=z[z.source==source].groupby('horizon')
            for h in HS:
                if h not in g.groups:continue
                s=g.get_group(h)
                rows.append([source,h,len(s),f(s.rmse_reduction_percent.median()),f(s.rmse_reduction_vs_training_drift_percent.median()),f(100*s.balanced_accuracy_moving.median(),2),f(s.median_abs_forecast_bps.median(),4)])
        return tab(['資料','H秒','경로','RMSE감소%','drift대비%','비영BA%','예측폭bp'],rows)
    for idx,(name,title,formula,logic,limit) in enumerate(indicator_defs,1):
        z=metrics[(metrics.indicator==name)&(metrics.window==5)&(metrics.horizon==0)]
        h0='; '.join(source+' H0 RMSE감소중앙값'+f(s.rmse_reduction_percent.median())+'%' for source,s in z.groupby('source'))
        add(f'E{idx:02d}',title+' - 안정화 ridge FV 후보','기초 지표 EDA','예측/반응 평가만',logic,formula+';W=1/5/10/30/60초,train1/99%clip·표준화·ridge10·절편/H별계수동결.',
            '실제 신규진입가격·수량·수수료 gate를동결해운용하지않았다. 학습된예측bp의부호/크기를측정한후보.',
            'H=1~180초는미래mid label이고실제보유시간·exittrigger가아니다. 청산및cost후P&L미측정.',
            '모든W와매초H에대한표면평가,아래W5의경로동일가중중앙값. '+h0,
            '가격규모오차개선과조건부방향정보를분리한다. 채택가능한순이익은이표에서계산할수없다. 양/음결과와drift기준을같이보존.',
            limit+' 정상과거60초/미래180초전체경로만사용하며Arcusproduction방향UNKNOWN·GRVTfreshtrade부족은미측정처리.', ['F01','F02'],indicator_table(name))

    # Range proposals: lookbacks and fixed-band assumptions are separate.
    for identifier,lookback in [('F01',900),('F02',3600)]:
        add(identifier,f'과거{lookback//60}분 min/max 밴드 중심 평균회귀·grid 후보','range/grid','가격경로 EDA만',
            '과거상하단안에서가격이center로회귀하며grid왕복을만든다는가정.',
            't직전lookback의min/max,center=(low+high)/2,halfwidth, z=(mid-center)/halfwidth. 신호이후band고정.',
            '|z|<1,outerhalf |z|>=.5에서center쪽방향을평가. 실제grid간격·orderTTL·수량은미동결.',
            'center first hit와band exit의선후를진단;동일1초도달은모호표본. 실제gridexit/fillPnL없음.',
            ('15분band/highR/미래30분에서center-first경로중앙31.8%,exit-first67.6%;center이동/기존halfwidth1.147. 비중첩highR은center24.0%,exit71.0%,경로median n8.' if lookback==900 else '60분band/highR/미래30분center-first32.3%,exit-first59.0%,neither6.9%;center이동/halfwidth.400. 높은R에서도center회귀를보장하지않았다.'),
            '과거범위중간점은미래economicFV라고자동인정할수없다. band이탈·center이동·순익cost를따로평가해야한다.',
            '검사는center가설의firstpassage이고실제gridbacktest가아니다. overlappedanchors·소수독립episode·국면별band폭선택차이.', ['R01'])
    add('F03','고정 +-1% band center grid 후보','range/grid','가격경로 EDA/entry 표본 없음',
        'center에서상하100bp 안에장래FV가있다는가정으로되돌림왕복을노린다.',
        '과거center,고정halfwidth100bp,z 및밴드이탈.',
        '검사의outerhalf조건은center에서50bp이상내부이탈이다. 실제gridentry문턱은미동결.',
        'center복귀·band이탈·riskexit가후보;실제주문청산규칙과손익없음.',
        '이번정상자료에서|z|>=.5의진입표본0. 넓은범위안에머무름만관측했고큰되돌림을검증하지못했다.',
        'entry표본0은grid효과의부재가아니라가정한검사조건에서검증기회가없었다는뜻.',
        '다른간격grid의실패로확대하지않음;범위밖손실·inventory·fees모델미실행.', ['R01'])
    ratio_defs=[('F04','이벤트마다 L1 stock 합산/체결대금 비율','sum_quote(b*Qb+a*Qa)/sum_trade(price*qty)','quote_event_stock_sum','주문stock을여러번세어업데이트빈도에영향받는대조변형.'),
                ('F05','명목 stock 시간적분/체결대금 비율','integral(b*Qb+a*Qa)dt / sum_trade(price*qty)','notional_buffer_seconds','pilot에서실제체결가격을보존한주비율이며단위초.'),
                ('F06','수량 stock 시간적분/체결수량 비율','integral(Qb+Qa)dt / sum_trade(qty)','quantity_buffer_seconds','production집계에가격*수량곱이없어사용한proxy;정확한명목비율아님.')]
    for identifier,title,formula,definition,note in ratio_defs:
        add(identifier,title+' - 변동성 국면 후보','R 국면','위험/변동성 EDA만',
            '공급stock 대비소비flow가많으면미래변동성이낮고작으면높다는가설. R은방향부호가없다.',
            formula+';W1/5/10/30/60;train25/75%threshold동결,거래0은inactive로분리. '+note,
            'R단독entry없음. 저변동시크기/폭 또는다른directionprovider를조절하는후보다.',
            'R단독exit없음. 실제inventory감축과독립적인risk/state변수.',
            ('정확한pilotR:W5→H5 high/lowRV .768,W60→H60 .864. 명목/수량R순위상관거의1,상대차이W5중앙.0011%,W60 .0039%.' if identifier=='F05' else 'production수량R:W5→H5 high/lowRV .375,W60→H60 .474. pastRV·flow에R을추가한RV MAE개선W5 약.9~1.7%,W60거의0또는음수. 이벤트stock합산의모든native tick복제는아니다.'),
            '관측비율과미래RV의관계는있지만이미조용한시장이지속되는효과와activity를통제하면추가가치가작다.',
            '개인분석원글은S&P500 tick의일별/20일MA라이번초단위crypto검증과다름. GRVT는ratio학습freshtrade부족.', ['R01'])
    add('F07','high-R grid / low-R 방향성 자동 전환','R·grid 복합','복합 가설/부분 EDA',
        'highR에서는range회귀,lowR에서는OFI·공격수요·약한refill의방향을따르는두갈래전환.',
        'R frozenquantiles,center/band,OBI/OFI/TFI/refill 강도와부호.',
        'high/low와각alpha를같이확인한다는제안. lowR만으로매수/매도를결정하지않으며실제통합entryrule은미동결.',
        'gridrange이탈/center변화,방향반전,holding·inventorystop을필요로하지만통합exitPnL실험없음.',
        'L1 W5/H25 방향정렬평균high .187bp/low .134bp,비영BA65.09%/55.29%;OFI high .133/low .154bp이나lowH60 -.001bp. 낮은R에서방향정확도우위는일관되지않음.',
        '큰변동성과방향을아는것은별개다. Rhigh를grid승인으로, Rlow를directionedge로즉시치환할수없다.',
        'regime별강도·band폭·독립episode차이가있다. 실제전환기체결·cost후profit미실측.', ['R01','R02'])
    add('F08','공격 체결 x 소비 측 weak refill','flow·refill EDA','원시 방향·강도 평가',
        '같은방향공격흐름이강하되소비되는반대호가의보충이약하면가격진행이남는다는가정.',
        'signedFlow*(1-consumedSideRefillRate);buy일때ask,sell일때bid. 관련가격측활동이없으면NaN 제외.',
        '원시부호를방향으로,train분위수를강도로평가. 구체실제entry quote/fee gate 미동결.',
        'H1~180반응label만계산;실제exit정책없음. 새S3처럼흡수→소진의episode전이가아니다.',
        'production H25강한-약한차이+.061bp;highR mean+.044bp/BA54.43%,lowR mean+.025bp/BA51.00%. lowR H60 mean-.179bp.',
        '곱이0아닌경우부호는trade와같아서단순direction개선보다강도·onset선택의추가가치를검정해야한다.',
        'E12의(signedFlow*(askRate-bidRate))와부호·표본정의가다름;netrefill로grossabsorption을식별하지못함.', ['R01','R02'])
    onset_map=[('l1_obi','E01'),('l2_obi','E02'),('ofi','E04'),('replenishment','E05'),('trade_imbalance','E07'),('signed_trade_flow','E08'),('aggressive_weak_refill','F08')]
    for idx,(name,parent) in enumerate(onset_map,1):
        z=onset[(onset.source=='production')&(onset.indicator==name)&(onset.horizon==25)]
        n=z.n.median() if len(z) else None;mean=z.mean_bps.median() if len(z) else None;med=z.median_bps.median() if len(z) else None;flat=z.p_flat.median()*100 if len(z) else None
        add(f'O{idx:02d}',name+' - 독립 onset 비교 후보','onset 변형','가격 경로 EDA만',
            parent+'의지속상태모든anchor대신신호가새롭게극단에진입한시점의이후반응을평가.',
            'W5,train10/90%outside로처음cross;buy/sell합쳐180초cooldown. 모든과거60/미래180초정상경로필요.',
            'onset의부호와정보cutoff를신호시점으로삼되실제makerentry·volume·feesgate는동결하지않음.',
            'future1~180초경로·favorable/adverse관측만. H는청산조건이아니다.',
            f'production H25:경로median n={f(n,1)},alignedmean={f(mean)}bp,alignedmedian={f(med)}bp,p_flat={f(flat,1)}%. pilot비중첩onset대체로경로당7~9개.',
            '평균반응이양수여도중앙0·반대이동·미체결이가능하다. 같은지속상태를수천독립신호로세는오류를줄인다.',
            'onset은원형FV·새state onsets와문턱/clock이다르며실제체결성과는미측정.', ['R01','R03'])
    add('O08','신호 절댓값 강도·signed decile 후보','강도 변형','반응 EDA만',
        '강한신호가약한신호보다큰directional move를만드는지평가하고작은신호를필터할가능성을검토.',
        '7지표 W5의train signed deciles와절댓값하위/상위20%;평가에서bin재학습없음.',
        '강도filter 후보이나actualentry gate미동결. 부호정렬후동일표본기준비교.',
        '각초반응분포평가만;exit·fee후손익없음.',
        'H25strong-weak: L1 .324,L2 .288,OFI .253,refill .044,signedtrade .003,tradeimbalance .115,weakrefill .061bp.',
        '강도는일부지표의규모정보를보였지만표본선택·무변화·cost·fill후markout을같이보아야한다.',
        'Discrete지표는빈bin이있다. 강한표본을사후고른수익선택으로사용하지않음.', ['R01'])

    # Updated independent strategies plus sub-branches and controls.
    new_entry='defaultW5의ready state,score방향한쪽maker. h=max(spread/2,tick/2),center=mid+tick*score-2tick*normalizedInventory;반대BBO와cross하지않게tickround. thinbook폭2배·크기.5, jump신규차단. cap150USDT/depth10%,flat에서만신규,model.valid 필요.'
    new_exit='재고발생후reduce-onlymaker를우선. longask/shortbid를BBO근처로감축;maxhold120초·adverse.35%·DD20·stale/grace/closing은riskmakerunwind. emergencytaker기본false이며즉시hardstop·완전shutdown을보장하지않음.'
    def new_table(sid):
        rows=[]
        for v in VENUES:
            r=modern_map['C1.'+v+'.'+sid]
            rows.append([v,r['enabled'],r.get('intents',0),r['fill_events'],f(r['gross_mtm_pnl'],5),f(r['maker_fees'],5),f(r['net_mtm_pnl'],5),f(r['end_inventory'],8)])
        return tab(['Venue','資料valid','意図','Fill','Gross','Fees','Net','残再庫'],rows)
    korean_new={
      'S1':('OFI를 점FV 대신 방향·onset·지속 상태로 읽는다.','asinh(OFI/TWdepth),train absq90로score를[-1,1]clip;abs<.4threshold중립. 비0sign전환onset,지속시간,OFI차분가속기록.','기존계수FV를제거했지만미래방향확률·이동규모는학습하지않음.'),
      'S2':('basis 괴리와발생origin을분리해로컬leg이익이가능한dislocation을평가.','spreadbp=1e4log(local/Binance);residual=spread-priorrollingmedian;최근1초ref/local이동으로origin. ADF<.05·KPSS>.05·연속200,refage1.5s·sourceskew500ms,drift<=max(5bp,6MAD).','spread수축과미헤지localPnL을분리;origin휴리스틱의인과적식별없음.'),
      'S3':('공격flow이후흡수와공급/공격자소진의순서를확인한다.','knownTI>=max(.3,trainq90),창flow>=L1depth→PRESSURE;episode후flow>=초기consumedDepth,refill/flow>=.5,move<1tick→ABSORPTION. episode30초.','체결부호단독에서사건전이로변경했으나netrefill로gross공급을식별하지않음.'),
      'S4':('L1 OBI를주방향상태로사용하고L2는보조진단으로둔다.','trainL1 absq90 score,score.9에서STRONG,.4threshold미만neutral. L2top10OBI·levelgap·RV를공통engine에보관.','원시방향정보와수수료를초과하는maker체결edge를분리.')}
    for idx,sid in enumerate(SID,1):
        logic,signals,explanation=korean_new[sid]
        if sid=='S2':exit_rule='residual수축과실제local PnL이각각비용을초과할때최초filledqty절반TP1;절반이하에서반복안함. residual<=.5bp이고이익조건충족시TP2. risk/time은profitgate우회maker. '+new_exit
        else:exit_rule=new_exit
        add(f'G{idx:02d}','상태 개정 '+sid+' '+SID[sid],'2026-10-07 state','역사 native replay/새 live 운용 없음',logic,signals,
            new_entry+(' absresidual>max(trainq90,maker왕복+1bp)·origin방향정합도필요.' if sid=='S2' else ''),exit_rule,
            '10분학습→30분native평가,세queue/latency조건. 아래C1단독경로실측과부록전체3조건. S2모두0의도;trade한단독7경로전부비용후손실. '+explanation,
            '코드수정·체결발생은실행진전이고수익성확인은아니다. 이미본기간의단일fold이며normalmaker폭을비용만큼벌리던구형정책도함께변경돼alpha수정효과를단독식별하지못한다.',
            '새미관측날짜·실측latency·계정specificfee·funding·USD/USDT미포함. Magnitude state는관측RV이며미래이동모델이아님.', ['M01','M02','M03','M05',f'C0{idx+2}'],new_table(sid))
    add('G05','S2 Binance-leads 로컬 catch-up 분기','state 하위 후보','구현/entry 표본 없음',
        'reference가먼저움직이고local이따라오지못한괴리가local방향이동으로메워지는경우.',
        '|ref1sreturn|>=1bp,|localreturn|<.5|refreturn|;residualsign과reference방향정합. stationarity/freshness/cost 공통gate.',
        'local저평가residual<0·ref상승이면localBuymaker;대칭short. 같은assetref관측만으로hedge포지션을만들지않음.',
        'G02의localprofit조건TP1/TP2및time/riskmaker감축.',
        '최종4venueS2가모두0의도/0체결. origin상태관측과actualentry를구분;분기별왕복PnL없음.',
        'Binance가모든시간항상lead한다는가정은하지않으며timestamp정렬과가격반응완료여부가중요.',
        'commonshock·basis변화·얕은흐름의noise가origin분류를오염할수있음.', ['C04','M01','M02'])
    add('G06','S2 local-idiosyncratic 로컬 되돌림 분기','state 하위 후보','구현/entry 표본 없음',
        'local이독자적으로움직였으나reference는안움직였고괴리가local되돌림으로해소될가능성을본다.',
        '|local1sreturn|>=1bp,|refreturn|<.5|localreturn|;residual과localimpulse반대방향entry.',
        'local급등·양residual이면makerSell,local급락·음residual이면makerBuy;G02공통stationarity/cost/clockgate.',
        'residual수축만으로profit으로부르지않고actuallocalleg이익을같이요구.',
        '분기별actualtrade/독립episodeexit성과없음.원형S2학습계수분해는기계적수렴분기의수익증거가아님.',
        'reference가뒤늦게local을따라오면residual이축소돼도내로컬포지션은수익을얻지못할수있다.',
        'local의새정보와일시적압력을실시간으로완전히구별하지못함.', ['C04','M01','M02','D01'])
    add('G07','S3 흡수 후 공급 소진 breakout 분기','state 하위 후보','구현/전체S3 replay에 포함',
        '흡수가유지되다가소비되는측의재공급이약해지고가격이전진하면같은방향수요의돌파를노린다.',
        'ABSORPTION 이후동방향TI유지,누적refill/episodeflow<.3,가격>=1tick진행,가시적depletion>0.',
        'buyepisode의ask공급소진이면Buy,selledgebid공급소진이면Sellmaker. pressure만으로전이하지않음.',
        new_exit,'G03전체상태기계PnL에포함되지만해당분기만고립한충분한episode/PnL표는없다.',
        '단순TI와동일하지않은이벤트가설. 공급의완전gross관측이없어전이를proxy로명시한다.',
        '누적refillratio·창peakflow·반영tick의관측delay와level범위절단에민감.', ['C05','M01','M02'])
    add('G08','S3 흡수 후 공격자 소진 reversal 분기','state 하위 후보','구현/전체S3 replay에 포함',
        '압력이공급에흡수된후공격flow가약해지고가격이진행하지않으면반대방향을노린다.',
        'ABSORPTION에서currentflow<episodepeak*.5,visible_refill>0,가격진행<1tick.',
        'buy공격자소진이면Sell, sell공격자소진이면Buymaker. unknown이나warmup은episode초기화.',
        new_exit,'G03결과에포함. 별도reversal분기의새날짜독립검증과최종왕복수익은없음.',
        '공격trade의원래부호와반대entry이므로G07의돌파와동일전략으로합쳐해석하지않는다.',
        'peak의fixedW측정과episode누적측정이다르며full흡수와우연한flow감소를완전히구분하지못함.', ['C05','M01','M02'])
    add('G09','극단 OBI·OFI·levelgap/spread 확대 Jump Risk MM','위험 정책 후보','구현/통합 비교에 포함',
        '한쪽queue가급격히취약해지고spread나levelgap이커지면유해한fill과inventorytail을억제한다.',
        '|OBI|>.85,|OFI|>1.5 및nextlevelgap>=3ticks 또는spread>train90%. R낮음·RV높음은THIN_BOOK.',
        'JUMP_RISK는신규노출없음;THIN_BOOK은폭2배·크기.5. 단독directionalpha가아님.',
        '재고있으면반대side의reduce-onlymaker를유지/가깝게조정. 단순폭확대가실제감축은아니다.',
        'volatility/inventory ablation과단독state에포함. jump만추가한독립counterfactualP&L없음.',
        'risk설명변수의조건부타당성과수익예측을분리한다. maker-onlyriskstop은즉시체결보장없음.',
        'jump문턱은초기동결규칙이며미래jump확률calibration은미완료.', ['M05','M07','M02'])
    add('G10','Direction x Magnitude x Volatility x Inventory 통합 구조','통합 설계','구현된 부분과 미학습 부분 구분',
        '각축의역할을방향·폭/크기·위험·감축으로분리하고점FV대신주문의도를산출한다.',
        'S4/S1/S3/S2 direction score,observedRV의FLAT/SMALL/ACTIVE, R/spread/gap risk,actualfilledinventory.',
        '선택모듈score가중평균,S4.45/S1.30/S3.15/S2.10;|합성|<.2neutral. 미래규모예측을배워tickedge에대입한구조는아직아님.',
        'inventory skew최대2ticks와reduce-onlymaker. closing·손실·holding위험은override.',
        'H01~H07증분비교로구현상태를평가. 현재forecast확률/규모calibration·multi-fold검증미완료.',
        '수익모델이완성됐다는뜻이아니며공통엔진과모듈ablation을실행가능하게한architecture다.',
        '관측된RV상태를futuremove모델로포장하지않음. 모든축의실제필요성은새기간의동일asset비교필요.', ['M05','C07','M01','M02'])
    future_models=[('G11','가격 변화 유무·활동 확률','P(mid_change by H) 또는 무변화확률','작은 신호와 무변화가 많은 상황을 먼저 분리한다.'),
                   ('G12','다음 가격 변화 방향 분류','다음 mid가 up/down일 조건부확률','fixed30초 전체 이동량 대신 다음 변화 방향을 예측한다.'),
                   ('G13','다음 가격 변화까지의 시간','time-to-next-change 또는 price event hazard','정보가 얼마나 오래 남고 제출 지연 뒤 이용 가능한지 다룬다.'),
                   ('G14','방향 조건부 이동 규모 모델','E[abs move|direction,state,H] 및 경험적 quantile','방향을 알 때 충분한 tick/bp가 비용 뒤 남는지를 별도 추정한다.')]
    for identifier,title,signals,logic in future_models:
        add(identifier,title+' - 후속 학습 후보','후속 목표 분해','논의/고유 학습·운용 없음',logic,
            signals+'; OBI/OFI·flow·depth·volatility state는 입력 후보이며 최종 특징·계수·loss·threshold는 미동결.',
            '확률·규모·시간을 quote 정책에 반영하는 제안이나 독립 주문 규칙은 아직 없음.',
            '고유 exit 정책과 왕복 PnL 없음. future horizon과 holdings를 자동 동일시하지 않음.',
            '가격 변화/부호/규모와 현재 RV·무변화 빈도는 관측했지만 이 고유 모델을 fit하여 운용한 기록은 없다.',
            '상태 기반 G10이 이러한 학습 모델까지 완료한 것은 아니다. 목적 변수를 나눠야 한다는 연구 제안을 별도 등록한다.',
            '기존 indicator regression 또는 observation RV 결과를 이 모델의 성과로 대체하지 않는다.', ['F01','D01','M05'])

    profiles={'static':('가격 신호 없는 BBO 기본 MM',[]),'obi':('L1 OBI 통합 기준',['S4']),
              'book':('OBI + OFI book pressure',['S4','S1']),'flow':('book + absorption/exhaustion',['S4','S1','S3']),
              'cross':('flow + cross-venue origin',['S4','S1','S3','S2']),
              'volatility':('cross + R/RV/levelgap 위험',['S4','S1','S3','S2']),
              'inventory':('volatility + inventory skew',['S4','S1','S3','S2'])}
    for idx,(profile,(title,mods)) in enumerate(profiles.items(),1):
        rows=[]
        for sc in ('C1','O1','C4'):
            r=modern_map[sc+'.ablation.'+profile];rows.append([sc,r.get('intents',0),r['fill_events'],f(r['gross_mtm_pnl'],5),f(r['maker_fees'],5),f(r['net_mtm_pnl'],5),f(r['max_drawdown'],5),f(r['end_inventory'],6)])
        logic='동일Bybit BTC에서각추가모듈의가치를비교한다. 기존서로다른asset의단독S1~S4성과를순위로비교하지않는다.'
        signal='BBO,alpha score없음.' if profile=='static' else '선택모듈='+','.join(mods)+';선택가중치합으로정규화·|score|<.2neutral.'
        if profile not in ('static','volatility','inventory'):signal+=' R/RV/gap/OFI 기반risk를제거하는ablation이나spread기본위험과hardlimits는유지.'
        add(f'H{idx:02d}','통합 '+profile+' - '+title,'통합 ablation','동일자산3조건 native replay',
            logic,signal,
            ('flat시BBO buy/sell두side,재고/holding/DD한도공통. 무제한양방향inventoryMM은아님.' if profile=='static' else new_entry+' 비volatilityprofile은riskfeature를고정해비교.'),
            ('재고가생기면반대BBO의STATIC_MAKER_UNWIND;forcehold/risk감축공통.' if profile=='static' else new_exit+('inventory center/감축비대칭을적용.' if profile=='inventory' else 'directional center는유지하나inventory center/감축비대칭은제거.')),
            '아래동일BybitBTC의3가상시나리오. fill0을DD0우수성과로선택하지않음. inventory는flat-onlyentry때문에주로exit정책을바꾼다.',
            '모듈을추가할수록항상좋아진증거는없다. profile은별도가상대안이며69케이스합산profit은실계좌수익이아니다.',
            '기본가중치·neutral문턱·정상quote폭도작용해각모듈의효과를완전히인과식별하지못한다. 일부profile0의도는기본spreadgate와tick/minimum의상호작용일수있다.', ['C07','M01','M02'],tab(['설정','意図','Fill','Gross','Fees','Net','MaxDD','EndBTC'],rows))
    # Retain code revisions that predate the final sealed research policy.
    add('I01','상태 S2 초기 구현 - 비연속 표본 결합·드리프트 제한 전','개정 중간판','보존 소스/최종 G02에서 보완',
        '같은 basis/origin 논리를 사용했지만, 정상 기준가격이 유효한 모든 학습 표본을 한 시계열로 이어 붙이고 실시간 baseline의 이동을 별도로 제한하지 않았다.',
        'log basis, causal rolling median, ADF/KPSS, origin, cost gate. 초기 calibration은 reference가 있는 records를 연속성 확인 없이 모았고, score 생성에는 stable_baseline 조건이 없었다.',
        'ready·stationary·abs residual>entry와 origin 방향 정합성만 통과하면 한쪽 maker 후보. G02는 가장 긴 연속 학습 구간과 baseline drift<=max(5bp,6MAD)를 추가했다.',
        'basis 수축과 실제 로컬 이익을 요구하는 TP1/TP2 및 maker 위험 감축은 같은 계열이었다.',
        '초기·최종 소스의 해당 차이를 tar 스냅샷에서 직접 확인했다. 중간 v2의 S2 4개 단독 경로는 모두 체결 0이었다. 초기 소스와 각 중간 재생의 정확한 전체 runtime 결합은 최종 sealed만큼 동결되지 않아 별도 수익성 결과로 승인하지 않는다.',
        '공백을 건너뛰어 정상성 검정을 하면 시간 의존 구조와 표본 간격이 왜곡될 수 있고, 천천히 이동하는 기준을 수렴 대상이라고 오인할 수 있다. 최종 보완은 이 계산 위험을 줄였지만 수익성 향상의 증거는 아니다.',
        '이 항목은 C02의 OLS anchor와도 G02의 최종 state 구현과도 구분한다. 조기 testnet의 +0.113898 USDT는 이 초기 state 모델의 성과가 아니다.', ['I01','I03','I04','M01'])
    add('I02','상태 S3 초기 구현 - 체결 유효성 확인 전 episode 누적','개정 중간판','보존 소스/최종 G03에서 보완',
        'PRESSURE→ABSORPTION→공급 또는 공격자 소진의 가설은 같지만, episode 누적 flow를 공통 feature의 유효성 검증보다 먼저 늘렸다.',
        '초기 on_trade는 episode 방향과 side만 일치하면 quantity를 self.flow에 더한 뒤 공통 on_trade를 호출했다. 최종은 finite·양수 수량/가격, 직전 book의 신선도, cursor 이전 timestamp 여부부터 확인한다.',
        '흡수·breakout/reversal 상태 및 공통 maker 정책을 사용한다. 나중에 제외되는 오래된 trade가 누적 flow에 들어가면 전이 문턱을 잘못 통과할 수 있었다.',
        '같은 maker 감축·holding·손실 제한. 실제로 체결된 재고와 신호 episode를 별도로 관리한다.',
        '스냅샷 간 코드 차이는 확인됐다. 최종 재생은 오래된 trade 417개를 제외했다. 그 수가 모두 초기 S3 episode를 바꾸었다고 검증한 것은 아니며, 해당 오류만 분리한 손익 반사실 실험은 없다.',
        '가설의 틀과 입력 집계 순서의 구현 오류를 구분해야 한다. 잘못 누적한 신호 상태를 바로잡았다는 사실이 새 episode의 예측력을 보장하지 않는다.',
        '중간 원장의 부분 체결을 최종 G03 손익에 합산하지 않는다. 초기 S1/S4 전략 파일은 스냅샷 간 동일해 별도 새 알파로 중복 등록하지 않았다.', ['I01','I03','I04','I06','M01'])
    intermediate=read_json('docs/evidence/mm_state_revision_20261007/replay_v2/summary.json')
    ir={r['case']:r for r in intermediate['results']}
    old_static_rows=[[sc,ir[sc+'.ablation.static'].get('intents',0),ir[sc+'.ablation.static']['fill_events'],f(ir[sc+'.ablation.static']['net_mtm_pnl'],6),f(ir[sc+'.ablation.static']['end_inventory'],6)] for sc in ('C1','O1','C4')]
    add('I03','통합 static 초기 구현 - 상시 재고 감축 분기 전','개정 중간판','중간 v2 재생/최종 H01에서 보완',
        '신호 없는 flat BBO 양방향 기준군이었지만, 재고가 생겼을 때 평상시 reduce-only 주문을 반환하는 분기가 빠져 있었다.',
        'BBO·unit·lot·max inventory. 초기 함수는 qty<=0 또는 new_exposure=False이면 바로 빈 의도를 반환했다. 공통 flat-only 제한 때문에 재고 보유 중 감축 의도가 사라질 수 있었다.',
        '평탄할 때 bid/ask를 제출하고 공통 위험 한도를 사용했다. 최초 스냅샷의 별도 calibration 인자 중복도 통합 생성자 단계에서 수정되었다; 이것은 새 진입 가설이 아니다.',
        '보유 중 평상시 STATIC_MAKER_UNWIND가 없고 공통 강제 holding/risk 경로에 의존한 초기 구현. 최종 H01은 확정 재고를 lot 단위로 반대 BBO에 reduce-only로 돌려준다.',
        '중간 v2 static C1은 의도 61건·fill event 23개·net -0.541994 USDT였다. 최종 H01의 353건·103개·-2.464474와 별도 기록이다. 아래는 중간 v2의 3개 조건이며 초기 미완료 원장과 합산하지 않는다.',
        '거래 수와 보유 시간이 달라져 손실 절댓값만으로 개정 우열을 판단하면 안 된다. 평상시 감축 누락을 고친 목적은 기준군의 실행 의미를 다른 정책과 맞추는 것이다.',
        'v2와 최종은 동일 자료를 반복 재생한 개발 이력이다. 독립 OOS 표본이나 새 30분 live 실험이 아니며, 가중치·queue 등 전체 변경 효과와 분리된 통계적 개선 검정도 없다.', ['I01','I02','I03','I04','I05','I06','M02'],tab(['조건','의도','Fill events','Net USDT','End BTC'],old_static_rows))

    baseline_proposals=[
      ('J01','S2 robust EWMA baseline 대안','basis의 중심을 느린 지수 가중 평균으로 갱신하되 이상치의 영향을 제한한다.','robust EWMA; half-life, 이상치 처리와 초기화는 피드백에서 동결되지 않았다.'),
      ('J02','S2 rolling quantile center baseline 대안','분포가 비대칭일 때 하나의 고정 평균 대신 분위수로 중심을 정의한다.','rolling quantile center; 사용할 분위수와 조합·lookback은 미동결. q50만 사용하면 구현된 rolling median과 수학적으로 같다.'),
      ('J03','S2 state-space slow mean baseline 대안','천천히 변하는 공통 basis와 빠른 잔차를 잠재 상태로 분리한다.','state-space slow mean; 관측/상태 잡음·전이식·filter 종류와 파라미터는 미동결.')]
    for identifier,title,logic,signals in baseline_proposals:
        add(identifier,title,'피드백 계산 대안','제안/고유 구현·학습 없음',logic,signals,
            '추정 중심을 뺀 residual과 Binance/local origin·freshness·비용으로 거래 후보를 만드는 대안. 실제 entry threshold나 독립 주문 규칙은 확정하지 않았다.',
            '로컬 이익과 residual 수축을 함께 요구한다는 설계 방향만 있으며 고유 exit·PnL은 없다.',
            '피드백의 baseline 후보 목록에서 확인했다. 실제 채택된 것은 G02의 causal rolling median이며 이 대안들을 fit·재생·운용한 기록은 없다.',
            '중심 추정의 유연성과 진짜 기회의 보존 사이에 trade-off가 있다. 실제 dislocation을 baseline이 빠르게 따라가면 alpha를 지우고, 너무 느리면 지속적인 basis drift를 가짜 괴리로 만들 수 있다.',
            'rolling median의 EDA나 실제 S2 결과를 이 대안의 성과로 대입하지 않는다. 문서에 모델 이름이 있다는 사실은 유효한 수익 모델이라는 뜻이 아니다.', ['FEEDBACK','M05','C04'])
    add('J04','피드백 정책 A - 비상 taker 없는 maker-only 감축','청산·위험 정책','제안 및 최종 기본 정책',
        '정상·비상 상황 모두 resting maker로만 줄이고 taker 수수료와 즉시 crossing을 피한다.',
        '재고·holding·손실·stale 상태와 opposite BBO. 반대 best를 즉시 치는 주문에 maker fee를 부여하지 않는다.',
        'G/H의 entry alpha를 사용하며 위험 상태에서는 새 노출과 재고 확대 side를 차단한다.',
        '재고 감축 side만 BBO로 당기고 reduce-only를 유지한다. emergency_taker=False이므로 위험 문턱을 넘어도 즉시 전량 청산을 보장하지 않는다.',
        '최종 state 기본 재생과 공통 정책에 포함됐다. 다른 alpha와 독립적인 정책 A 대 정책 B의 체결·손익 실험은 없다.',
        'maker-only는 비용·주문 방식의 선택이며 손절의 시간 보장이 아니다. exit 부문의 불리한 markout과 최종 미청산 재고를 함께 확인해야 한다.',
        '최종 shutdown MTM을 실제 청산으로 부르지 않는다. A02의 정상 maker·hard IOC 정책과 구분한다.', ['FEEDBACK','M01','M07','M02'])
    add('J05','피드백 정책 B - 정상 maker·hard risk emergency taker','청산·위험 정책','명시 옵션/이번 성과에는 비활성',
        '정상 운용은 maker로 하되 극단 위험에서 비용을 지불하고 노출을 즉시 줄이는 별도 정책.',
        'hard risk threshold, inventory·fresh book·minimum·taker fee와 emergency 횟수.',
        '기존 entry 가설을 사용하며 비상 감축을 새로운 directional entry로 혼동하지 않는다.',
        '명시적으로 emergency 옵션을 켰을 때만 시장성 reduce-only 감축. emergency 비용·빈도·PnL을 정상 maker와 별도 기록해야 한다.',
        '피드백과 코드 옵션은 확인됐지만 최종 state 재생에서는 false였다. 따라서 이번 G/H 손익을 정책 B의 검증 결과라고 부르지 않는다.',
        'tail inventory 위험을 줄일 수 있는 반면 spread·슬리피지·taker 비용이 추가된다. 정책 A와의 같은 조건 비교가 없으므로 우열을 단정할 수 없다.',
        'v1/v4의 위험 IOC 기록을 새 state 정책 B의 실험으로 재분류하지 않는다. 원형과 계정별 비용·위험 문턱도 다르다.', ['FEEDBACK','M07','M01'])
    add('J06','피드백 일반형 - Direction·Volatility·Inventory quote skew','피드백 통합 가격 정책','제안/최종 tick 정책의 선행식',
        '정확한 미래 FV 한 점 대신 현재 mid를 기준으로 방향과 재고에 따라 quote 중심을 옮기고 위험에 따라 폭을 정한다.',
        'bid=m-h(V)+alpha*D-kappa*I, ask=m+h(V)+alpha*D-kappa*I. D=방향 score,V=유동성/변동성,I=확정 재고.',
        '양의 D는 중심을 올리고 음의 D는 내린다. 실제 tick round·PostOnly·양방향 승인·lot 정책은 별도 필요하며 alpha/kappa의 학습값은 피드백에서 확정하지 않았다.',
        'long 재고는 중심을 낮춰 ask를 더 적극적으로 하고 bid 확대를 억제한다; short는 대칭. 폭 확대만으로 재고가 줄어든다고 해석하지 않는다.',
        '일반형 자체의 독립 운용이나 PnL은 없다. G10/H07이 이 방향을 tick 기반 skew와 flat-only 신규 진입·maker 감축으로 제한 구현했다.',
        '제안된 연속형 양방향 가격식과 실제로 보유 중 감축만 하는 최종 구현은 같지 않다. 따라서 H07 결과를 이 일반형의 완전한 검증으로 확대하지 않는다.',
        'D·V·I의 역할 분리는 논리적 설계이며 계수의 경제적 유효성, fill-conditioned edge와 최적 quote 폭은 미검증이다.', ['FEEDBACK','M05','C07','M02'])

    # Non-MM project examples are included, but no experiment is invented.
    add('Z01','Turtle breakout 프로젝트 검증용 프로토타입','프로젝트 기타','코드 확인/성과 근거 없음',
        '직전고가돌파long·저가이탈exit의추세추종. MM과독립된프로젝트시스템검증용전략도누락방지등록.',
        '문서는20일고가/10일저가라표현하나설정interval=1m,BTCUSDT paper. deque high20/low10에현재bar를넣고[:-1]로비교.',
        '실제코드는현재bar포함20칸에서현재를빼직전19bars고가와close를비교;in_position=False일때ENTER_LONG.',
        '직전9bars저가이탈EXIT_LONG. 실제fill전략연계없이signal발생시in_position을변경한다.',
        '이대화의코인MM testnet/pilot/EDA에포함된체결·PnL자료없음. 독립생산운용을확인하지못함.',
        '20일/10일명칭·1분interval·실제19/9lookback이서로달라규칙명확화가필요하다. 테스트전략존재와유효한trading성과는별개다.',
        '이보고서는코드를고치거나새backtest를실행하지않았다. 미래침범방지를위해currentbar제외는맞지만deque크기로lookback하나가줄어든다.', ['C08'])
    add('Z02','Example signal - 직전 캔들 고가/저가 돌파','프로젝트 기타','hot-path 예제/성과 근거 없음',
        '완료한새1분bar가직전bar범위를벗어나면long진입/exit signal을만든다.',
        'BinanceBTCUSDT bar_1m,구독warmup30bars,전략내closedbars최소4,closed·noncorrection조건.',
        'close>prev.high면ENTER_LONG,signal유효시각현재+60초.',
        'close<prev.low면EXIT_LONG. 자체filledposition검증·risk·quoteprice설계는예제에없고실행층의역할.',
        '해당예제의실제운용PnL/예측성과기록없음. MM연구표의성공/실패에합산하지않는다.',
        '전략인터페이스·warmup·eventid·validity연결을검증하는기본예제다.',
        '60초signal validity는목표보유기간이나수익률근거가아니다.', ['C09'])

    assert len({s['id'] for s in registry})==len(registry)
    families=Counter(s['family'] for s in registry)
    intro=f'''# 코인 MM 전략·후보 전체 통합 보고서

작성일은 2026-10-12 KST이며, 실측 범위는 2026-10-03~07의 보존 기록이다. 이번에는 기존 자료를 통합했으며 새로운 운용, 백테스트 또는 모델 최적화를 실행하지 않았다.

이 보고서는 **{len(registry)}개 등록 항목**을 각각 기술한다. 여기에는 전략 원형, 개정판, 하위 분기, 기초 지표 후보, 대조군, 주문·위험 정책과 미실행 제안이 포함된다. 동일한 계열이라도 v3, v4, 안정화 EDA 모델, 상태 개정판과 보존된 초기 구현을 구분했다. 등록 항목 수는 독립적으로 검증된 알파의 수가 아니다. 거래소·종목 조합, 계산 창과 측정 시계, queue·지연 설정은 각 카드와 부록에서 별도로 비교한다.

## 1. 핵심 결론과 판정 언어

운영 가능한 수익성이 검증된 전략은 아직 없다. 다만 모든 기록이 손실이었다는 결론은 정확하지 않다. 조기 종료된 GRVT S2 테스트넷 점검에서는 **한 차례 왕복 거래의 순익이 +0.113898 USDT**였고, Bybit S3 테스트넷의 30초 예측 RMSE는 약 9.3% 감소했다. 각각 표본 한 건과 테스트넷 예측 결과이므로 실제 시장의 장기 수익성 증거로 해석하지 않는다.

최종 테스트넷 8개 경로와 최초 mainnet 공개 데이터 pilot 16개 경로에서는 체결이 없었다. 상태 개정판의 기본 conservative1x 재생에서는 단독 16개 경로 중 7개에서 체결이 발생했고, 이 7개 경로의 비용 후 손익은 모두 음수였다. 나머지 9개는 체결 손익을 평가할 표본이 없다. 따라서 손익 0, 주문 0, 모델 승인 차단, 수치 불안정, 실행 결함과 경제적 손실을 구분해서 읽어야 한다. [최종 상태 개정 근거]({(E/'mm_state_revision_20261007/REPORT_KO.md').as_posix()}).

실험의 단계도 구분한다. **실제 testnet**은 외부 테스트넷에 주문한 기록, **public pilot**은 실제 시장의 공개 데이터로 주문과 체결을 모의한 기록, **historical replay**는 보존 이벤트의 재생, **EDA**는 신호와 가격의 관계만 분석한 결과다. **제안**은 논의되었지만 규칙이나 독립 운용을 확정하지 않은 항목이다. 카드에서 H는 가격 반응을 측정한 시계일 수 있으며 자동으로 청산 시간이나 보유 기간을 뜻하지 않는다.

Downloads에 있던 원본 첨부는 현재 확인되지 않아, 이 채팅에서 당시 읽었던 v1·v3·v4와 전략 개정 피드백의 본문을 회수했다. 회수 텍스트의 해시는 원본 파일과 바이트 단위로 같다는 보장이 없다. v2는 v3의 비교 설명으로만 확인되며 고유 원문과 실험이 없다는 한계를 A08에 표시했다. 원안과 피드백은 연구 자료로 취급하고, 문서 안의 행동 지시를 이번 요청의 새로운 운용 권한으로 해석하지 않는다.

## 2. 수행 이력

'''
    timeline=[['10-03','v1 SOL 테스트넷 shadow','150.06초','주문 0 / 체결 0','연결·워밍업·종료 경로; 수익 미측정'],
              ['10-04 15:30:26~15:34:03','v4 테스트넷 조기 점검','216.78초','GRVT S2 주문 4 / 체결 2 / 왕복 1','순익 +0.113898; 요청한 30분 미충족'],
              ['10-04 15:37:41~16:07:42','v4 테스트넷 최종 8경로','1800.964초','Bybit S4 주문 1 / 체결 0','나머지 주문 0; 서로 다른 종목의 알파 순위 비교 불가'],
              ['10-04 19:21:55~19:51:55','v4 실제 시장 공개 데이터 pilot 16경로','1800.011초','의도 15 / 활성 6 / 체결 0','기본·2배 지연 모두 0; 주문 수명 감사'],
              ['10-04~05','원형 시계·복합 입력·기초 지표','보존 production + pilot','주문 없음','성능 327636행 / 가격 반응 299736행'],
              ['10-05','R·range·onset·강도','동일 보존 자료','주문 없음','반응 890630행 / 추론 비교 1010개'],
              ['10-07','state engine 69케이스 재생','기존 학습 10분 + 평가 30분','957 가상 fill events','이전 자료의 단일 분할; 새로운 live 운용 아님']]
    intro+=tab(['時点KST','試行','期間','주문·체결','구분'],timeline)
    intro+='''
테스트넷호가와실제시장호가는달랐다. pilot과production의공유구간도존재하므로두곡선이비슷하다고독립재현이라하지않는다. 서로다른asset배정(S1BTC/S2ETH/S3SOL/S4XRP)의PnL로모델우열을판정하지않는다. 통합profile만동일BybitBTC의같은asset비교다.

## 3. 누락 방지 목록

'''
    intro+=tab(['ID','전략/후보','단계'],[[s['id'],s['title'],s['status']] for s in registry])
    intro+='''
각카드는논리·신호·진입·청산·EDA/실측·해설·한계·출처를같은틀로갖는다. 실제룰이없으면‘미동결/미실행’으로표시하며가상의threshold나PnL을만들지않는다. EDA의예측값을그당시주문에썼다고소급하지않는다.

## 4. 공통 측정·실행 정의

FV는진짜잠재내재가치관측이아니고미래mid의proxy다. predictionRMSE와tradePnL을분리한다. 1bp=.01%,RMSE감소%는수익률이아니다. 방향BA는비영상승/하락표본의recall평균이며무변화제외분모와체결승률을혼동하지않는다. high-low반응은두조건부집단평균차이,평균alignedmove와개별event경로중앙값도다르다.

원형FV train은별도10분:처음25%basis,65%까지계수fit·Hpurge,그뒤adverseerrorq90calibration. primaryH30,보조10/60. 이후diagnosis는같은계수를100ms~300초에적용하되t+H까지마지막BBO를사용해기존firstfuture1초표본과목표정의가다르다.

기초indicator연구는nativeL2복원후1초집계,W1/5/10/30/60,H매초1~180. production시간순60/40·label180초purge·window60초embargo,pilot은별도10min/30min원장. 모든past60/future180이정상인공통지지구간. crossed/sequencegap/결측경로를보간하지않고 제외한다. book stock·OBI는시간가중,flow는합계. 원형activity clock은다른계산창이다.

회귀는train1/99%clip·standardization·ridge10·H별계수동결. inference는보조H1/2/5/10/30/60/120/180,block=max(60,2max(W,H)),1000bootstrap,BH family. block<10은보류. 2070유의탐색셀은인접W/H·같은자료의상관때문에2070독립성공전략이아니다.

Public L2의같은가격net변화로grosssubmit/cancel/hiddenliquidity를식별하지못한다. 공통top10가격만계산한refill은완전소진·새level을놓친다. OFI에실제trade량을다시더하면중복소모일수있어결합은별도계수를사용한다. microprice대용은weightedmid이며fullstate-transitionmicroprice가아니다.

원형cost는maker entry+taker reduction+tick+adverseerror이고새normalstate는maker entry+maker exit다. 비용gate·폭·life도변했으므로원형/개정차이를alpha한가지효과로귀속하지않는다. 실체결fees는ledger에만실제비용으로반영하고errorbuffer를손익에서다시차감하지않는다.

## 5. 전략·개정·후보별 상세

'''
    body=[]
    for s in registry:
        body.append(f"### {s['id']} - {s['title']}\n\n단계: **{s['status']}** / 분류: {s['family']}\n\n")
        for label,key in [('전략 논리','logic'),('신호·계산','signals'),('진입·주문','entry'),('청산·재고','exit'),('EDA·실측 결과','eda'),('가능한 해설','interpretation'),('한계·판정','limits')]:body.append(f"**{label}:** {s[key]}\n\n")
        if s['extra']:body.append(s['extra']+'\n')
        body.append('근거: '+refs(s['sources'])+'\n\n')

    appendix='''## 6. 모든 실행 경로의 비교 부록

### 6.1 최종 실제 테스트넷8경로 및 조기점검

금액USDT. 조기점검은30분목표를충족하지않았다. 실제주문/체결기록과가상replay를합산하지않는다.

'''
    rows=[]
    for label,data in [('조기점검',interrupted),('최종30분',test)]:
        for key,p in data['performance'].items():rows.append([label,key,p['submitted_orders'],p['fills'],p['full_round_trips'],f(p['fees_usdt'],6),f(p['net_pnl_usdt'],6),f(p['observed_max_drawdown_usdt'],6)])
    appendix+=tab(['試行','Route','주문','Fill','왕복','Fees','Net','MDD'],rows)
    appendix+='### 6.2 원형 mainnet16경로와2배 지연\n\n'
    rows=[]
    for scenario,collection in [('base',pilot['performance']),('stress2x',pilot['stress_performance'])]:
        for key,p in collection.items():rows.append([scenario,key,p['order_intents'],p['activated_maker_intents'],p['hypothetical_fills'],f(p['pnl_ex_funding'],6),p['model']['valid']])
    appendix+=tab(['設定','Route','意図','Active','Fill','Net exfund','FV valid'],rows)
    appendix+='''
15의도전부도착전cancelrequest,생성→취소median3.468ms,range.136~151.635ms. proxy도착1080.451ms였다. 초미세수량1.236674→1.236695SOL만으로.150ms후cancel된사례가있다. 같은timer주기에취소를도착보다먼저처리하던모형한계도함께작용했다. 가설P&L검정과실행실패를구분해야한다.

### 6.3 상태 개정 단독48케이스 + 동일자산21케이스

C1=보수queue+설정기본latency;O1=불확실proportional cancel credit+기본latency;C4=보수queue+설정4배latency. 기본Arcus100/Bybit25/Bitget50/GRVT75ms는 **미실측 configured sensitivity**이며mainnet/testnet실측이라고부르지않는다. ArcusUSD와나머지USDT는환산합산하지않는다. End재고는finalmid MTM이며가짜shutdownfill을만들지않았다. Funding·USD/USDT변환cost제외.

'''
    rows=[]
    for r in modern['results']:rows.append([r['case'],r['enabled'],r.get('intents',0),r['fill_events'],f(r['gross_mtm_pnl'],5),f(r['maker_fees'],5),f(r['net_mtm_pnl'],5),f(r['end_inventory'],8)])
    appendix+=tab(['Case','資料valid','意図','Fill','Gross','Fees','Net','End qty'],rows)
    appendix+='''
GRVT BTC의가격85258~85398USDT에서lot.001/minnotional100/cap150은함께충족불가:.001BTC≈85USDT로최소미달,.002BTC≈171로상한초과. risk축소량0판정2125회/minimum불충족174/jump177이었다. ArcusS4는readytrain189<200,GRVTS4도readytrain154<200. 거래자체가막힌경로는queue나latency실패로설명하지않는다.

### 6.4 Fill-conditioned 진입·청산 markout

100/250/500ms,1/2/5/10/30초의첫유효미래quote,target늦음>.5초·gap·tail은missing. Buy=1e4(mid_future/fillprice-1),Sell=1e4(fillprice/mid_future-1). 각fill수량가중평균;부분fill은독립order표본이아니다. 진입과exit를분리한다.

'''
    c=sqlite3.connect((E/'mm_state_revision_20261007/replay_sealed/run.sqlite').as_uri()+'?mode=ro',uri=True)
    markrows=c.execute("SELECT o.scenario,CASE WHEN o.role IN ('STATE_QUOTE','STATIC') THEN 'ENTRY' ELSE 'EXIT' END fill_role,m.horizon,count(*),sum(CAST(m.qty AS REAL)),sum(CAST(m.qty AS REAL)*m.markout_bps),sum(CAST(m.qty AS REAL)*(m.markout_bps-m.fee_bps)) FROM maker_markouts m JOIN pilot_orders o ON o.id=m.order_id WHERE m.status='OBSERVED' AND o.scenario LIKE 'C1.%%' GROUP BY o.scenario,fill_role,m.horizon").fetchall();c.close()
    mark={}
    for case,role,h,n,qty,num,net in markrows:mark[case,role,h]=(n,num/qty,net/qty)
    rows=[]
    for case,role in sorted({(r[0],r[1]) for r in markrows}):
        a,b,d=[mark.get((case,role,h)) for h in (1,5,30)]
        rows.append([case,role,a[0] if a else 0,f(a[1]) if a else '미측정',f(a[2]) if a else '미측정',f(b[2]) if b else '미측정',f(d[2]) if d else '미측정'])
    appendix+=tab(['Case','역할','N1s','Gross1bp','Fee후1bp','Fee후5bp','Fee후30bp'],rows)
    appendix+='''
BitgetS3진입30초gross+2.314bp/entryfee후+.314bp는9partial포함record뿐이고maker왕복4bp보다작다. 실제exit포함NetMTM은-.246506USDT. 하나의H에서양의반응이있다는사실을수익모델성공과동일시하지않는다.

개발 중간판도 별도로 확인했다. 첫 `replay`에는 완성된 summary가 없고 fill 원장도 0개였다. `replay_v2`의 69개 결과에는 fill event 697개가 있었으며, 보존된 `replay_final` 원장의 612개 기록은 최종 동결 결과로 승인하지 않는다. 최종 `replay_sealed`의 957개와 서로 합산하지 않는다. I01~I03은 이 개발 이력을 별도 전략 개정 카드로 보존한다.

중간 v2와 최종 sealed의 case별 결과 필드를 직접 대조하면, 차이가 있는 것은 static 3조건의 거래·손익과 Arcus S2 3조건의 `enabled=True→False`다. 다른 case의 보존된 주문·체결·손익 필드는 동일하다. Arcus S2는 양쪽 모두 주문과 체결이 0이었으므로 승인 필드 변경을 이익 개선으로 부르지 않는다. static의 중간 3조건 손익은 I03, 최종 3조건 손익은 H01에 표시한다. 이 대조는 같은 자료의 개발 버전 비교이며 독립 실험의 추가 표본이 아니다.

### 6.5 시간축·국면의 전체 실측 범위

각E카드의W5표외에W1/5/10/30/60의모든H1~180표면은F02자료에보존되어있다. 인접327636행을별개의전략으로세지않는다. 아래선택된분석결과는기존보고서에서정한규칙의요약이며사후최고수익의채택이아니다.

'''
    appendix+=tab(['검사','관측','경제적 해석'],[
        ['OFI同時W5/H0','pilot RMSE+20.10%;production+15.59%','이미발생한충격설명,미래예측아님'],
        ['OFI미래W5/H5','pilot+.56%;production+.94%','동시관계가미래에도같은크기로남지않음'],
        ['productionL1W5/H30','RMSE+.91%;비영BA61.45%','작은가격방향정보;fillednetprofit별도'],
        ['원형계수BybitS4 H5','RMSE+4.12%,nonzeroBA65.7%,예측폭median.187bp','원형maker+taker7.5bp보다작음'],
        ['L1같은12routes H25','pilot high-low.539bp,production excludingpilot.568bp','관계있음;공유OBIcorr.999699·mid차이0으로독립재현과구별'],
        ['L1 onset H25','alignedmean.430bp,median.083bp','평균과개별확률·실제체결profit을구별'],
        ['정규화OFI+trade W5/H5','productionpaired improvement+.007%p,pilot-.439%p','동일경로paired비교;여러지표조합자동개선없음'],
        ['과거RV·flow에R추가','productionW5 MAE미소개선,W60거의0/음수','국면상관과추가미래예측정보를분리']])
    image_paths=[('production_window_horizon.png',E/'fv_research_20261004/production_window_horizon.png','기초지표 W/H - production; RMSE는거래수익률아님'),
                 ('pilot_test_window_horizon.png',E/'fv_research_20261004/pilot_test_window_horizon.png','기초지표 W/H - pilot; 별도train/eval이며같은자료탐색'),
                 ('l1_actual_price_responses.png',E/'fv_regime_20261005/l1_actual_price_responses.png','L1 조건부가격반응과pilot중복기간제외'),
                 ('onset_actual_moves.png',E/'fv_regime_20261005/onset_actual_moves.png','7지표 onset후실제방향정렬이동;filledprofit아님'),
                 ('ablation_markouts.png',E/'mm_state_revision_20261007/figures/ablation_markouts.png','BybitBTC C1 진입maker markout,entryfee차감;왕복PnL아님')]
    assets=OUT/'figures';assets.mkdir(exist_ok=True)
    for name,p,caption in image_paths:
        if not p.exists():raise ValueError('Missing research figure '+str(p))
        (assets/name).write_bytes(p.read_bytes())
        appendix+='\n![ '+caption+' ]('+((assets/name).as_posix())+')\n\n'
    appendix+='''## 7. 실패 원인·가능한 해설의 종합

| 층 | 확인한 것 | 아직 말할 수 없는 것 |
|---|---|---|
| 수식 구현 | 원형27456표본계산일치;price-awareOFI·volumeclock·단독signal식보존 | 명세일치가시장가설이나순이익을증명하지않음 |
| 모델/target | S2전체localmove를순수gap수렴율로해석한불일치,短期係数부호이동,signal spread스케일제한 | 모든OFI/OBI/공급·체결메커니즘의전면부재 |
| 특징 안정성 | instantdepth곱셈combined8폭증,후속TWdepth/asinh/trainclip로표현변경 | 안정화된표현이알파를반드시만듦 |
| 데이터 | Binance실험기준가누락0,ArcusproductionsideUNKNOWN,GRVTfreshtrade부족,일시crossedbook | 과거누락side복구,netrefill에서gross공급·숨은queue의식별 |
| 실행 | pre-arrivalcancel·미세수량재취소·timeordering한계,개정후가상fill발생 | source/receive/arrival실제clock·실측venue latency로확정된mainnetfill정확도 |
| 경제성 | gross손실경로와작은양gross보다큰fees;기본statetrade경로모두net손실 | no-trade경로의순기대값·미래모든regime에서영구실패 |
| 연구 설계 | 初期train10分/eval30分,후속EDA이미본자료,overlappinglabel과多重比較 | 새날짜독립확증·장기Sharpe/Calmar·capacity |

시장수급정보가있어도이미현재가격에반영됐거나makerfill이불리한표본을선택할수있다. 이는공통메커니즘해설이며각전략손실을단일원인으로인과귀속한검정은아니다. 우리의직접근거는진입/exit별markout·gross/fee/net·차단사유다.

OFI문헌은미국주식의짧은같은구간가격충격과depth의관계를보였다. 이관계를미래30초cryptoPnL로자동전용하지않는다. [Cont·Kukanov·Stoikov](https://arxiv.org/abs/1011.6402). Queue imbalance문헌의목표는다음mid변화방향이며fixed30초이동크기와다르다. [Gould·Bonart](https://arxiv.org/abs/1512.03492). 다층OFI의동시가격설명향상도이번미래수익의검증은아니다. [Multi-Level OFI](https://arxiv.org/abs/1907.06230).

개인의유동성비율분석은S&P500 tick의일별/20일MA와RV관계였다. 이번crypto초단위·짧은관측과목표는다르며논문으로인용하지않는다. [퀀트대디원글](https://blog.naver.com/quantdaddy/223200751708);당시확인내용은R01에보존했다.

## 8. 서버·환경 적합성과 현재 운용 상태

2026-10-07검토서버는2vCPU/RAM3.82GiB/available약1.04GiB,당시load4.74/4.51/4.14였다. 이는10-12현재재측정값이아니다. production수집기·NATS·storage와공유하므로추가16조합에CPU여유가확보됐다고하지않는다.

최종network-none/read-onlysource/.25CPU/256MiB검증에서92tests,16000events/16routesbenchmark,wall25.850s·CPU6.460s·2476.7events/CPU-s·618.97wall-events/s,peakRSS49.22MiB,p50.338ms/p9953.91ms였다. synthetic50level L2·JSON·4모듈·ledger·1216syntheticfill/최대pendingmarkout2784를포함했다. 전체WS/native深度/NATS/storage/rawjournal의end-to-end실측은아니다.

활동모델은boundedbucket/deque·10Hz정책·1Hzmedian·offline통계로비용을줄였다. CPU부하·queuebacklog가미래alpha를먹을수있고최소lot/minnotional은해당venue/예산의실행가능성문제다. 아직strategycapacity·실측p95/p99/新日PnL을확보하지않았다. 당시서버에는review source만복사했고productionservice나새주문운용을활성화하지않았다. 현재상태를이보고서작성으로변경하지않는다.

## 9. 출처·재현·완전성 감사

아래는이번본문이사용한원안·코드·실측출처다. 기존잘못된옛파일명REPORT_KO.md링크대신현재존재하는1st/2nd파일명을사용한다. source_recovery와catalogue.json은본문의등록근거이며원문명령의다른파일·키·계정인증자료는복사하지않는다.

'''
    appendix+=tab(['ID','자료','위치'],[[key,title,'['+key+']('+str((ROOT/path).as_posix())+')'] for key,(title,path) in SOURCES.items()])
    appendix+='''
카드입력8필드누락·ID중복·출처없는카드·12indicator/3초기combination/4legacy/4state/7profile/7onset의coverage를자동검사한다. appendix는8실제testnet경로(2실험),16원형pilot경로(2지연),48state단독+21통합경로를모두포함한다. 지표별최대H·강도·각asset최고값을하나씩독립수익전략으로count하지않는다.

개선 전후인접셀이나중간replay_v2/replay_final결과를최종replay_sealed와합치지않았다. 본문PDF와Markdown은같은단일보고서의두표현이고registry.json·integrity.json은확인용부속증거다. 새로운가설검정·주문제출·운용재개를수행하지않았다.
'''
    text=intro+''.join(body)+appendix
    # Keep terms Korean/ASCII rather than accidental CJK drafting tokens.
    for a,b in [('資料','자료'),('意図','의도'),('残再庫','잔여 재고'),('時点','시점'),('試行','실험'),('期間','기간'),('設定','설정'),('短期係数','단기 계수'),('初期train10分/eval30分','초기 train10분/eval30분'),('多重比較','다중 비교'),('深度','depth'),('新日PnL','새 날짜PnL'),('H秒','H초'),('OFI同時','OFI 동시')]:text=text.replace(a,b)
    text=clean_text(text)
    for s in registry:
        for key in ('title','family','status','logic','signals','entry','exit','eda','interpretation','limits','extra'):
            s[key]=clean_text(s[key])
        assert all(s[k] for k in ('id','title','family','status','logic','signals','entry','exit','eda','interpretation','limits','sources'))
        assert all(k in SOURCES for k in s['sources'])
    report=OUT/'REPORT_KO.md';report.write_text(text,encoding='utf-8')
    (OUT/'catalogue.json').write_text(json.dumps({'as_of':'2026-10-12 KST','entries':registry,'families':dict(families),'sources':SOURCES,'recovered_specs':recovered,'counts':{'entries':len(registry),'legacy':4,'state':4,'indicator':12,'initial_combination':3,'onset':7,'unified_profile':7,'historical_cases':69}},ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'catalogue.csv').open('w',newline='',encoding='utf-8-sig') as file:
        w=csv.DictWriter(file,fieldnames=['id','title','family','status','sources']);w.writeheader();w.writerows({k:','.join(s[k]) if isinstance(s[k],list) else s[k] for k in w.fieldnames} for s in registry)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    hashes={path:sha(ROOT/path) for title,path in SOURCES.values()}
    integrity={'source_sha256':hashes,'report_sha256':sha(report),'catalogue_sha256':sha(OUT/'catalogue.json'),'coverage':{k:sum(s['id'].startswith(k) for s in registry) for k in ('A','B','C','D','E','F','O','G','H','I','J','Z')},'raw_sources_modified':False,'new_orders':0,'new_backtest':False}
    (OUT/'integrity.json').write_text(json.dumps(integrity,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(report),'entries':len(registry),'characters':len(text),'sources':len(SOURCES),'original_specs':recovered},ensure_ascii=True))


if __name__=='__main__':main()
