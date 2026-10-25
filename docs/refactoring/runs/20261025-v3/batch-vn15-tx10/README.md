# VN15 → TX09 / TX10 로컬 수락 — 2026-10-25

요청한 세 티켓만 수행했다. Codex 명세/보안 경계/수락, OpenCode `opencode/deepseek-v4.1-flash-free` 구현/테스트, Codex 독립 실행 구조다. FreeLLMAPI 설정/인증/기존 model profile은 보존했다. upstream 가중치 실체는 UNKNOWN이다. TX10은 선행 TX01이 이미 충족된 독립 가지로 격리 snapshot에서 병행하고 마지막에 통합했다.

| 티켓 | 완료 범위 | 독립 scoped PASS | repair 위임 |
|---|---|---|---|
| VN15 | Reuse existing bounded readonly Paper ledger projection in the existing local dashboard cold scan; separate PAPER_SIMULATION, UNKNOWN/unreconciled and unavailable marks/timestamps from actual venue evidence. | 77 | 0 |
| TX09 | Reuse existing Telegram numeric ACL/outbox and opt-in authoritative owner command bridge; add bounded cold Paper queries and existing local feed snapshot reads without affecting trade/status legacy or submit path. | 106 | 0 |
| TX10 | Optional bounded data_manifest provenance in existing explicit strategy/Paper registration; validate manifest and referenced object hashes and availability before import/claims; preserve existing export/Ncloud clients unchanged. | 146 | 0 |

고정 source `b74e22d443aff428e7971d1695cf00ecd9553818`: **2438 PASS / 2 live-data 제외**. [전체 회귀](full-regression.json)·[입력 해시/명령](full-source-inputs.json)·[소스 범위](source-review.json)·[최종 상태 범위 검사](final-scope-status-validation.json)·[수락](acceptance.json). scoped와 전체 검사는 겹치므로 합산하지 않는다.

Paper 계좌는 실제 venue holdings가 아니다. 기존 원장을 별도 readonly connection/transaction으로 조회하며 owner version/state/lease를 변경하지 않는다. 수수료/funding/realized는 전략·통화별 원값이고 믿을 수 있는 mark/FX/원천 timestamp/heartbeat가 없으면 UNAVAILABLE/UNVERIFIED다. UNKNOWN/gaps는 UNRECONCILED다. 파일 mtime으로 liveness/freshness를 만들지 않는다. 기존 실제/Mock2 보고를 보존했다.

Telegram 새 조회는 기존 numeric ACL과 cold worker thread를 쓰며, 보고 장애/과대 응답은 제한된 UNAVAILABLE이다. /status와 /trade, 기존 opt-in /pause /halt /resume→UDS→ExecutionOwner→durable outbox의 epoch/TTL/dedup을 보존하고 재실행 효과를 막는다. 새 Paper control owner를 만들지 않는다. 외부 Telegram HTTP/account 호출은 없다.

TX10은 optional research_receipt.data_manifest를 module import/claim 전에 검증한다. 크기/개수/총byte/파일경계/체크섬/available-at를 제한하고 receipt를 기존 config/runtime identity에 보존한다. 등록 시점 provenance이며 미래 파일 불변성이나 파일시스템 전체 원자 snapshot을 주장하지 않는다. 기존 설정은 이 optional field가 없어도 동작하며 PAPER 설정만으로 live API를 활성화하지 않는다. 기존 export/Ncloud upload/download 코드와 causal history는 변경하지 않았다.

현재 **33 완료 / 10 미완료 / 8 미착수**. 로컬 계약 상태이며 live/deploy 승인이 아니다.

- **완료 (33):** VN00, VN01, VN02, VN03, VN04A, VN04B, VN05, VN06, VN07, VN08, VN09, VN10, VN11, VN12, VN13, VN14A, VN14B, VN15, VN16, TX00, TX01, TX02A, TX02B, TX02, TX04A, TX04B, TX04, TX05, TX07A, TX08A, TX08, TX09, TX10.
- **미완료 (10):** TX03A, TX03B, TX03, TX06A, TX06B, TX06C, TX06D, TX06E, TX06, TX08B.
- **미착수 (8):** VN17, VN18, VN19, VN20, TX07B, TX07, TX11, TX12.

Codex 소규모 검토 수정: VN15의 position 필드 순서/필수 strategy id·계좌별 read-view id 연결, TX09의 누락 원장/계좌 gap 표시, TX10의 상대 경로 별칭 중복 거절. OpenCode 원본 실행과 수정 후 Codex 독립 실행을 각 owner-review receipt와 구분해 기록한다.

기존 완료30개/비선택48개 객체·profiles/permissions/tooling·DAG 간선을 보존했다. 다음 VN17 또는 네 번째 티켓은 착수하지 않았다. 로컬 commit/원본 적용만 수행하고 원격 push는 하지 않는다. 사용자 dirty174건 보존은 별도 적용 receipt에 기록한다.

조회 활성화는 기존 실행 설정의 `PAPER_PROJECTION_DATABASES`에 Paper 원장 절대 경로 JSON 배열을 명시한다(최대16개). 미설정/누락 원장은 UNAVAILABLE이며 자동 계좌 탐색이나 live 연결은 없다. `/feeds`는 기존 `LOCAL_MARKET_SNAPSHOT_FILE`/`LOCAL_SNAPSHOT_BUDGETS`를 재사용한다. manifest 연결의 필드/상한은 [TX10 bounded ticket](../../../../tickets/TX10-existing-export-registration-link.md)에 있다. `available_at_ns`는 명시적으로 제공한 provenance 시각과 현재/행 시각을 비교하는 계약이며, 역사적 데이터 가용성을 별도 외부 기관이 증명한 것으로 해석하지 않는다.

원본 작업폴더에 로컬 적용 완료: `0f56fccf4b65d63b2b62cd89f34906b421e57579` (검증 source `b74e22d443aff428e7971d1695cf00ecd9553818`). 사용자 dirty174건·실제 수정 byte·삭제/미추적 문서·줄바꿈을 보존했다. [적용 기록](original-application.json). 이후 메타데이터 검사/적용 receipt만 별도 로컬 commit으로 갱신한다. 원격 push는 수행하지 않았다.
