## Objective
- 문서 전용 최소 재사용 리팩토링 계획을 완성한다. 기존 ETF/venue/Paper/cloud/strategy 동작을 보존하면서 NATS-free data/recovery, strategy file plugin, 기존 Paper 연결, 동일계좌 safety, projection/E2E 범위를 문서화한다.
- 제품 코드는 작성·삭제하지 않고, Codex Tech Lead 결정과 최신 사용자 범위 교정을 권위 문서에 반영한다.

## Important Details
- 기준 SHA: `4977ab509490173e312183f32d47f862d94ee0bf`.
- 필수 core 18개: `VN07,VN08,VN09,VN13,TX01,TX02A,TX02B,TX04A,TX04B,TX05,TX07A,TX08A,VN15,TX09,TX10,VN17,VN18,VN19`.
- 선택/비core: `TX03A,TX03B,TX03,TX06A,TX06B,TX06C,TX06D,TX06E,TX06,TX07B,TX07,TX08B`.
- 37개 미래 행, 완료 14개(`VN00–06,VN10–12,VN14A/B,VN16`) 보존. 역사 상태 자동 승격·재설정 금지.
- DAG: `TX05→VN13`만 의존, `TX09→VN15`, `TX08 aggregate→TX08A`만, `TX11` core deps = `TX05,TX08A,TX10,VN15,VN18`; all-five/cross-venue 제외.
- KIS ETF는 `KisCashBroker` 직접 호출. legacy `broker_factory`의 `BrokerStub` 부재를 ETF venue 미지원 증거로 사용 금지.
- Bybit/GRVT `fv_mm`·liquidity·probe는 testnet 전용. Binance spot ≠ perp, mock ≠ testnet ≠ live.
- GRVT `native_client_id` bit-width/hash 결함은 **UNVERIFIED**로 하향, 수정 금지.
- `perp_execution` mapping/signing composition은 재사용 artifact/prototype. 주입 `Transport`/`normalize_private_event`는 live socket 아님.
- 신규 exchange/client/signer/Private Stream/cloud uploader/universal margin/lifecycle/cross-venue atomic group 금지.
- 테스트: unchanged Golden first → 가장 작은 기존 runtime mock/transport fixture. 새 테스트는 누락 local data/Paper/recovery fault만. Live external 자동 재실행 금지.
- 과거 HEAD `1961 pass`는 과거 사실. 현재 단계는 STATIC/DOC이며 테스트 PASS를 주장하지 않음.
- 소스 삭제 0 / 제품 변경 0은 pending owner verification이며 self-acceptance 아님. Codex만 판정·수락.
- Bash는 `python3 -m pytest*` 외 denied. `rg`/shell exploration 불가. 명시된 pytest command 없음, old tests 금지.
- `function_inventory_*.json`, `function_dispositions*`, `direct_callers*`는 현재 docs tree에 없음. 601 functions/245 direct AST calls는 수기/live 검증하지 않음.
- 응답은 한국어 문서 산출물 기준이며 마지막에 `DOCS_COMPLETE`와 편집 경로를 나열해야 한다.

## Work State
### Completed
- 필수 입력 읽음: ticket, `tech_lead_scope_decisions.json`, `revised_dag.json`, `metadata_validation.json`, raw reports, 기존 v3/08/11/12/13 문서.
- 새 계획 작성: `/work/docs/refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md` — 136 lines, 6분류 규칙·현재 경로·TX 중복 매핑·최소 diff·37행 잔여작업·DAG·제외 범위 포함.
- 새 matrix 작성: `/work/docs/refactoring/vnext/reuse_audit/execution_reuse_matrix.md` — 59 lines, legacy/runtime path + 5 normalized Perp adapters + local bridge 후보 + evidence limits.
- `11_integrated_vn_tx_plan.md`를 canonical `revised_dag.json` 기준으로 전면 재작성 — 114 lines, 완료 14 + 미래 37 표, Mermaid, deferred/external labels. Cyrillic `ТX05` typo 수정.
- `VibeTrading_Refactoring_Final_Plan_v3.md` 수정: 상단 binding scope block, §1 reuse-first 범위, 상태표 rows, G4–G9 waves, DeepSeek 역할 문구.
- `VibeTrading_Developer_Ticket_Spec_v3.md` 수정: 상단 binding block; VN07 REUSE 우선; VN13/VN15/VN17–VN20 교체; TX00–TX12 전체를 reuse/audit/optional 중심으로 교체. 420 lines.
- `VibeTrading_Architecture_Requirements_v3.md` 수정: 상단 binding block, §0.1/0.2, §6 selected product, §8 reference-only, §9 deferred, §12 selected core, §13.1 gates, §16 invariants/final completion.
- `08_execution_and_portfolio_contracts.md` 수정: binding block, C13 selected mode, C16 DEFERRED, C17 selected.
- `12_production_acceptance_matrix.md` 수정: binding block, status rows, scenarios B–F를 core/selected/deferred로 조정.
- `13_strategy_deployment_lifecycle.md` 수정: binding block, steps 2/4/5/6를 strategy-file + 기존 Paper + 기존 runtime 보존으로 조정.

### Active
- 편집된 문서 전체를 재독하며 광범위 mandatory 문구 잔존, 섹션 중복/깨진 heading, 링크·DAG 불일치, line-count 초과 여부를 최종 검토 중.
- 아직 최종 `DOCS_COMPLETE` 응답을 작성하지 않음.

### Blocked
- `bash`/`rg` denied (허용: `python3 -m pytest*`만). 문서 전수 grep 불가, `read`로 chunk 검토 필요.
- 명시적 pytest command가 ticket에 없고 old tests 실행 금지라 테스트 실행 불가/불필요.
- machine inventory/dispositions/245 AST call 원본 파일이 현재 docs tree에 없어 전체 수기 검증 불가; matrix에 한계 명시함.

## Next Move
1. `/work/docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md` 150–420 및 3개 v3/08/12/13 문서의 남은 구간을 재독해 모순 문구·중복 섹션·DAG 오류를 수정한다.
2. 최종 응답에 편집 경로 전체, 실행 명령/테스트 결과(없음), `소스 삭제 0 / 제품 변경 0 pending owner verification`, `DOCS_COMPLETE`를 한국어로 간결히 기록한다.

## Relevant Files
- `/work/docs/tickets/REUSE-minimal-plan-docs.md`: 권위 ticket·deliverables·금지 범위.
- `/work/docs/refactoring/vnext/reuse_audit/tech_lead_scope_decisions.json`: binding scope/core/optional 결정.
- `/work/docs/refactoring/vnext/reuse_audit/revised_dag.json`: canonical 51-row DAG/statuses.
- `/work/docs/refactoring/vnext/reuse_audit/metadata_validation.json`: PASS, 37 revised scopes.
- `/work/docs/refactoring/vnext/reuse_audit/crypto_runtime-response.md`: Bybit/GRVT/perp path evidence.
- `/work/docs/refactoring/vnext/reuse_audit/owners_etf-response.md`: KIS/ETF/Paper/Kiwoom/TOSS/cloud evidence.
- `/work/docs/refactoring/vnext/reuse_audit/adapters-response.md`: adapter/caller evidence (truncated).
- `/work/docs/refactoring/vnext/reuse_audit/adapters-completion-response.md`: mandatory supplement, Perp adapter rows.
- `/work/docs/refactoring/vnext/reuse_audit/execution_reuse_matrix.md`: 새 venue/path 비교 matrix.
- `/work/docs/refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md`: 새 통합 결정·잔여작업 계획.
- `/work/docs/refactoring/vnext/11_integrated_vn_tx_plan.md`: canonical DAG 실행계획.
- `/work/docs/refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md`: 상위 실행계획 권위.
- `/work/docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md`: VN/TX Delta Spec.
- `/work/docs/refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md`: 불변조건·아키텍처 요구사항.
- `/work/docs/refactoring/vnext/08_execution_and_portfolio_contracts.md`: C10–C17 계약.
- `/work/docs/refactoring/vnext/12_production_acceptance_matrix.md`: readiness/evidence matrix.
- `/work/docs/refactoring/vnext/13_strategy_deployment_lifecycle.md`: Paper/Live lifecycle.
Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.
## 완료
- 최소 재사용 계획과 실행 재사용 matrix를 신규 작성했다.
- canonical DAG를 `revised_dag.json`과 일치하도록 재작성했다.
- 3개 v3 권위 문서에 2026-10-23 binding scope/SHA/링크를 추가하고 광범위한 필수 범위를 교정했다.
- 08/12/13 계약·승인·lifecycle을 선택된 local core 기준으로 조정했다.
- core 18개, optional/deferred 항목, 완료 14개 이력, 37개 잔여 작업을 보존했다.
- 테스트하지 않은 static 감사를 live 검증으로 표현하지 않았다.

## 검증
- 실행한 pytest: 없음. ticket에 지정된 pytest command가 없고 STATIC/DOC 단계라 제품 테스트를 재실행하지 않았다.
- 과거 HEAD의 `1961 pass`는 역사 기록으로만 유지했다.
- `소스 삭제 0 / 제품 변경 0`은 owner 검증 전 pending 상태이며 Codex 수락을 주장하지 않는다.

## 편집 파일
- `/work/docs/refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md` — 신규
- `/work/docs/refactoring/vnext/reuse_audit/execution_reuse_matrix.md` — 신규
- `/work/docs/refactoring/vnext/11_integrated_vn_tx_plan.md`
- `/work/docs/refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md`
- `/work/docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md`
- `/work/docs/refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md`
- `/work/docs/refactoring/vnext/08_execution_and_portfolio_contracts.md`
- `/work/docs/refactoring/vnext/12_production_acceptance_matrix.md`
- `/work/docs/refactoring/vnext/13_strategy_deployment_lifecycle.md`

DOCS_COMPLETE
