# O01A final source delta audit — all exact source supplied inline
Roleaudit, qualifiedopencode/nemotron-3-ultra-free, read-only. No tools/explorationneeded. PriorNemotronreview readwholecore/status andfoundpathcheck/useraceinprivatewrite_record; nowdescriptorpinnednofollowhelpersreplaceit. Codexrereviewedallotherfunctions; 113independenttestsPASSincludingstatic/racingstatepath/root/canonical/providerroute/pincoveragecounterexamples. Thisisfinaldeltaauditofprovidedchangedfunctions andfullmake_packet/verify_sourcecallers. Noedit/codeexecution/otheragents. Returnactualremainingfindingswithconcreterepro, orNO_CRITICAL_FINDINGS. Don'trepeatrequirements orproposefutureauditinstead ofreviewingTHECODE.
Threat: workerexecutesinsidecredentialfreeFS/PID/NETsandbox, cannotaccesshoststateparents, rootorcanonicals. TrustedhostCodexauthorizesconcretespec/canonical/readroot; sameUIDhostattackerrootnotsecuritysandboxthreat. Stillprivatependingreceiptmustnotfollowpathcomponentlinks orredirectreceiptwriteaftercheck. NewdirfdO_NOFOLLOW/fchmodboundobjecthandles; neverchmodexistingancestors. Pathrenamecouldmakeoriginaldirectoryobjectmove, not redirecttovictimpointer. ModelRouteprefixmustmatchcanonicalentry; reporoot/canonicalsymlinksrejectbeforeANYread/git. Sourcepinsmustexactlycoverread+editandmatchcurrentHEAD+canonicals+actualbytes. write_recordALWAYS PENDING_CODEX_REVIEW/UNTRUSTED_INPUT, neverattestsmodels/testclaims. HostO01Badapterwillcollectactualprocess/teststdoutandexitsseparately.
Priorfindings disposition: legacyVN/TXrawprose statuses notenumerated intentionally; COMPLETEcomesonlyexactstagecategory+deps+bothcontrols. No completionsubstringinference. classify_failureheuristicnotauth; testdigestformatonlyisuntrustedbydesign. Don'treopentheseunlessconcreteNEWbypasswithactualstage/readymechanism.

BEGIN EXACT CHANGED SOURCE UNITS
FILE scripts/dev/agent_task.py sha256=0e34b16aba5e7fb0b680f379ce6eaa6e32f98a8f35c932a2b67c4108890ffff4

246: def _validated_root(root):
247:     """Return an absolute root after rejecting symlinked root/ancestor components."""
248:     if isinstance(root, str) and not root.strip():
249:         raise ValueError("root must be a non-empty path")
250:     path = Path(root)
251:     absolute = path.absolute()
252:     current = Path(absolute.anchor)
253:     for part in absolute.parts[1:]:
254:         current = current / part
255:         if current.is_symlink():
256:             raise ValueError(f"root traverses a symlink: {current}")
257:     if not absolute.exists():
258:         raise ValueError(f"root does not exist: {absolute}")
259:     if not absolute.is_dir():
260:         raise ValueError(f"root is not a directory: {absolute}")
261:     return absolute

264: def _require_canonical_files(root):
265:     for rel in (status_mod.TICKETS_CANONICAL, status_mod.STAGE_CANONICAL):
266:         _existing_regular_file(root, str(rel), "canonical metadata")

421: def _model_route(tickets, profile, model):
422:     profiles = tickets.get("model_routing_profiles")
423:     if not isinstance(profiles, dict):
424:         raise ValueError("canonical model_routing_profiles missing")
425:     profile_models = profiles.get(profile)
426:     if not isinstance(profile_models, list) or model not in profile_models:
427:         raise ValueError(f"model {model!r} is not registered in profile {profile!r}")
428:
429:     readiness = tickets.get("model_readiness")
430:     if not isinstance(readiness, list):
431:         raise ValueError("canonical model_readiness missing")
432:     matches = [
433:         entry
434:         for entry in readiness
435:         if isinstance(entry, dict) and entry.get("model_id") == model
436:     ]
437:     if len(matches) != 1:
438:         raise ValueError(f"model {model!r} must have exactly one model_readiness entry")
439:     entry = matches[0]
440:     if entry.get("status") != QUALIFIED_O00:
441:         raise ValueError(
442:             f"model {model!r} is not {QUALIFIED_O00} (got {entry.get('status')!r})"
443:         )
444:     route = _require_nonempty_str(
445:         entry.get("provider_route"), f"model_readiness[{model}].provider_route"
446:     )
447:     if "/" not in model:
448:         raise ValueError(f"model id must include a provider prefix: {model!r}")
449:     provider = model.split("/", 1)[0]
450:     if route != provider:
451:         raise ValueError(
452:             f"model {model!r} provider_route {route!r} does not match "
453:             f"model prefix {provider!r}"
454:         )
455:     return provider, route

469: def make_packet(root, task_id, spec):
470:     """Build a validated TaskPacket without writing anything."""
471:     root = _validated_root(root)
472:     task_id = _require_nonempty_str(task_id, "task_id")
473:     spec = _validate_spec(spec)
474:     _require_canonical_files(root)
475:
476:     status = status_mod.load_status(root)
477:     _check_ready(status, task_id)
478:
479:     head = _git_head(root)
480:     if head == "UNKNOWN":
481:         raise ValueError("git HEAD is unavailable; refusing to build a packet")
482:
483:     tickets, _stage = _load_canonical(root)
484:     record, family = _find_record(tickets, task_id)
485:
486:     prerequisites = _require_str_list(record.get("depends_on"), f"{task_id}.depends_on")
487:     canonical_forbidden = _validate_forbidden_patterns(
488:         spec["forbidden_paths"], record.get("forbidden_paths") or []
489:     )
490:
491:     canonical_contracts = []
492:     seen_contracts = set()
493:     for field in ("contracts", "contract_ids"):
494:         if field not in record:
495:             continue
496:         validated = _require_str_list(record[field], f"{task_id}.{field}")
497:         for cid in validated:
498:             if cid not in seen_contracts:
499:                 seen_contracts.add(cid)
500:                 canonical_contracts.append(cid)
501:     missing_contracts = [cid for cid in canonical_contracts if cid not in spec["contract_ids"]]
502:     if missing_contracts:
503:         raise ValueError(
504:             "contract_ids must retain canonical contracts; missing="
505:             + json.dumps(missing_contracts, ensure_ascii=False)
506:         )
507:
508:     provider, route = _model_route(tickets, spec["profile"], spec["model"])
509:
510:     source_hashes = {}
511:     for rel in spec["read_paths"]:
512:         if _matches_forbidden(rel, canonical_forbidden):
513:             raise ValueError(f"read path matches a forbidden pattern: {rel!r}")
514:         target = _existing_regular_file(root, rel, "read_paths[]")
515:         source_hashes[rel] = _sha256_file(target)
516:
517:     edit_scope = _canonical_edit_scope(record, family)
518:     for rel in spec["edit_paths"]:
519:         if _matches_forbidden(rel, canonical_forbidden):
520:             raise ValueError(f"edit path matches a forbidden pattern: {rel!r}")
521:         _validate_path_string(rel, "edit_paths[]")
522:         entry = _resolve_canonical_edit(rel, edit_scope)
523:         _check_no_symlink(root, rel, "edit_paths[]")
524:         target = root / rel
525:         if target.exists():
526:             if not target.is_file():
527:                 raise ValueError(f"edit path must be a regular file: {rel!r}")
528:             source_hashes[rel] = _sha256_file(target)
529:         else:
530:             if entry["action"] != "ADD":
531:                 raise ValueError(
532:                     f"missing edit path requires canonical ADD action: {rel!r}"
533:                 )
534:             _check_missing_ancestors(root, rel, "edit_paths[]")
535:             source_hashes[rel] = None
536:
537:     canonical_hashes = {
538:         str(status_mod.TICKETS_CANONICAL): _sha256_file(
539:             root / status_mod.TICKETS_CANONICAL
540:         ),
541:         str(status_mod.STAGE_CANONICAL): _sha256_file(
542:             root / status_mod.STAGE_CANONICAL
543:         ),
544:     }
545:
546:     return {
547:         "schema_version": SCHEMA_VERSION,
548:         "task_id": task_id,
549:         "family": family,
550:         "base_sha": head,
551:         "prerequisites": prerequisites,
552:         "objective": spec["objective"],
553:         "read_paths": spec["read_paths"],
554:         "edit_paths": spec["edit_paths"],
555:         "forbidden_paths": spec["forbidden_paths"],
556:         "contract_ids": spec["contract_ids"],
557:         "implementation_steps": spec["implementation_steps"],
558:         "acceptance": spec["acceptance"],
559:         "test_commands": spec["test_commands"],
560:         "stop_conditions": spec["stop_conditions"],
561:         "review_severity": spec["review_severity"],
562:         "profile": spec["profile"],
563:         "model": spec["model"],
564:         "provider": provider,
565:         "route": route,
566:         "canonical_hashes": canonical_hashes,
567:         "source_hashes": source_hashes,
568:     }

601: def verify_source(root, packet):
602:     """Revalidate HEAD, canonical hashes and every pinned source hash."""
603:     root = _validated_root(root)
604:     _require_canonical_files(root)
605:     packet = _require_dict(packet, "packet")
606:     base_sha = _require_nonempty_str(packet.get("base_sha"), "packet.base_sha")
607:
608:     head = _git_head(root)
609:     if head == "UNKNOWN":
610:         raise ValueError("git HEAD is unavailable during source verification")
611:     if head != base_sha:
612:         raise ValueError(f"git HEAD changed: expected {base_sha}, got {head}")
613:
614:     canonical_hashes = _require_dict(
615:         packet.get("canonical_hashes"), "packet.canonical_hashes"
616:     )
617:     required_canonical = {
618:         str(status_mod.TICKETS_CANONICAL),
619:         str(status_mod.STAGE_CANONICAL),
620:     }
621:     if not required_canonical.issubset(canonical_hashes):
622:         raise ValueError("packet.canonical_hashes is missing canonical files")
623:     for rel, expected in canonical_hashes.items():
624:         _validate_path_string(rel, "canonical_hashes key")
625:         expected = _require_hex64(expected, f"canonical_hashes[{rel!r}]")
626:         target = _existing_regular_file(root, rel, "canonical_hashes")
627:         if _sha256_file(target) != expected:
628:             raise ValueError(f"canonical metadata changed: {rel}")
629:
630:     read_paths = _require_str_list(packet.get("read_paths"), "packet.read_paths")
631:     edit_paths = _require_str_list(packet.get("edit_paths"), "packet.edit_paths")
632:     expected_paths = []
633:     seen_expected = set()
634:     for label, rels in (
635:         ("packet.read_paths[]", read_paths),
636:         ("packet.edit_paths[]", edit_paths),
637:     ):
638:         seen_in_list = set()
639:         for rel in rels:
640:             _validate_path_string(rel, label)
641:             if rel in seen_in_list:
642:                 raise ValueError(f"duplicate pinned path: {rel!r}")
643:             seen_in_list.add(rel)
644:             if rel not in seen_expected:
645:                 seen_expected.add(rel)
646:                 expected_paths.append(rel)
647:
648:     source_hashes = _require_dict(
649:         packet.get("source_hashes"), "packet.source_hashes"
650:     )
651:     for rel in source_hashes:
652:         _validate_path_string(rel, "source_hashes key")
653:     missing = [rel for rel in expected_paths if rel not in source_hashes]
654:     extra = sorted(rel for rel in source_hashes if rel not in seen_expected)
655:     if missing:
656:         raise ValueError(
657:             "packet.source_hashes is missing pinned paths: "
658:             + json.dumps(missing, ensure_ascii=False)
659:         )
660:     if extra:
661:         raise ValueError(
662:             "packet.source_hashes has extra pinned paths: "
663:             + json.dumps(extra, ensure_ascii=False)
664:         )
665:
666:     for rel in expected_paths:
667:         expected = source_hashes[rel]
668:         _check_no_symlink(root, rel, "source_hashes")
669:         target = root / rel
670:         if expected is None:
671:             if target.exists():
672:                 raise ValueError(f"pinned new path now exists: {rel}")
673:             continue
674:         expected = _require_hex64(expected, f"source_hashes[{rel!r}]")
675:         if not target.is_file():
676:             raise ValueError(f"pinned source file is missing: {rel}")
677:         if _sha256_file(target) != expected:
678:             raise ValueError(f"source file changed: {rel}")
679:     return True

854: def _raise_state_dir_component_error(exc, is_last, parent_fd, name):
855:     if exc.errno in (errno.ELOOP, errno.ENOTDIR):
856:         is_symlink = exc.errno == errno.ELOOP
857:         if not is_symlink:
858:             try:
859:                 entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
860:                 is_symlink = stat.S_ISLNK(entry.st_mode)
861:             except OSError:
862:                 pass
863:         if is_symlink:
864:             raise ValueError("state_dir traverses a symlink") from exc
865:         if is_last:
866:             raise ValueError("state_dir is not a directory") from exc
867:         raise ValueError("state_dir has a non-directory ancestor") from exc
868:     raise ValueError(f"cannot open state directory: {exc}") from exc

871: def _open_state_dir_component(parent_fd, name, is_last):
872:     flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
873:     try:
874:         return os.open(name, flags, dir_fd=parent_fd)
875:     except FileNotFoundError:
876:         pass
877:     except OSError as exc:
878:         _raise_state_dir_component_error(exc, is_last, parent_fd, name)
879:     try:
880:         os.mkdir(name, 0o700, dir_fd=parent_fd)
881:     except FileExistsError:
882:         pass
883:     except OSError as exc:
884:         raise ValueError(f"cannot create state directory: {exc}") from exc
885:     try:
886:         return os.open(name, flags, dir_fd=parent_fd)
887:     except OSError as exc:
888:         _raise_state_dir_component_error(exc, is_last, parent_fd, name)

891: def _open_private_state_leaf(state_dir):
892:     absolute = Path(state_dir).absolute()
893:     if str(absolute) == absolute.anchor:
894:         raise ValueError("state_dir must name a private leaf directory")
895:     try:
896:         dir_fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
897:     except OSError as exc:
898:         raise ValueError(f"cannot open state directory root: {exc}") from exc
899:     try:
900:         parts = absolute.parts[1:]
901:         for index, part in enumerate(parts):
902:             is_last = index == len(parts) - 1
903:             child_fd = _open_state_dir_component(dir_fd, part, is_last)
904:             os.close(dir_fd)
905:             dir_fd = child_fd
906:         if os.fstat(dir_fd).st_uid != os.getuid():
907:             raise ValueError("state_dir leaf is not owned by the current user")
908:         try:
909:             os.fchmod(dir_fd, 0o700)
910:         except OSError as exc:
911:             raise ValueError(f"cannot secure state directory: {exc}") from exc
912:         return dir_fd, absolute
913:     except BaseException:
914:         os.close(dir_fd)
915:         raise

918: def write_record(state_dir, packet, results, *, decision):
919:     """Append one private PENDING_CODEX_REVIEW receipt; never records acceptance."""
920:     if decision != DECISION_PENDING:
921:         raise ValueError(f"decision must be exactly {DECISION_PENDING!r}")
922:     packet = _require_dict(packet, "packet")
923:     if not isinstance(results, list):
924:         raise ValueError("results must be a list")
925:
926:     packet_hash = _canonical_json_hash(packet)
927:     results_hash = _canonical_json_hash(results)
928:
929:     leaf_fd, absolute = _open_private_state_leaf(state_dir)
930:     try:
931:         receipt = {
932:             "schema_version": SCHEMA_VERSION,
933:             "record_kind": "AGENT_TASK_RECEIPT",
934:             "decision": DECISION_PENDING,
935:             "provenance": PROVENANCE_UNTRUSTED,
936:             "host_execution_attested": False,
937:             "created_utc": datetime.now(timezone.utc).isoformat(),
938:             "task_id": packet.get("task_id"),
939:             "base_sha": packet.get("base_sha"),
940:             "model": packet.get("model"),
941:             "packet_sha256": packet_hash,
942:             "results_sha256": results_hash,
943:         }
944:         payload = (
945:             json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
946:         )
947:
948:         for _attempt in range(100):
949:             name = (
950:                 "receipt-"
951:                 + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
952:                 + "-"
953:                 + uuid.uuid4().hex
954:                 + ".json"
955:             )
956:             try:
957:                 fd = os.open(
958:                     name,
959:                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
960:                     0o600,
961:                     dir_fd=leaf_fd,
962:                 )
963:             except FileExistsError:
964:                 continue
965:             except OSError as exc:
966:                 raise ValueError(f"cannot create receipt: {exc}") from exc
967:             try:
968:                 os.fchmod(fd, 0o600)
969:                 data = memoryview(payload.encode("utf-8"))
970:                 while data:
971:                     written = os.write(fd, data)
972:                     data = data[written:]
973:                 os.fsync(fd)
974:             except OSError as exc:
975:                 raise ValueError(f"cannot write receipt: {exc}") from exc
976:             finally:
977:                 os.close(fd)
978:             try:
979:                 os.fsync(leaf_fd)
980:             except OSError:
981:                 pass
982:             receipt["path"] = str(absolute / name)
983:             return receipt
984:         raise ValueError("cannot allocate a unique receipt path")
985:     finally:
986:         os.close(leaf_fd)
END SOURCE
