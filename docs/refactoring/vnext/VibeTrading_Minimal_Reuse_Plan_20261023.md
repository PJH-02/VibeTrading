# VibeTrading 최소 재사용 계획 — 2026-10-23

> **지위:** 이 문서는 `docs/refactoring/vnext/reuse_audit/tech_lead_scope_decisions.json`과 `revised_dag.json`을 그대로 반영한 **결정·잔여작업 계획**이다. 제품 코드를 쓰지 않으며, 어떤 작업도 이 문서로 시작되지 않는다. 범위 충돌 시 이 문서와 위 두 JSON이 v3의 광범위한 "범용 Trading Platform 완성" 문구보다 우선한다.
> **기준 SHA:** `4977ab509490173e312183f32d47f862d94ee0bf`. 동결 HEAD 및 함수 정의를 Git/AST로 대조했다. **정적 감사는 live 검증이 아니다.**
> **근거:** `crypto_runtime-response.md`, `owners_etf-response.md`, `adapters-response.md`(+`adapters-completion-response.md` 보충), `execution_reuse_matrix.md`, 기존 v3 계약·완료 VN 14개/R03–R05/O00 기록. 함수 인용은 감사 보고서의 `path:line`을 따른다.

## 1. 기준선과 한계

- 사용자가 보고한 현재 동작(ETF 주문/취소/체결/포지션/reconcile, Ncloud archive/upload/download)은 **보존 대상**이다. 이 감사는 그 동작을 새로 live 검증하거나 부정하지 않는다.
- KIS legacy `broker_factory.create_broker`는 기본적으로 `BrokerStub`를 반환하지만, 실제 ETF 런타임은 `KisCashBroker`를 직접 호출한다(`brokers가 없다`는 사실은 ETF venue 미지원 증거가 아니다). ETF는 `mock`/`mock2`만 허용하고 real route는 이 코드에 없다.
- Bybit/GRVT `fv_mm`, liquidity, probe는 **testnet 전용**이다. Binance spot ≠ Binance perp, mock ≠ testnet ≠ live, KIS mock ≠ KIS real을 혼동하지 않는다.
- `perp_execution`의 정규화·서명 조합은 **재사용 가능한 artifact/prototype**이다. 주입 `Transport`와 `normalize_private_event`는 새 live socket이 아니다. 새 범용 계층을 기존 동작보다 우선하지 않는다.
- GRVT `native_client_id` bit-width/hash 결함 주장은 **UNVERIFIED로 하향**한다. reproducer·위반 계약·선택 caller 없이 수정하지 않는다.
- 47개 파일·692개 함수의 inventory/dispositions와 422개 direct AST calls는 기계 증거이며 본 문서에서 수기 재검증하지 않았다. `direct_callers`/`function_dispositions`는 증거 링크일 뿐 승인된 작업 목록이 아니다.

## 2. 현재 실행 경로 (인용)

| 경로 | 현재 호출 사슬 | 소유/환경 |
|---|---|---|
| KIS ETF | `etf_trading.py:954 main` → `:855 run` → `:867 _run`; `:612 event` → `:387 place` → `EtfJournal.stage etf_journal.py:188` → `_submit_staged :344` → `KisCashBroker.submit_order kis_execution.py:137`; `:518 reconcile` | ETF 전용, mock/mock2 |
| KIS legacy wrapper | `services/broker_factory.py:23 create_broker` → `:55 BrokerStub`; Kiwoom 분기 `:46` | legacy/dynamic; ETF 실경로 아님 |
| Internal Paper | `strategy_runtime.py:251 process_event` → `:456 _execute` → `PaperExecution.submit paper_execution.py:98`/`cancel :154` → `PortfolioLedger.admit_order :381`/`apply_fill :485` | PAPER 전용, 외부 mutation 0 |
| Bybit liquidity | `liquidity_mm/cli.py:67 main` → `runner.py:720 execute`; submit `:299` → `exchange.py:131`; cancel `:346` → `:142`; reconcile `:369` | fixed testnet linear perp |
| Bybit/GRVT fv_mm | `scripts/run_fv_testnets.py:25` → `fv_mm/runner.py:346 run`; factory `:351`; Bybit `venues.py:76/85/88/98`, GRVT `:195/198/201/224`; private `private.py:9/35`; reconcile `:151` | testnet only |
| Bybit probe | `tests/test_crypto_perp_pipeline.py:8/112`; `bybit_execution.py:344` submit path, `:305 _reconcile`, `:419 run` | testnet; run blocks live `:422` |
| Normalized perp | `perp_execution.py:1308 make_perp_adapter` → five adapters; all need injected `Transport`; `normalize_private_event` only | test-visible composition, no live transport |
| Binance spot | `services/execution/crypto_binance.py:134/185/208`; legacy `brokers.py:70` | spot only, not perp |
| Arcus/GRVT extended | `extended_brokers.py:112 Arcus` / `:216 Grvt`; `_rpc :70`, WS `:78/227` | live-WS prototype, testnet/live gates |
| Hyperliquid | `brokers.py:241` read-only + `submit_order :260` raise; `perp_execution.py:1085/1210` SDK signing composition | read path vs signing artifact 분리 |
| Kiwoom | COM `kiwoom_broker.py:208/378/436/154`; REST data `etf_trading.py:589`, `EtfHistory :74` | Windows COM vs data-only 분리 |
| TOSS | `EtfTradingLoop.event etf_trading.py:620`(backfill); `brokers.py:285 submit_order` rejects | **DATA_ONLY** |
| Cloud | `storage_backend.py:178 archive_backend`; `NcloudStorageBackend.put_file :123`, `put_bytes :134`, `verify :143`, `download_file :165` | working Ncloud/S3, 보존 |

## 3. 6분류 규칙 (기본값 KEEP)

1. **KEEP:** 현재 동작·계약·Golden을 그대로 보존한다. 변경 요구가 없으면 손대지 않는다.
2. **REUSE:** 새 로컬 경로가 기존 함수를 호출만 한다. 기존 함수 수정·이동·rename은 하지 않는다.
3. **ADAPT:** 선택된 경로에 필요한 좁은 bridge/환경/안전 수정만 한다. 새 엔진·새 connector·새 권위를 만들지 않는다.
4. **COMPLETE_MISSING:** 선택된 전략/상품/환경의 실제 gap이 함수·caller 증거로 입증될 때만 인정한다. 단순 wrapper 메서드 부재는 증거가 아니다.
5. **REPLACE_PROVEN_DEFECT:** reproducer, 위반 계약, 실제 선택 caller가 모두 있을 때만. 현재 승인된 제품 수정은 **0건**이며 GRVT client-ID 주장은 UNVERIFIED다.
6. **RETIRE:** 계획만 가능하다. active import/dynamic loader/deploy/rollback/Golden 무영향 증명과 별도 승인 전에는 삭제하지 않는다.
- **UNKNOWN/N/A는 정직한 상태**이며 자동 신규 개발이 아니다. raw audit의 ADAPT/defect 아이디어도 자동 승인 작업이 아니다.

## 4. 중복 TX 요구 → 기존 함수

| TX 요구 | 이미 존재하는 함수/근거 | 판정 |
|---|---|---|
| TX00 capability facts | `PortfolioLedger.CAPABILITIES portfolio_ledger.py:61`, `KisCashBroker.account_params kis_execution.py:50`, `CryptoBinanceAdapter.__init__ crypto_binance.py:36` | REUSE facts; 새 registry 없음 |
| TX01 action/identity | `OrderIntent`(`etf_trading.py:423`), `ExecutionOwner.dispatch execution_owner.py:218`, `PaperExecution.submit :98` | ADAPT existing contract only |
| TX02A owner/lease | ETF `_acquire_ownership etf_trading.py:95`, `PortfolioLedger` lease `portfolio_ledger.py:80-94`, `RunLock journal.py:25` | REUSE; one writer 증명만 추가 |
| TX02B attribution | `EtfJournal.logical_book :98`, `apply_status :312`, `PortfolioLedger.apply_fill :485` | REUSE one owner per path |
| TX03 product/accounting | `ProductSpec` `paper_execution.py:104/240`, `CAPABILITIES :61`, `apply_funding :578` | scope-limited REUSE; inverse/hedge unsupported |
| TX04 Paper durability | `PaperExecution._transition :64`, `PortfolioLedger.transaction :164`, `save_paper_state :222` | REUSE; restart/dedup gap만 |
| TX04B continuous input | `PaperExecution.on_book/on_trade :294/375`, `_arrive :221`, `_sweep :263` | ADAPT local NATS-free input |
| TX05/VN13 | `strategies/contract.py:15/112` (`load_strategy`, `STRATEGY_ID/SUBSCRIPTIONS/WARMUP/create_strategy`), `PaperStrategyRuntime.register :170` | one workstream; no parallel runtime |
| TX06A–E | `liquidity_mm`, `fv_mm`, `extended_brokers`, `bybit_execution`, `perp_execution` mappings/signers | preserve existing; no new HTTP/sign/private wrapper |
| TX07A facts | `Runner.order_event/execution_event liquidity_mm:161/184`, `fv_mm on_order/on_fill :83/113`, `BybitAudit.fill :130` | ADAPT projection only |
| TX07B reconcile | `liquidity_mm.Runner.reconcile :369`, `fv_mm.Runner.reconcile :151`, `EtfTradingLoop.reconcile :518` | REUSE existing; selected gap only |
| TX08A/B | `PortfolioLedger.admit_order :381`, `_reserved :322`, `record_group_fact :249` | same-account gate only; cross-venue deferred |
| TX09/VN15 | `EtfTradingLoop.account_report :708`, `EtfJournal.notify :290`, existing Telegram paths | one projection workstream |
| TX10 export/cloud | `archive_backend storage_backend.py:178`, Ncloud `:123/:134/:143/:165` | keep; manifest linkage만 |
| VN07–09 recovery/storage | market checkpoint/journal + `SpoolReader`, `archive.py` evidence functions | complete existing; no universal account state |

## 5. 최소 diff·파일 범위·보존 gate

| 작업 | 허용 파일 범위(예정, 이번 단계 편집 아님) | 최소 diff | 보존 gate |
|---|---|---|---|
| 데이터/복구 | `market_spool.py`, `local_storage.py`, `market_checkpoint.py`, 관련 reader tests | retained-prefix reader, strategy/Paper checkpoint continuity | 기존 replay identity/gap semantics, 원본 무삭제 |
| 전략 파일 plugin | 신규 `src/vibetrading/strategies/<id>.py` 또는 `kr/{c3,kp,kd}.py`, `strategies/contract.py` seam | STRATEGY_ID/SUBSCRIPTIONS/WARMUP/create_strategy/on_event + explicit config | C3/KP/KD economics·Golden 불변; calculator는 내부 helper |
| 기존 Paper 연결 | `strategy_runtime.py`, `paper_execution.py`, `portfolio_ledger.py` seam | local data→Signal→Paper, restart fault | 외부 mutation 0, 기존 matcher/ledger 재사용 |
| 동일계좌 안전 | `portfolio_ledger.py`, `etf_journal.py` seam | reservation/attribution/cancel ownership/restart | ACK≠terminal, unknown 예약 유지, per-mode one writer |
| projection/control | `telegram*.py`, `dashboard/bridge.py`, `local_observation.py` | actual/assumed/PAPER/recovery label, 기존 command bridge | hot path I/O 0, 기존 `/trade`·Mock2 보고 보존 |
| cloud/manifest | `storage_backend.py` seam, registry metadata | manifest→strategy file/Paper 등록 연결 | 기존 uploader/verify/download 변경 없음 |
| runtime wiring | `local_runtime.py`, `local_config.py`, release manifest | local process/bounded resource wiring | 기존 runtime·배포 topology 보존 |

## 6. 범위 교정 당시 37개 — VN07·VN08·VN09·TX01·TX02A 로컬 및 TX00 정적 감사 완료, 현재 31개 잔여

`핵심`은 `tech_lead_scope_decisions.json`의 `mandatory_core` 18개다. `기반`은 TX00 감사, `집계`는 별도 신규 제품이 아닌 gate다. 역사 상태는 재설정·자동 승격하지 않는다.

| ID | 구분 | 역사 상태(보존) | 선행(revised_dag) | 남은 일 |
|---|---|---|---|---|
| VN07 | 로컬 완료 | COMPLETE_LOCAL_IMPLEMENTATION (이전 partial 증거 보존) | VN06, VN04B | exact-cut 단계적 복구·전체 격리; VN07B 수락 |
| VN08 | 로컬 완료 | COMPLETE_LOCAL_IMPLEMENTATION (이전 상태 보존) | VN07 | retained-prefix proof·실제 storage restart 검증 |
| VN09 | 로컬 완료 | COMPLETE_LOCAL_IMPLEMENTATION (dry-run only) | VN08, VN16 | 실제 증거 교집합·비실행 제안서; 삭제/자동 전환 없음 |
| VN13 | 핵심 | COMPLETE_LOCAL_IMPLEMENTATION | VN03, TX01, TX04B | 전략별 Python file + 기존 contract + shared data/Paper |
| VN15 | 핵심 | PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES | VN14B | TX09와 한 projection workstream |
| VN17 | 핵심 | PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES | VN09, VN15, VN16, VN13 | 기존 runtime local wiring/bounded resources |
| VN18 | 핵심 | PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES | VN17, VN05 | Golden + data recovery + 선택 plugin/Paper/same-account fault |
| VN19 | 핵심 | PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES | VN18 | 기존 packaging/export/rollback, 선택 data-path 전환 |
| VN20 | 외부 | PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES | VN19 | proof 이후 외부 작업; 삭제 0 |
| TX00 | 정적 감사 완료 | COMPLETE_LOCAL_IMPLEMENTATION (제품 구현 아님) | VN00 | 현재 함수·caller·환경 증거 마감; connector 변경0 |
| TX01 | 로컬 완료 | COMPLETE_LOCAL_IMPLEMENTATION (PAPER 계약) | TX00 | 기존 연결 재사용; 전체 identity/causal NEW 검사 완료 |
| TX02A | 로컬 완료 | COMPLETE_LOCAL_IMPLEMENTATION (협조 Paper owner) | TX01, VN10 | 기존 물리 계좌 lease/checked API·commit·cleanup fence 수락 |
| TX02B | 핵심 | COMPLETE_LOCAL_IMPLEMENTATION | TX02A | 기존 book의 attribution/reservation/restart gap만 |
| TX02 | 집계 | COMPLETE_LOCAL_IMPLEMENTATION | TX02A, TX02B | 기존 owner/books 공동 수락 |
| TX03A | 선택 | ISOLATED_LINEAR_PAPER_ONLY | TX01 | 추가 상품 일반화는 요청 시까지 DEFER |
| TX03B | 선택 | PAPER_CASHFLOW_IMPLEMENTATION_ACTUAL_MARGIN_OPEN | TX02B, TX03A | 선택 Paper 회계 gap만; universal live margin 금지 |
| TX03 | 선택 | PLANNED | TX03A, TX03B | 명시 선택 상품 gate만 |
| TX04A | 핵심 | COMPLETE_LOCAL_IMPLEMENTATION | TX02B | 기존 atomic persistence의 restart/dedup gap |
| TX04B | 핵심 | COMPLETE_LOCAL_IMPLEMENTATION | TX04A, VN03 | NATS-free 연속 입력을 기존 matcher에 연결 |
| TX04 | 집계 | COMPLETE_LOCAL_IMPLEMENTATION | TX04A, TX04B | 기존 Paper E2E gate |
| TX05 | 핵심 | COMPLETE_LOCAL_IMPLEMENTATION | VN13 | VN13과 동일 workstream |
| TX06A | 선택 | REQUEST_MAPPING_ONLY_FULL_CODE_COMPLETION_BLOCKED | TX00 | Binance 기존 connector 유지; 새 wrapper 금지 |
| TX06B | 선택 | REQUEST_MAPPING_ONLY_FULL_CODE_COMPLETION_BLOCKED | TX00 | Arcus 기존 WS/signer 유지 |
| TX06C | 선택 | REQUEST_MAPPING_ONLY_FULL_CODE_COMPLETION_BLOCKED | TX00 | GRVT fv_mm/private/reconcile 유지 |
| TX06D | 선택 | REQUEST_MAPPING_ONLY_FULL_CODE_COMPLETION_BLOCKED | TX00 | Bybit fv_mm/liquidity/probe 유지, testnet 한정 |
| TX06E | 선택 | REQUEST_MAPPING_ONLY_FULL_CODE_COMPLETION_BLOCKED | TX00 | Hyperliquid SDK/broker 보존 |
| TX06 | 선택 | PLANNED | TX00 | all-five rebuild gate 제거; 선택 route 감사만 |
| TX07A | 핵심 | PLANNED | TX01 | 기존 order/fill/journal 위 최소 정규화 projection |
| TX07B | 선택 | PLANNED | TX00, TX07A | 기존 reconciliation의 입증된 gap만 |
| TX07 | 선택 | PLANNED | TX07A, TX07B | 선택 runtime equivalence gate |
| TX08A | 핵심 | PAPER_SHARED_ACCOUNT_FIXTURE_IMPLEMENTATION | TX02, TX05, TX07A | 동일계좌 동시전략 reservation/attribution/cancel/restart |
| TX08B | 선택 | PAPER_OBSERVATIONAL_COORDINATOR_IMPLEMENTED | TX08A, TX07A | cross-venue는 명시 수요까지 DEFER |
| TX08 | 집계 | PLANNED | TX08A | same-account evidence만 |
| TX09 | 핵심 | PLANNED | VN15 | VN15와 동일 read-only projection/control |
| TX10 | 핵심 | PLANNED | TX01 | 기존 Ncloud 유지; manifest→plugin/Paper metadata만 |
| TX11 | 집계 | PLANNED | TX05, TX08A, TX10, VN15, VN18 | 좁은 NATS-free E2E, five-venue/arb 제외 |
| TX12 | 집계 | PLANNED | TX11, VN19 | 최소 local acceptance; 범용 완성 badge 없음 |

**DAG 요약(cycle-free):** `VN07→VN08→VN09`; `VN03/TX01/TX04B→VN13→TX05`; `VN14B→VN15→TX09`; `VN09/VN15/VN16/VN13→VN17→VN18→VN19→VN20`; `TX00→TX01→TX02A→TX02B→TX02`; `TX02B→TX04A→TX04B→TX04`; `TX01→TX03A→TX03B→TX03`; `TX01→TX07A→TX07B→TX07`; `TX02/TX05/TX07A→TX08A`; `TX08A→TX08`; `TX08A/TX07A→TX08B`; `TX05/TX08A/TX10/VN15/VN18→TX11`; `TX11/VN19→TX12`. TX08 aggregate는 TX08A만 의존하고, TX11 core는 all-five/cross-venue를 선행으로 갖지 않는다.

## 7. 순서와 명시적 제외

1. **데이터/복구:** VN07→VN08→VN09. 기존 reader/archive/upload 검증 우선, GC는 proof 후 후보만.
2. **파일 plugin + 기존 Paper:** TX01 → TX02A/B → TX04A/B → VN13/TX05. C3/KP/KD calculator는 계산기일 뿐 plugin 완료가 아니다.
3. **동일계좌 안전:** 이미 확인한 TX02A/B를 재사용하여 TX08A. 새 multi-strategy 연결에만 요구하며 기존 ETF runtime을 재작성하지 않는다.
4. **Projection/E2E:** VN15/TX09와 TX10은 독립 가지다. VN17→VN18 뒤 TX11 및 VN19를 각각 완료하여 TX12로 합류한다..
- **필수 core에서 제외:** all-five-venue rebuild, TX06 묶음, TX03A/B의 universal margin, TX08B cross-venue, TX07B의 새 Private Stream, TX06A–E 신규 HTTP/sign/private wrapper. 이들은 "나중에 선택"이며 이 계획의 완료 조건이 아니다.
- 삭제/통합은 **미래 최소 diff 계획**일 뿐 이번 단계 실행이 아니다. 코드 삭제 0.

## 8. 테스트·증거·상태

- 각 미래 티켓은 **기존 Golden을 먼저** 실행하고, 다음으로 가장 작은 기존 runtime mock/transport fixture를 사용한다. 새 테스트는 누락된 local data/Paper/recovery fault에만 허용한다. Live 작업은 외부 gate이며 자동 재실행하지 않는다.
- 과거 HEAD에서 **1961 pass**로 기록된 것은 과거 사실이다. 이번 단계는 **STATIC/DOC**이며 제품 테스트 재실행이 필수가 아니다. 이 문서는 어떤 테스트 PASS도 주장하지 않는다.
- **Codex 계획 수락:** 제품 변경·삭제0, 함수 정의/참조의 Git/AST 대조, 완료 VN14개 보존, 정합성169건 및 최종 canonical CLI1건 통과. 이것은 정적 계획 수락이며 live/제품 실행 수락이 아니다. [수락 기록](../runs/20261023-v3/reuse/acceptance.json).
- 안전 불변조건: cancel ACK ≠ terminal fill; UNKNOWN은 reservation 유지; TOSS DATA_ONLY; fill identity 단조·중복 없음; per-mode one writer. 이 문서는 live/operational 검증 완료를 주장하지 않는다.

## 9. 보류된 보조 작업과 현재 수락 범위

- O01 실행기 보완과 R06의 입증된 미사용 파일 정리는 이 최소 전환의 필수 선행이 아니다. R06도 실제 import/dynamic/deploy/rollback/Golden 근거와 검증 없이 삭제하지 않는다.
- 위37개는 역사적 VN/TX 상태를 보존한 범위표다. 모든 항목을 새로 구현하라는 목록이 아니며 조건부 venue/product/cross-venue 항목은 필수 core에서 제외한다.
- [Tech Lead 검토](./reuse_audit/tech_lead_review.md)와 [검증 기록](./reuse_audit/plan_validation.json)은 raw worker 제안을 재사용/보존 기준으로 제한한다. 새 제품 구현을 시작하려면 선택 함수의 실제 gap과 최소 변경을 명시한 별도 bounded ticket이 필요하다.

## VN07 후속 구현 완료 (2026-10-23)

기존 market checkpoint·Paper/ETF 원장과 전략 인스턴스를 재사용한 bounded checkpoint 및 검증 복원을 완료했다. 저널·입력 경계가 일치하면 복구하고 불일치·미확인 suffix·손상 시 선택된 전체 runtime을 격리한다. 독립 DB의 동시 commit이나 원장 rewind는 하지 않는다. 이전 VN07A는 안전 차단 하위 작업이었으며 VN07B가 남은 로컬 구현을 완성했다.

VN07B 수락 당시 상태는 **15 완료 / 22 미완료 / 14 미착수**였다. 제품 1,850 + tooling 169 = **2,019 PASS**, live-data 2건 제외. 기존 14개 완료 항목·Golden·거래소·cloud 동작을 보존했다. 당시 VN08 등 다른 티켓은 미착수였고 production gate는 BLOCKED였다. [수락 기록](../runs/20261023-v3/vn07b/acceptance.json). 위 정적 감사 당시 수치는 역사 기록이다.

## VN08 후속 구현 완료 (2026-10-23)

VN08 수락 당시 상태는 **16 완료 / 22 미완료 / 13 미착수**였다. 원본 prefix가 있을 때만 read-only 증거를 생성하고, 독립적으로 pin한 hash/generation과 실제 retained anchor를 검증한다. 저장소는 기존 progress와 manifest/fact를 재검증하며 새 progress를 만들거나 되감지 않는다. 전체 **2,055 PASS**, live-data 2건 제외. 기존 writer/codec·Golden·거래소/cloud 동작 보존. 당시 VN09는 미착수였으며 실제 GC/삭제/cutover는 계속 별도 gate다. 위 VN07 당시 수치는 역사 기록으로 보존한다. [수락 기록](../runs/20261023-v3/vn08/acceptance.json).

## VN09 후속 구현 완료 (2026-10-23, dry-run only)

VN09 수락 당시에는 **17 완료 / 22 미완료 / 12 미착수**였다. 기존 증거로 GC 후보만 계산하며 원본·consumer·저널·manifest·archive·retention·lease 증거가 부족하거나 검사 중 바뀌면 요청 cut 전체를 보존한다. 제안서는 비실행 상태로 원자적·비덮어쓰기 발행한다. 기존 uploader·trading·Golden은 유지했다. 전체 **2,119 PASS**, live-data 2건 제외. FreeLLMAPI GLM 검증은429로 실행되지 않았고 대체 모델은 사용하지 않았다. 실제 GC/삭제·자동 cutover와 다른 티켓은 착수하지 않았다. [수락 기록](../runs/20261023-v3/vn09/acceptance.json).

## 12. TX00 현재 소스 정적 감사 수락

TX00 수락 당시 상태는 **18 완료 / 21 미완료 / 12 미착수**였다. TX00 완료는 기존 실행 경로의 정적 재사용 감사이며 신규 venue 구현이나 운영 검증 완료를 뜻하지 않는다. 47파일/712정의/459정적 참조를 확인했고 기존 TOSS36건 및 독립 경계14건이 통과했다. 역사 감사·완료17개·거래소/cloud/전략 코드·Golden을 보존했다. 다음 선행은 TX01 기존 Signal/OrderIntent/Paper 연결이며 이 티켓에서는 착수하지 않았다. [현재 감사](./reuse_audit/tx00_current/closure.md)·[수락](../runs/20261023-v3/tx00-current/acceptance.json).

## 13. TX01 기존 Paper 계약 로컬 수락 (2026-10-24)

TX01 수락 당시에는 **19 완료 / 20 미완료 / 12 미착수**였다. 기존 연결이 충분하여 새 bridge/engine/API를 만들지 않았다. NEW 사전 검사17줄과 회귀시험을 추가했고 37개 다른 runtime 메서드는 AST가 같다. 기존236 PASS 기준 위에서 독립287/전체2170 PASS(외부 live-data2건 제외)를 확인했다. 역사 완료18개·Golden·거래소/cloud·Mock/Mock2·TOSS를 보존했다. 다음 TX02A 및 별도 전략 file/Paper 연속 입력 작업은 미착수다. [수락](../runs/20261024-v3/tx01/acceptance.json).

## 14. TX02A 기존 Paper owner 로컬 수락 (2026-10-24)

TX02A 수락 당시 **20 완료 / 19 미완료 / 12 미착수**였다. 기존 shared-root 물리 계좌 lease를 재사용하고 다른44개 장부 메서드·schema·계산·체결·Golden·ETF/Mock/Mock2/TOSS/cloud를 보존했다. 기존289 PASS 위에서 독립323/전체2204 PASS(외부2건 제외)를 확인했다. owner 강제 종료 후 최신 잔액/예약/UNKNOWN 복구도 합성 child process로 검증했다. 동일 보호 root/협조 버전/로컬 볼륨 전제이며 새 owner 서비스·epoch 엔진·real 거래소 권한을 만들지 않았다. 다음 TX02B와 다른 티켓은 미착수다. [수락](../runs/20261024-v3/tx02a/acceptance.json).

## 15. TX02B → TX02 → TX04A 연속 수락 (2026-10-24)

이전 배치 수락 당시 **23 완료 / 16 미완료 / 12 미착수**였다. TX02B는 일반 전략의 내부 `@` 귀속 이름 사용만 차단했고, TX02는 기존 Paper/ETF 장부의 수동 계산 공동 검증이며, TX04A는 기존 matcher/원장 주문 정합성 확인을 보완했다. 전체2266 PASS/외부2건 제외. 완료된20개와 다른48개 task 객체·DAG 간선·원래 거래소/클라우드·전략/Golden을 보존했다.

남은28개는 아래 배치 현황과 두 canonical JSON에서 확인한다. 다음 후보는 TX04B 연속 입력과 VN13/TX05 전략 파일 연결이며 이번에는 착수하지 않았다. 추가 상품/거래소 범용화는 여전히 필수가 아니다. [전체 완료/미완료/미착수 목록](../runs/20261024-v3/batch-tx02b-tx04a/README.md).

## 16. TX04B → TX04 → VN13 연속 수락 (2026-10-24)

이전 배치 수락 당시 **26 완료 / 13 미완료 / 12 미착수**였다. 기존 연속 입력·idle Paper 진행·stale 참조 호가 차단과 파일별 명시 configuration 연결을 완료했다. 전체2322 PASS/외부2건 제외. 이전 완료23개와 비선택48개 task 객체·DAG 간선을 보존했으며 기존 거래소/cloud/C3/KP/KD 경제 경로를 보존했다. 다음 후보 TX05는 VN13 결과를 재사용해 남은 공동 E2E만 확인하며 새 runtime을 만들지 않는다. 이번에 착수하지 않았다. [전체 상태](../runs/20261024-v3/batch-tx04b-vn13/README.md).

## 17. TX05 기존 파일 전략/Paper 경제 E2E 수락 (2026-10-24)

현재 **27 완료 / 12 미완료 / 12 미착수**다. 기존 VN13/TX04 경로와 실제 체크포인트를 재사용했다. 제품 변경0, 독립286/전체2325 PASS(외부2건 제외). 완료된26개·비선택50개 task 객체와 DAG 간선, 기존 전략/거래소/cloud/Golden을 보존했다. 추가 티켓/배포/실주문은 진행하지 않았다. [전체 분류·증거](../runs/20261024-v3/tx05/README.md).
