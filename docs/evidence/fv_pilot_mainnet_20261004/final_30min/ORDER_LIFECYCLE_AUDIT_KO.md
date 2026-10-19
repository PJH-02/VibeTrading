# 주문 의도 15건의 수명 재검토

2026-10-04의 기존 30분 원본을 읽기 전용으로 분석했다. 새 운용이나 실제 주문 제출, 모델 재보정은 하지 않았다.

모든 주문에서 가정 도착 전 취소 요청이 발생했다. 생성→취소 요청 중앙값은 3.468ms, 범위는 0.136–151.635ms였다. Arcus·Bitget에 적용한 도착 지연은 둘 다 1,080.451ms의 대용값이다.

| 의도 | 거래소·전략 | 최종 상태 | 생성→취소 요청 ms | 기록된 활성 시간 ms | 취소 요청의 최근 결정 상태 | 활성 중 해당 가격까지 도달한 반대 체결 |
|---|---|---|---:|---:|---|---:|
| paper-measured-1 | arcus:S3 | POST_ONLY_REJECTED | 1.392 | 활성 기록 없음 | insufficient_trade_volume_history | 0 |
| paper-measured-2 | arcus:S3 | CANCELLED | 3.468 | 3.839 | edge_below_cost | 0 |
| paper-measured-3 | arcus:S3 | CANCELLED | 6.992 | 활성 기록 없음 | insufficient_trade_volume_history | 0 |
| paper-measured-4 | arcus:S3 | CANCELLED | 0.245 | 활성 기록 없음 | edge_below_cost | 0 |
| paper-measured-5 | arcus:S3 | CANCELLED | 0.150 | 활성 기록 없음 | eligible | 0 |
| paper-measured-6 | arcus:S3 | CANCELLED | 2.084 | 활성 기록 없음 | edge_below_cost | 0 |
| paper-measured-7 | bitget:S1 | CANCELLED | 151.635 | 152.487 | edge_below_cost | 0 |
| paper-measured-8 | arcus:S3 | CANCELLED | 0.136 | 활성 기록 없음 | edge_below_cost | 0 |
| paper-measured-9 | arcus:S3 | CANCELLED | 8.302 | 13.880 | edge_below_cost | 0 |
| paper-measured-10 | arcus:S3 | CANCELLED | 25.634 | 26.095 | edge_below_cost | 0 |
| paper-measured-11 | arcus:S3 | POST_ONLY_REJECTED | 1.520 | 활성 기록 없음 | eligible | 0 |
| paper-measured-12 | arcus:S3 | CANCELLED | 21.201 | 20.964 | edge_below_cost | 0 |
| paper-measured-13 | arcus:S3 | CANCELLED | 4.899 | 활성 기록 없음 | eligible | 0 |
| paper-measured-14 | arcus:S3 | CANCELLED | 24.639 | 23.965 | edge_below_cost | 0 |
| paper-measured-15 | arcus:S3 | CANCELLED | 0.498 | 활성 기록 없음 | edge_below_cost | 0 |

최종 기록은 PostOnly 거절 2건, 활성 기록 없이 취소 7건, 활성 후 취소 6건이다. 마지막 결정 기록은 비용 미달 10건, 거래 활동량 부족 2건, 여전히 eligible인 가격·수량 변경 3건으로 나뉜다. 취소 사유는 독립 필드가 없어서 요청 시점의 최근 결정과 코드 비교로 재구성했다.

Arcus에서 활성화된 5건의 기록된 유지 시간은 3.839–26.095ms였으며 그 동안 수신한 native 거래는 0건이었다. Bitget의 활성 1건은 152.487ms 유지됐고 native 거래 13건이 있었지만 반대 방향으로 주문 가격까지 도달한 체결은 0건이었다. 따라서 이번 6건은 대기열을 소진할 수 없었다.

가격뿐 아니라 아주 작은 수량 변화도 즉시 취소를 일으킨다. 예를 들어 paper-measured-5는 매수 가격 121.280과 eligible 상태가 유지됐지만 수량이 1.236674→1.236695 SOL로 변해 생성 0.150ms 후 취소 요청이 생겼다. 이는 단순한 신호 소멸과 구분해야 하는 현재 운용 로직의 민감성이다.

시뮬레이터는 도착과 취소 효력이 같은 처리 주기 안에 지난 경우 취소부터 처리한다. 활성 기록 없는 7건을 실제 거래소에서 전혀 활성화되지 않았을 것이라는 증거로 해석하지 않는다. 더 짧은 실제 유지 구간의 체결 여부를 원본으로 확정할 수 없으며, 현재 결과에는 이 시간 처리 방식과 표시 잔량 전부를 앞선 queue로 두는 체결 가정이 함께 작용한다.

예측 평가와 체결 평가는 구분한다. 현재 30초 동결 FV의 RMSE는 12개 유효 모델 중 6개가 mid 유지보다 작고 6개가 컸다. 4개는 배포 가능한 FV가 없었다. 주 검정 BH q<0.05 통과는 0개이며, 체결 0건으로 체결 조건부 수익성은 미측정이다. 작은 예측 개선과 체결 손익 부재를 곧바로 모든 전략의 알파 부재로 동일시할 수 없다.

상세 시각·수량·후보 가격·호가 및 원본 대조는 [order_lifecycle_audit.json](order_lifecycle_audit.json)에 남겼다.
