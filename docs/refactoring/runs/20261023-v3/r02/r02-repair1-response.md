Context reviewed. Now checking the inventory file and R01 status doc to quote facts correctly:
Bash is not permitted; I'll rely on the coordinator-supplied path index (repo_inventory.json and R01-authority-status.md are both listed there, so linking them is sanctioned). Applying edits to the three allowed files now.

**README.md:**
**docs/architecture.md:**
A small consistency tweak to the architecture command-boundary wording, then **docs/archive/README.md**:
Now verifying all three files' final state (bash is denied, so I re-read to check):
Two remaining wording fixes for the "권한/권위" distinction:
Done. Stopping after edits per ticket — no tooling/tests were run for docs-only scope (no new tests or code; R02 acceptance validation/link resolution is coordinator-owned).

## Changed files (exactly the 3 edit paths)

**1. `README.md`**
- Removed the duplicated volatile ticket-state list (R00/R01/O00 ACCEPTED, "R02는 READY", 14/23/14 counts, "다음은 O01 runner") and replaced it with: snapshot date/source **2026-10-21, 소스 99f25957 (ticket base)** + pointer to canonical JSON (`tickets_production.json`, `stage_status.json`) and derived `docs/status.md` as the only changing-summary owner.
- **R00 inventory fact corrected**: explicitly states the inventory already exists and was accepted — [repo_inventory.json](docs/refactoring/vnext/repo_inventory.json), 504 parsed Python files, 24 dynamic import sites, unresolved-site meaning UNKNOWN, "인벤토리나 소스 지도가 없다는 뜻이 아님". Linked the R01 output as 권위 상태 색인 (`docs/tickets/R01-authority-status.md`, in the supplied path index).
- **Malformed phrase replaced**: "체크포인트 미개확 접미사 치환 권한 간극" → "체크포인트 이후 미개확(아직 열리지 않은) 스풀 세그먼트를 치환하는 권한 부재 간극" (now appears once in README, once in architecture, no third copy).
- "R01(서버 나열)" → R01 권위 상태 색인; "v3 권한 문서" → "v3 권위 문서"; "머신이 읽는 권한 상태" → "권위 상태"; "동일 모의계좌·권한 문서" → title-accurate link text.
- **Removed authorization self-claim**: "문서에서 허용하는 것은 도움말·상태 확인뿐입니다" → "이 README는 실행 승인이나 소프트웨어 권한 부여 문서가 아닙니다…", help/status examples only (kept only the 4 pre-existing safe help/status commands).

**2. `docs/architecture.md`**
- "권한 문서" → "권위 문서"; "권한 상태(JSON)" → "권위 상태(JSON)". Link `../scripts/refactoring/deploy_p02_schema.py` (correct from docs/) preserved, as were other valid doc links.
- **R00 inventory added to Track 2** with the linked `repo_inventory.json` (504/24 facts) and a real **filepath→owner table** built strictly from the supplied `verified_candidate_source_map` (collector, spool, transport, engine, recovery_checkpoint, storage, account_order_ledger, etf, research — with the map's own partial/партial caveats verbatim).
- **Market-only vs account/strategy recovery kept distinct**: "market-only 복구는 입증되었으나 계정·전략 복구는 불완전, 전체 Paper/Live 준비 추론 불가".
- "파일 수준 인벤토리는 별도 티켓의 대상" (no-inventory/deferral claim) removed; "다이내믹 임포트 인벤토리: UNKNOWN" corrected to "24개 지점 중 미해결 지점의 의미만 UNKNOWN (인벤토리 자체는 존재)". 14/23/14 counts de-duplicated (README now points here as the single snapshot).
- R01 wording "서버 기록성 나열" → "R01의 권위 상태 색인"; KNOWN-UNKNOWN section and baseline line re-worded with the clear unopened-spool-segment failure phrasing.

**3. `docs/archive/README.md`**
- **Rollback link fixed**: `[../scripts/refactoring/deploy_p02_schema.py]` → `[../../scripts/…]` (lexical resolution from `docs/archive/`). Kept `../refactoring/…` links unchanged (correct from docs/archive).
- **Removed invented `data/production/`**: replaced with actual protected `runtime_data/` (절대 복사·열기 금지) plus explicit "외부 마운트·외부 저장 위치는 UNKNOWN"; evidence/rollback preservations as given, NATS legacy row untouched.
- Dynamic-import point 2 now cites the existing/accepted R00 inventory (link `../refactoring/vnext/repo_inventory.json`, correct two-level form) with 504/24 and UNKNOWN for unresolved sites only.
- **De-duplicated volatile states**: 병행 트랙 메모 (O01/R03/etc.) removed → points to `../status.md` + canonical JSON as the only change summary. "no files moved on this docs ticket" retained verbatim; no move/delete performed.

## Evidence for acceptance
- None of the 3 files leaves its assigned edit path; no product files, code, tests, canonical JSON, docs/status, or v3 authority docs touched.
- All newly added link targets (`docs/refactoring/vnext/repo_inventory.json`, `docs/tickets/R01-authority-status.md`) are present in the coordinator-supplied tracked Markdown/path index; relative targets resolve lexically per their directory (repo-root README, docs/architecture.md, docs/archive/README.md).
- Snapshot provenance (2026-10-21, source 99f25957ca5ec1614b4e8ee550e16077b5fd8773) and the 1-failure baseline now appear once each in README and once in architecture, both followed by pointers to canonical JSON/status rather than self-asserted READY state. R02 is not self-approved anywhere.

## Commands run
- Read-only tool reads of the ticket, context JSON, three docs, and a targeted existence check attempt (bash denied in this environment) — **no build/test/deploy commands**, consistent with R02's "no tests or helpers added by worker".

## Limitations
- Bash was denied, so I could not independently re-verify file existence on disk; new link targets rely on the coordinator's path index (worker snapshot may lag) as the ticket instructs.
- Cannot reproduce exact full inventory JSON contents (file not in worker snapshot); quoted counts (504/24) are taken verbatim from the coordinator-verified `r00_inventory_fact`, not from reading the JSON.
- `docs/status.md` remains controller-owned and untouched; final 99+ link resolution and safe-CLI checks are the coordinator's independent verification, per ticket.
