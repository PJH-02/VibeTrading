# VN07 bounded identity subunit — Pin unopened C..H spool segment authority

task_id: VN07/segment-identity (subunit only, entire VN07 remains INCOMPLETE)
base_sha: 99f25957ca5ec1614b4e8ee550e16077b5fd8773
prerequisites: VN06, VN04B localcomplete; R00/R01/O00 accepted
role: debug; model: opencode/deepseek-v4.1-flash-free (qualified O00 debugging profile)
read_paths: vibetrading/local_runtime.py::replay_checkpoint_startup, vibetrading/market_spool.py::SpoolReader/SpoolScan, tests/refactoring/test_checkpoint_startup.py, docs/tickets/VN07-identity-context.md
edit_paths: vibetrading/local_runtime.py, tests/refactoring/test_checkpoint_startup.py ONLY
forbidden_paths: allotherfiles including market_spool.py/local_engine.py; .env*/secrets/runtime_data/.git/historicalevidence; liveorders/deploy/dataGC
contract_ids: C01–C09, retained-C originaldigest, fixed durableH, bounded reader, virgin restore/no replayorders
review_severity: critical recovery authority (Codex plus independent differentfamilyreview)
stop_conditions: invariant change, outofscope fix, missingdurableprefix, testfailure

## Objective / reproduced defect
Fresh full isolatedLinux suite:1531pass1fail2deselected. Existing test_segment_replacement_after_c_proof_cannot_open_a_new_suffix_authority selects firstiterdir entry accidentally. Independent deterministiccounterexample chooses newest SEALEDsuffix: after target.restore_virgin replaces suffixfile atomically with byte-identical newinode; replay incorrectly succeeds. Replacing Csegment correctlyfails. Never fix onlytestordering. Existing SpoolReader pinsidentity onlycurrentsegment and clearsidentity onadvance, so unopenednextsegment has no pre-restoreauthority. C0 path also currently creates reader AFTERrestore.

## Implementation_steps — narrow checkpoint-specific runtime guard
Keep general SpoolReader/sourcefileunchanged (outsideoriginalVN07allowlist); no wire/checkpointformat/account/GC changes. Add a private checkpoint-specific reader/helper inside local_runtime.py. Before verifyingC/restoring, capture a bounded per-segment proof for the frozen scaninventory, using actualopenedhandle os.fstat (notonly path.stat). MetadataonlyO(segmentcount), no retainedpayloads/per-recordindex; segmentcountbound fromconfiguredmax_records. For everysegment pin dev/ino; sealedsegments additionally exactsize/mtime; activeprefix sameinode and size>=pinnedsize. Use existing bounded sealed/active/sealed rename-open pattern; everyhandle closes evenonerror. Private reader subclass can override _open_segment: validate actualdescriptor against pinnedproof on EACHopen, including __init__/_locate_start/Cread and everylatersegment. InstallproofsBEFOREsuperconstructor. Unknown/missingfrozensegment failsclosed. No raw pathTOCTOU authorization; comparethedescriptorusedforparsing. Preserveactive->sealed rename of SAMEinode and append beyond frozenH, including ifactivegrewbeforeseal. Do not compare active mtime/size bystrictequality: legitimatewriterappendmustremainvalid. Onceobservedsealed immutable signature mayonlystrengthen, not weaken.
Construct/readers for EVERYproducer (C0 included) before restore and reuseexactreader allreplaybranches (including apply_order); remove post-restore freshfallbackauthority. FrozenH comesfrominitial durable scan; newrecords appendedlater mustnotreplay. Revalidate pinned proofsinventory beforeinstall and immediatelyafterrestore inside existingfailurefence beforeapplyingframes, so replacement of no-suffixC or unopenedtail duringrestorehook alsofails. Descriptorchecks on lateropens stillneeded; a one-timepathcheck isinsufficient. If restoredcandidate fails, awaitengine.close and neverreuseasvirginfallback. Pre-installinvalidmetadata mayleavevirginunreadyengine forcallercleanup, matchingexistingcontract. Do notclaimatomiccross-producerfilesystemtransaction oradversarialhostFSsandbox; this guards originalspoolauthority under existing trustedwriter assumptions.

## Acceptance / independent regression requirements
- Change the existing replacementtest to deterministic parameterized oldestC/newestunopenedsuffix (atleast two sealedsegments), no iterdirorderdependency. Byte-identicalreplacement mustraiseidentitychanged; restoredengineclosed, replayed0.
- Add case with C0/unopenedprefix replacement, no freshpostrestoreauthority.
- Add multi-segment traversal replacement immediatelybefore later _open_segment actualdescriptor isused, evenif a prior inventorycheckpassed. No advance/readinessafterfailure, no executiondelivery.
- Add sealed-inplacechange and missing retainedsegment failclosed; historical spool prefixmissing iscorruption (GCnotimplemented, no retained-base fallback).
- Preserve appendafterfreeze / activegrow / sameinodeactive->sealedrotation: onlyC..frozenH restored, later3remainsliveinput, no replayorders; no unboundedopens/payloadlists. Existingmulti-producer exactapply-order and allcheckpoint testsstaygreen.
No weakening old assertions, no genericReader rewrite, no changing sourceevent economics. Keep import/format styleconsistent, small helper only.

## test_commands (all mustreallyrun)
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_checkpoint_runtime.py tests/refactoring/test_vnext_vn07.py tests/refactoring/test_local_runtime.py tests/refactoring/test_replay_handoff.py tests/refactoring/test_spool_recovery.py
If test_market_spool.py doesnotexist use providedcontext exactspooltestpath, notinventtests. Do notmark parentVN07complete. Return actualcounts/exit, changedfunctions andremainingboundaries. No agents/installs/metadata/commit/externalnetwork. Max2repairs; stopafterboundedcompletion.
