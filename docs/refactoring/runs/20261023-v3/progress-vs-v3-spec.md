# v3 개발자 티켓 명세 대비 진행 현황 — 2026-10-23

기준 문서: `docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md`.
검토 기준 HEAD: `e77ead950ce1229271ce468946035f296746de32`.
최신 패키징 제품 소스 커밋: `bf75b1c5d259433fc0aa2d9b43764855837b3f68`.
이 보고서는 기존 구현·수락 증거를 대조한 결과이며, 다른 구현 티켓을 시작하거나 새 제품 시험을 실행하지 않았다.

## 원계획 R/O 티켓

| 티켓 | 원명세 기준 현재 판정 | 완료된 범위 / 남은 범위 |
|---|---|---|
| R00 | 완료 | 저장소·진입점·동적 import·소유권 기준선, 보호 대상·UNKNOWN 분류. `vnext/repo_inventory.json` 수락 기록. |
| R01 | 완료 | 원본 JSON 권위를 유지하는 DAG 상태 색인, 작업 가이드, 상태 문서. 독립 시험 20개 수락 기록. |
| R02 | 완료 | README·아키텍처·상태 문서와 보존/격리 인덱스 정리. 당시 링크 110개와 안전한 CLI 도움말 검증. 삭제·운영 변경 없음. |
| R03 | 부분 완료 | R03A: 140개 모듈 src 이동, bytes/AST 차이0. R03B1: setuptools·pytest 경로·핵심 Docker 패키징 완료. R03B2 기본 파일 경로와 R03C path-map/배포·릴리스 경로 소비자는 미착수. 전체 R03 수락은 아직 안 됨. |
| R04 | 미착수 | 활성·레거시 소유권, 단일 정식 전략 등록/로딩 경로 통합 대기. |
| R05 | 미착수 | C3/KP/KD 순수 계산 추출과 ETF Runtime 분리 대기. |
| R06 | 미착수 | 증거 기반 레거시·중복 도구 격리 대기. |
| O00 | 완료 | 6개 모델 경로의 실제 READ/WRITE/pytest 및 의도된 거절 동작 검증·수락. 현재 시점 공급자 가용성을 다시 실시간 검증했다는 뜻은 아님. |
| O01 | 기반 구현 수락 후 보완 필요 | Task packet·scope/test gate, TTL·중단·중복 방지, 실제 두 모델 patch 재현 등 기존 수락 증거 있음. 이후 R03B1 실제 저장소 실행에서 unchanged `.env.example`을 baseline scope validator가 거절한 오류 발견. 현재 REWORK_REQUIRED로 정정. R03 path-map 소비 연동도 R03C에 남음. |

원계획의 R00–R06/O00–O01은 **9개**다. R03A/B1/B2/C는 R03을 실행하기 위해 나눈 하위 단위이므로 원계획 완료 수를 늘려 세지 않는다. 현재 원계획 전체 수락 조건이 닫힌 것은 R00/R01/R02/O00 네 개이며, O01은 과거 수락을 취소·삭제하지 않고 후속 발견에 따라 보완 필요로 표시한다.

## VN/TX: 기존 완료와 이번 추가 작업

기존 전체 51개 상태는 로컬 완료14 / 부분23 / 미착수14로 유지된다. 집계 티켓과 하위 티켓이 섞인 개수이므로 이를 전체 프로젝트의 완료율로 해석하지 않는다.

이미 로컬 수락된14개:
`VN00, VN01, VN02, VN03, VN04A, VN04B, VN05, VN06, VN10, VN11, VN12, VN14A, VN14B, VN16`.
이들은 v3 계획 이전의 완료 증거를 재사용했으며, 이번 작업에서 다시 구현한 것이 아니다.

이번에 닫은 부분 기능:

| 부모 티켓 | 추가 수락된 기능 | 실제 증거 | 부모 티켓의 남은 범위 |
|---|---|---|---|
| VN07 | checkpoint 복구 중 아직 열지 않은 spool segment의 파일 교체·손상 검출과 identity 증명 강화 | `00b47bffc66691015be53c663258db0b52826790`, 독립 시험183개 및 제한 범위 독립 리뷰 | ETF/Paper account·strategy·pending action 동일 복구 경계 미완료 |
| TX00 | TOSS DATA_ONLY: 주문 및 비GET 데이터 요청을 인증/전송 전에 거절. OAuth 인증 갱신 의미는 유지 | `1efd0f3664542ee795d8c89960951a24998ab223`, 독립 시험43개 및 제한 범위 독립 리뷰 | 5개 Perp Venue + KIS/Kiwoom/TOSS의 상품·환경·계좌별 capability registry 전체 확정 미완료 |

부분23개:
`VN07, VN13, TX00, TX01, TX02A, TX02B, TX02, TX03A, TX03B, TX03, TX04A, TX04B, TX04, TX05, TX06A, TX06B, TX06C, TX06D, TX06E, TX06, TX08A, TX08B, TX08`.

미착수14개:
`VN08, VN09, VN15, VN17, VN18, VN19, VN20, TX07A, TX07B, TX07, TX09, TX10, TX11, TX12`.
VN20의 실제 운영 전환·삭제는 외부 승인 후 작업이다.

## 검증 범위와 현재 제한

- 이동 전 동일 소스에서 기존 비실거래 시험1711개 + 추가 검토 시험6개 통과, 실데이터 시험2개 제외. 이 결과는 `b778955d83aef5ac38aeef33d57233a39eab1783` 기준의 **이동 전** 통합 증거다.
- R03B1 최종 소스에서는 시험4개, wheel/editable 빌드·설치, 저장소 밖 import/CLI6개, Docker 이미지3종의 import/CLI9개가 통과했다. 과거 flat 패키지를 넣은 베이스의 앱 재사용·크립토 오버레이도 확인했다.
- 새 src 배치 전체의 Linux 회귀, 기본 resource 경로, Compose mount, 릴리스·P02 호환 및 path-map consumer 전체 수락은 R03의 미완료 범위다. R03B1의 PASS를 R03 전체 PASS로 확장하지 않는다.
- O01 baseline validator 오류는 `r03b1/repair1-adapter-receipt.json`에 실패로 보존되어 있다. 해당 티켓의 worker pytest4개/exit0과 실행 어댑터의 실패를 구분한다. 이번 현황 정리에서는 어댑터 코드를 수정하지 않았다.
- 실제 계좌·서버·30분 초과 soak/72시간 운영·최종 2vCPU/메모리 성능 수락은 아직 증명하지 않았다. 배포·실거래·main merge는 수행하지 않는다.

## 커밋·발행 범위

기존 완료 및 수락된 하위 기능은 `codex/v3-local-candidate`에 모두 커밋되어 있다. 최신 기존 체크포인트는 `e77ead9`, 이 보고서와 O01 상태 정정은 별도 문서/메타데이터 커밋으로 남긴다. 이 후보에는 과거 미완료 기능의 기준선도 포함되어 있으며, 그것들을 완료로 표시하지 않는다.

원본 Windows 작업 폴더의 기존 미커밋 변경·삭제·untracked 파일은 완료 검증된 이번 후보와 구분하여 그대로 둔다. 발행 대상은 후보 브랜치의 누적 커밋이며, 원본 폴더의 미검증 변경을 새로 stage하지 않는다.

구현 재개 제어는 `STOPPED_AT_USER_REQUEST`, `start_next_ticket=false`, `stop_after_ticket=R03B1`을 유지한다. 이번 요청으로 승인된 추가 동작은 현황 보고·완료 커밋 정리·push이며 후속 기능 구현은 아니다.
