# R03A — Pure package relocation (mechanical)

task_id: R03A (subunit of R03)
base_sha: ff31bbbd8d6c8c15ee47fc63d08b1d26d54d7d43
prerequisites: R00/R01/R02/O00/O01 accepted; integrated1711+6PASS on unchanged product source.
role: mechanical deterministic Python/Git (explicit v3 mechanical role)
read_paths: tracked vibetrading/**/*.py metadata/AST and protected P02 script digest
edit_paths: rename all140tracked vibetrading Pythonfiles to src/vibetrading; docs/refactoring/vnext/path_map.json; newR03A evidence only
forbidden_paths: economic logic, strategy/order/price/qty/risk/model changes, data/evidence/rollback edits, source aliases, deploy/server/liveorders
contract_ids: v3 R03, existingC01–C17
objective: Canonicalsourcepackagerootmove only; module importnames stayvibetrading.*.
implementation_steps: gitmv wholepackage, deterministic140filebyte/AST before-aftercomparison, emitone-to-one pathmapandrootprefixrule forplannedADDpaths.
acceptance: byte differences0/ASTdifferences0/missingfiles0, originalpackagealias absent, P02scriptdigestunchanged. ParentR03 remainsRUNNING untilpackaging/defaultresourcepaths/CLI/Docker/releaseandtoolingpathmapcompatibility verified.
test_commands: deterministic byte/ASTfilemap check in receipt; integratedsourcebeforemovealready1711+6PASS; no runtimePASSclaimatthisintermediatepoint.
review_severity: mechanical + Codex source review
stop_conditions: any AST/bytesdifference, unexpectedpath/sourcecollision, rollbackdigestchange.
