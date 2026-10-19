"""Whitespace-only Korean prose formatting; numbers, links and formula atoms stay intact."""
from pathlib import Path
import re
import sys

_kiwi = None


def polish(text):
    global _kiwi
    if _kiwi is None:
        local = Path(__file__).resolve().parents[1] / 'tmp/pdfs/report_spacing_runtime'
        if local.exists():
            sys.path.insert(0, str(local))
        from kiwipiepy import Kiwi
        _kiwi = Kiwi(num_workers=2)
        for word in ('작성일','테스트넷','백테스트','대조군','체결','해시','의도','수명','원안','공급충격','서비스량','매수량',
                     '매도량','미측정','미운용','양방향','고유','전신','기대값','실측','구현','미확정','순유동성','재공급',
                     '공급량','체결량','워밍업','재고량','잔여량','수익률','무변화','비중첩','강도','순이익','순손익','신호',
                     '보충률','잔여물량','부호','방향성','체결수량','공급부족','재가격화','수량한도','종목별','손절','재학습',
                     '청산률','수렴율','반사실','통계적','경제적','미실행','미완료','명목비율','표준화','계수','부속','원형'):
            _kiwi.add_user_word(word, 'NNG', 5.0)
    links = []
    lookup = {}

    def hide(match):
        value = match.group(0)
        if value in lookup:
            return lookup[value]
        key = chr(0xE000 + len(links))
        links.append((key, value))
        lookup[value] = key
        return key

    protected = re.sub(r'!?\[[^\]]*\]\([^\n)]*\)|`[^`\n]+`|[A-Za-z_][A-Za-z0-9_.-]*', hide, text)
    lines = protected.splitlines()
    indices = [i for i, line in enumerate(lines) if re.search('[가-힣]', line)]
    for i, spaced in zip(indices, _kiwi.space((lines[i] for i in indices), reset_whitespace=False)):
        if re.sub(r'\s', '', lines[i]) != re.sub(r'\s', '', spaced):
            raise ValueError('Formatter changed non-whitespace content')
        lines[i] = spaced
    result = '\n'.join(lines) + ('\n' if text.endswith('\n') else '')
    result = re.sub(r'\*\*\s*(.*?)\s*\*\*', lambda m: '**'+m.group(1).strip()+'**', result)
    result = re.sub(r'\s*·\s*', '·', result)
    result = re.sub(r'(?<=\d)\s+(?=%)', '', result)
    for a,b in [('체 결','체결'),('의 도','의도'),('수 명','수명'),('해 시','해시'),('원 안','원안'),('고 유','고유'),
                ('실 측','실측'),('구 현','구현'),('양 수','양수'),('음 수','음수'),('전 신','전신'),('테스트 넷','테스트넷'),
                ('백 테스트','백테스트'),('대조 군','대조군'),('파일 명','파일명'),('읽어야한다','읽어야 한다'),('수익미 측정','수익 미측정')]:
        result=result.replace(a,b)
    # Preserve conventional research vocabulary when the tokenizer over-splits it.
    joined=('최종 동일 절편 개선 계수 신호 가격 규모 오차 공급 수급 청산 회귀 분석 결과 자료 시간 미래 과거 관측 현재 실제 '
            '모형 모델 표본 조건부 비교 평균 중앙값 동결 정상성 중심점 동시 깊이 단위 재고 손익 순익 비용 진입 주문 수량 '
            '측정 유효 누적 소진 흡수 공격 체결 예측 위험 상한 하한 수명 데이터 상태 차단 독립 반응 가설 전신 방향 '
            '강도 단계 원안 고유 실측 구현 절댓값 강화 순위 신규 한계 기준 보유 부호 잔량 변형 변동성 진행 정책 '
            '추가 성과 경제적 통계적 시간축 실험 미측정 미확인 미동결 미실행 미완료 미운용 재공급 매수량 매도량 '
            '서비스량 공급량 체결량 대조군 테스트넷 백테스트 소수 연속 반전 정보 예약 복귀 유동성 국면 실제 규칙 '
            '입력 계산 부분 체결률 수렴율 소액 중간 당시 보수적 양방향 양수 음수 대칭 확대 해시 전량 선언 적격 '
            '외삽 일시적 분모 감소 분류 예제 종료 경로 시스템').split()
    for word in joined:
        result=re.sub(r'[ \t]*'.join(map(re.escape,word)),word,result)
    result=result.replace('가격규모오차개선','가격 규모 오차 개선').replace('모든 W 와매초 H','모든 W와 매초 H')
    for key, value in links:
        result = result.replace(key, value)
    result=re.sub(r'(?<=[A-Za-z0-9]) +((?:의|은|는|을|를|가|와|과|로|만|별|에|에서|이고|이며|보다))(?=[\s,.·])',r'\1',result)
    result=result.replace('모든 W 와매초 H','모든 W와 매초 H')
    return result
