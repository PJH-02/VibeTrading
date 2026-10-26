# TX02B — preserve existing books, fence reserved strategy attribution

Base: a5f1d53bfc753347eee171dcab435c8076dd92b7. Prerequisite TX02A accepted. This is ticket1 of the authorized three-ticket batch TX02B -> TX02 aggregate -> TX04A. Role refactor, exact model opencode/deepseek-v4.1-flash-free; Codex independently accepts. No recursive delegation, no metadata/self-approval, no other ticket edits.

Existing PortfolioLedger already maintains physical/virtual positions, gross reservations, fees, funding and restart identity. Do not rebuild these or modify EtfJournal/ETF/runtime/exchange/cloud behavior. Demonstrated gap: admit_order rejects only '@physical', while callers can admit/claim '@physical_netting_adjustment', '@unallocated_funding' or other internal '@' identities and collide with system attribution categories.

Only writable:
- src/vibetrading/portfolio_ledger.py: existing admit_order and claim_strategy_event only (small guards).
- tests/refactoring/test_tx02b_attribution.py: new cases using existing ledger helpers.

Read 1–5 relevant files: portfolio_ledger.py, tests/refactoring/test_portfolio_ledger.py, tests/refactoring/test_strategy_runtime.py, etf_journal.py (comparison only), trading_contracts.py. Other copied files are readonly dependencies. Preserve all existing tests, Goldens, schema, arithmetic, receipt identity, owner/lease/transaction fencing. No packages, credentials, original runtime data, network, live orders, deployment, push/merge or destructive maintenance. Only exact pytest shell command is needed; do not explore with ls/grep.

Implement:
1. After existing bounded identity validation, reject any NEW user strategy identity starting with '@' in admit_order and claim_strategy_event, before any SQL write or receipt. Keep existing '@physical' rejection semantics/error compatible. Do not normalize/strip/replace IDs or create another validator API.
2. Internal _strategy_credit, physical/netting/unallocated funding categories and existing persisted history must remain readable and unchanged. Do not migrate, delete or reassign historical facts, nor block internal credit writers. Existing '@registration'/'@action:' EVENT IDs are allowed; only STRATEGY IDs are reserved.
3. Tests must demonstrate internal-category spoofing cannot create order/claim/reservation/strategy cash changes; normal strategy IDs, explicit unallocated funding, opposite strategies' physical=sum(virtual), gross pending risk, cancel/UNKNOWN/restart and exact Decimal remain correct. Use actual existing ledger; no duplicate account engine or second allocator. Include partial fills and restart attribution evidence, existing ETF Golden regressions remain readonly.

Exact command:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx01_signal_binding.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx02b_attribution.py

Return exact edits, command/actual exit/count and limitations, no self-acceptance. At most two repair delegations. Stop after TX02B.
