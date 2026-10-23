# Agent operating rules (v3 refactoring)

- Read the selected ticket and 1–5 relevant files. Use targeted rg/AST lookups;
  avoid repeated whole-repository or whole-metadata scans. Read the three v3
  documents once, then only the necessary ticket/contract sections.
- Product authority: `docs/refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md`,
  `VibeTrading_Developer_Ticket_Spec_v3.md`, and
  `VibeTrading_Architecture_Requirements_v3.md` in that directory.
  Status authority: existing `tickets_production.json` and `stage_status.json`.
  `python3 scripts/dev/status.py --root . --json` is a derived read-only index,
  not a replacement contract or state database. Workers may not edit metadata
  to make a test pass; the orchestrator records legitimate receipts/status.
- Use only the explicit, qualified model profiles declared in
  `tickets_production.json`. Keep the existing `~/.local/bin/delegate-opencode ROLE TICKET MODEL`
  invocation form; do not invent roles, models, or a new orchestration protocol.
- Work in the credential-free OS sandbox and make exact, scoped edits. No package
  installs, broad rewrites, or unrequested refactors.
- Codex performs critical acceptance. Workers never self-approve or claim
  acceptance on their own.
- Preserve C3, KP, KD, Mock, Mock2, and TOSS `DATA_ONLY` behavior.
- Never run live trading, deployment, push, merge, NATS deletion, or commands
  that expose secrets. Do not delete or replace production data.
- The ticket is sufficient authority. Do not add an unnecessary approval loop;
  stop after the scoped edits, exact test command, and concise receipt.
