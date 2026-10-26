# R02 — Current architecture and preservation index

task_id: R02
base_sha: 99f25957ca5ec1614b4e8ee550e16077b5fd8773
prerequisites: [R00 ACCEPTED, R01 ACCEPTED]
read_paths: [README.md, docs/status.md, docs/tickets/R02-context.json]
edit_paths: [README.md, docs/architecture.md, docs/archive/README.md]
forbidden_paths: [all other paths, product code, data, historical receipts, rollback scripts, canonical metadata]
contract_ids: [v3 R02, R00 preservation]
review_severity: low
stop_conditions: [unknown source claim, missing referenced local document]
model: opencode/glm-5.3-flash-free (O00 qualified repository profile)

## Objective / implementation_steps
Replace the outdated sprawling root README with a concise Korean introduction, current status, entry points and links (roughly100–180lines, fewer is fine). The supplied context is a Codex-derived bounded path/title index and actual proven status. Do not read all indexed docs or historical receipts. Read only top160lines of oldREADME, current docs/status and thecontext. No exploratorytoolcalls, agents or shell commands. This is docs-only; coordinator validates links/CLI later.
Clearly distinguish last documented deployed NATS architecture and scopedP02 from the local partially implemented NATS-free candidate; current server state remains uncontacted/UNKNOWN. Do not claim PostgreSQL/QuestDB commercial operation or completedv3/5venuecapability. Mention actual current failing baseline without inventing readiness. Existing real strategy behavior staysC3/KP/KD and KISMock/Mock2, TOSSDATA_ONLY. Link the threev3 authorities, canonical JSON and status helper, architecture, P02 rollback, existing ETF/CSV/MM/backtesting/dashboard runbooks as historical/conditional references using paths actually incontext. Preserve useful links from theoldtopREADME, but no unsafe start/deploy/live order commands.
Create docs/architecture.md: bounded ownership map of current legacydeployedNATS versus localcandidate(spool/engine/recovery/checkpoint/storage/orderledger) and research/backtest ownership. Unknown dynamicimports remainunknown. Reuse existingpaths; no fictional completepipelines. Show no proof of actualcurrentserver and no fullPaper/live readiness. Document 2vCPU3.8GiB is a target notmeasuredserverpass.
Create docs/archive/README.md preservation/quarantine index only. No actualmoves/deletions; preserve docs/refactoring/runs, docs/evidence, data/production paths, active scripts/refactoring/deploy_p02_schema.py and legacyNATS until externalVN20. Quarantine candidates require perpath references/dynamicimport/deploy/rollback evidence beforefutureaction; no blanketarchiveapproval. Explicitly list nofilesmoved onthisdocsticket.
Commands allowed in docs: `python3 scripts/dev/status.py --root . --json`, `python3 -m vibetrading.cli --help`, existing entrypoints only with --help. Do not add unverified runningexamplepaths. All localMarkdown links mustexist incontext pathindex, beoneofnewdocfiles, or point toknownsource/rollback path. No gratuitousemoji or productiongradeclaims. Keep 3v3authoritydocs untouched.

## Acceptance / test_commands
Coordinator will validate every addedMarkdownlocaltarget with a stdliblinkresolver against the actualrepo, inspect diffscope/content, and run safeCLIhelp in credentialfree networknamespace. No tests or helpers shouldbeaddedbyworker. Return changedfiles and docsverificationlimits; do notclaimtestsrun orselfapproval.
