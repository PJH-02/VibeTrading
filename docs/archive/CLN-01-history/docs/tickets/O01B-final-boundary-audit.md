# O01B final independent boundary source review — <=500 words
Read-onlyroleaudit, qualifiedfreellmapi/gemini-2.5-flash, differentfamilyfromDeepSeekcodewriter. Exactcurrentcriticalboundaryfunctions suppliedINLINE; no tools/exploration/edits/tests/agentsneeded. Returnconcreteremainingfindingswithfunction/line/mechanism/repro, orNO_CRITICAL_FINDINGS. DoNOTrepeatrequirements asreview orclaimexecutedtests/acceptance. Codexaloneaccepts.
ScopeFS/network/inference-secret/testacceptance boundaries ONLY. TrusthostCodex-authoredcanonicalpacket/spec andexistinghostproviderconfig; coremake_packet/verify_source alreadyseparatelyreviewedwithfalseprerequisite/root/symlink/pin/modelroute tests. Do notclaimfullsandboxagainstmalicioussameUIDhost/root/OSorhardCPU/memorycontainment. WorkerfilesystemcontainsnohostHOME/keys/proddata; exactapprovedsourcefilesonly, nativefree inferenceparentnetworkallowed, toolBashnetisolated. Publicruntime/depsRO. No paid/newaccount/autofallback/autodeploy/autoapply.
EvidenceprovidedseparatelybyCodex: 142unit+negativeboundarytestsPASS(plusnewfreshHOME-pytestprobe); twoactualqualifiedmodelsDeepSeekandMiMoeachwrotefunction+rantests; hostindependent8and19testsPASS; samepatchesreappliedfreshcopieswith8/19PASS, originalsunmodified. Prior6realbugs independentlyreproducedthenfixed: unknownfile/changedorigin notdetected, projectplugin/configexecution, testHOME/PATHpoison, safetyflagacceptedpending, relayredirectleakingSYNTHETICbearer. Newrealintegrationfixes: nativelocator excludesstablelauncherwrapperitself; freshHOMEremountspublicdepssoindependentpytestavailable. Reviewactualcodeforremainingbypasses, particularlysecretredirect/auth/body, dummykeyonly, projectpluginsdisabled, actualsourceRO/privateNET/testHOME, andnoPENDINGaftersafety/failure/sourcechange.
Callerunchangedhelpercontracts: _workspace_manifest enumeratesallnonsymlinkworkspacefilesexcludingfresh.git, checksbaseline/after andallowededits; _validate_snapshot_pins comparescopiedbytesbeforeinference; _collect_worker_artifacts readsactualnesteddelegateJSON/metadata/exit/tokenusage, scrubsrealkeybeforeexport; corecheck_testsrequiresnonemptyorderedreal0intresults+actualstdoutdigest; ownerlockglobalnormalizedorigin/TTL/cancelcontrolled. Tests coverthese; identifyassumptionratherthaninventomittedhelperbehavior. Nohostkeyliteralinthecodebelow, variable_secret isloadedONLYoutsideworkers. Actualupstreamweightsunknownandquotapoolsnotassumedindependent.
Source scripts/dev/agent_run.py sha256=caf09379aac6345d052b6ffc96c7556aeb4b5f1ed9503c7fe374005e293b4d11

BEGIN EXACT SOURCE
567: def _load_provider(provider_config, model):
568:     config = json.loads(Path(provider_config).read_text(encoding="utf-8"))
569:     selected = model.split("/", 1)[1]
570:     provider = (config.get("provider") or {}).get("freellmapi")
571:     if not isinstance(provider, dict):
572:         raise ValueError("freellmapi provider is not configured")
573:     models = provider.get("models")
574:     if not isinstance(models, dict) or selected not in models:
575:         raise ValueError(f"freellmapi model is not configured exactly: {selected!r}")
576:     options = provider.get("options") or {}
577:     reference = options.get("apiKey")
578:     base_url = options.get("baseURL")
579:     if not isinstance(reference, str) or not (
580:         reference.startswith("{file:") and reference.endswith("}")
581:     ):
582:         raise ValueError("freellmapi apiKey must use a file secret reference")
583:     if not isinstance(base_url, str):
584:         raise ValueError("freellmapi baseURL is missing")
585:     parsed = urlsplit(base_url)
586:     if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
587:         raise ValueError("freellmapi baseURL must be a loopback HTTP gateway")
588:     secret = Path(reference[6:-1]).expanduser().read_text(encoding="utf-8").strip()
589:     if not secret:
590:         raise ValueError("freellmapi secret is empty")
591:     return secret, base_url.rstrip("/"), models[selected]

612: class FreeLLMRelay:
613:     """Loopback relay that forwards only the exact selected chat completion."""
614:
615:     def __init__(self, upstream_base, secret, selected_model, timeout=75):
616:         self.upstream_base = _validated_loopback_base(str(upstream_base))
617:         self._secret = secret
618:         self.selected_model = selected_model
619:         if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
620:             raise ValueError("relay timeout must be numeric")
621:         if timeout <= 0:
622:             raise ValueError("relay timeout must be positive")
623:         self.timeout = min(float(timeout), float(MAX_RELAY_TIMEOUT))
624:         self.statuses = []
625:         self.served_models = set()
626:         self.retry_after = []
627:         self.redirects_blocked = 0
628:         self.redactions = 0
629:         self.safety_blocker = False
630:         self._server = None
631:         self._opener = build_opener(_NoRedirect)
632:
633:     @property
634:     def url(self):
635:         return f"http://127.0.0.1:{self._server.server_port}/v1"
636:
637:     def start(self):
638:         relay = self
639:
640:         class Handler(BaseHTTPRequestHandler):
641:             def log_message(self, *unused):
642:                 return
643:
644:             def do_POST(self):
645:                 try:
646:                     length = int(self.headers.get("Content-Length", "0") or "0")
647:                 except ValueError:
648:                     self.send_error(400)
649:                     return
650:                 if self.path != "/v1/chat/completions":
651:                     self.send_error(403)
652:                     return
653:                 if length <= 0 or length > MAX_RELAY_BODY:
654:                     self.send_error(413)
655:                     return
656:                 raw = self.rfile.read(length)
657:                 try:
658:                     payload = json.loads(raw)
659:                 except ValueError:
660:                     self.send_error(400)
661:                     return
662:                 if not isinstance(payload, dict) or payload.get("model") != relay.selected_model:
663:                     self.send_error(403)
664:                     return
665:                 request = Request(
666:                     relay.upstream_base + "/chat/completions",
667:                     data=raw,
668:                     headers={
669:                         "Authorization": "Bearer " + relay._secret,
670:                         "Content-Type": "application/json",
671:                     },
672:                 )
673:                 try:
674:                     try:
675:                         response = relay._opener.open(request, timeout=relay.timeout)
676:                     except HTTPError as exc:
677:                         response = exc
678:                         if exc.code in (301, 302, 303, 307, 308):
679:                             relay.redirects_blocked += 1
680:                     with response:
681:                         code = response.status
682:                         content_type = response.headers.get(
683:                             "Content-Type", "application/json"
684:                         )
685:                         retry_after = response.headers.get("Retry-After")
686:                         body = response.read(MAX_RELAY_RESPONSE)
687:                     relay.statuses.append(code)
688:                     if retry_after:
689:                         relay.retry_after.append(
690:                             {"status": code, "retry_after": str(retry_after)[:128]}
691:                         )
692:                     if relay._secret.encode("utf-8") in body:
693:                         relay.redactions += 1
694:                         relay.safety_blocker = True
695:                         body = body.replace(
696:                             relay._secret.encode("utf-8"), b"[REDACTED]"
697:                         )
698:                     for line in body.decode(errors="replace").splitlines():
699:                         candidate = line[6:] if line.startswith("data: ") else line
700:                         try:
701:                             item = json.loads(candidate)
702:                         except ValueError:
703:                             continue
704:                         if isinstance(item, dict) and item.get("model"):
705:                             relay.served_models.add(str(item["model"]))
706:                     self.send_response(code)
707:                     self.send_header("Content-Type", content_type)
708:                     if retry_after:
709:                         self.send_header("Retry-After", retry_after)
710:                     self.send_header("Content-Length", str(len(body)))
711:                     self.end_headers()
712:                     self.wfile.write(body)
713:                 except (OSError, TimeoutError, ValueError):
714:                     self.send_error(502, "Temporary inference relay unavailable")
715:
716:         self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
717:         threading.Thread(target=self._server.serve_forever, daemon=True).start()
718:         return self
719:
720:     def stop(self):
721:         if self._server is not None:
722:             self._server.shutdown()
723:             self._server.server_close()
724:             self._server = None
725:
726:     def observations(self):
727:         return {
728:             "http_statuses": list(self.statuses),
729:             "served_models": sorted(self.served_models),
730:             "retry_after": list(self.retry_after),
731:             "redirects_blocked": self.redirects_blocked,
732:             "redactions": self.redactions,
733:             "safety_blocker": self.safety_blocker,
734:         }

737: def _opencode_config(model, relay_url=None, model_entry=None):
738:     permission = {
739:         "*": "deny",
740:         "task": "deny",
741:         "question": "deny",
742:         "external_directory": "deny",
743:         "read": "allow",
744:         "edit": "allow",
745:         "bash": {"*": "deny", "python3 -m pytest*": "allow"},
746:         "glob": "deny",
747:         "grep": "deny",
748:         "webfetch": "deny",
749:         "websearch": "deny",
750:         "skill": "deny",
751:     }
752:     provider = {}
753:     if model.startswith("freellmapi/"):
754:         selected = model.split("/", 1)[1]
755:         provider = {
756:             "freellmapi": {
757:                 "npm": "@ai-sdk/openai-compatible",
758:                 "options": {
759:                     "baseURL": relay_url,
760:                     "apiKey": "o01b-local-relay-placeholder",
761:                 },
762:                 "models": {selected: model_entry or {"name": selected}},
763:             }
764:         }
765:     return {
766:         "$schema": "https://opencode.ai/config.json",
767:         "model": model,
768:         "small_model": model,
769:         "lsp": False,
770:         "formatter": False,
771:         "permission": permission,
772:         "provider": provider,
773:         "agent": {
774:             "plan": {
775:                 "prompt": (
776:                     "Read-only bounded audit. Read only the exact frozen source "
777:                     "excerpt named by the attached ticket, then return concrete "
778:                     "findings. No exploration, metadata search, Bash, agents, "
779:                     "edits, or user questions. Codex alone accepts."
780:                 )
781:             },
782:             "build": {
783:                 "prompt": (
784:                     "You are a bounded implementation worker. Read the attached "
785:                     "ticket and exact relevant source files directly. No repository "
786:                     "exploration, no subagents, no metadata changes. Implement exact "
787:                     "scope, run the specified pytest command, and stop. The "
788:                     "orchestrator alone accepts work."
789:                 )
790:             },
791:         },
792:     }

817: def _common_mounts(work, home, runtime, edit_paths, *, unshare_net=True):
818:     bwrap = _bwrap_path()
819:     if not bwrap:
820:         raise RuntimeError("bwrap is unavailable")
821:     args = [
822:         bwrap,
823:         "--unshare-user",
824:         "--unshare-pid",
825:         "--die-with-parent",
826:         "--new-session",
827:         "--cap-drop",
828:         "ALL",
829:     ]
830:     if unshare_net:
831:         args += ["--unshare-net"]
832:     args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/etc"]
833:     for source in ("/usr", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts",
834:                    "/etc/nsswitch.conf", "/etc/ld.so.cache", "/etc/alternatives"):
835:         if Path(source).exists():
836:             args += ["--ro-bind", source, source]
837:     for link, target in (("/bin", "usr/bin"), ("/lib", "usr/lib"), ("/lib64", "usr/lib64")):
838:         if Path(target).exists():
839:             args += ["--symlink", target, link]
840:         elif Path(link).exists():
841:             args += ["--ro-bind", link, link]
842:     args += ["--dir", "/home", "--bind", str(home), WORKER_HOME]
843:     if runtime is not None:
844:         native = Path(runtime.native)
845:         if native.is_file():
846:             args += [
847:                 "--ro-bind",
848:                 str(native.resolve()),
849:                 f"{WORKER_HOME}/.opencode/bin/opencode",
850:             ]
851:         for dep in runtime.python_deps:
852:             if Path(dep).exists():
853:                 args += ["--ro-bind", str(dep), str(dep)]
854:     args += ["--ro-bind", str(work), WORKER_ROOT]
855:     for rel in edit_paths:
856:         source = Path(work) / rel
857:         source.parent.mkdir(parents=True, exist_ok=True)
858:         args += ["--bind", str(source), str(Path(WORKER_ROOT) / rel)]
859:     return args

862: def _worker_command(runtime, work, home, shell, edit_paths, ticket_rel, role, model, ttl):
863:     # The API parent needs loopback/network; only Bash tool subprocesses are netless.
864:     args = _common_mounts(work, home, runtime, edit_paths, unshare_net=False)
865:     args += ["--ro-bind", str(shell), "/opt/bash"]
866:     args += [
867:         "--clearenv",
868:         "--setenv", "HOME", WORKER_HOME,
869:         "--setenv", "PATH", f"{WORKER_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
870:         "--setenv", "SHELL", "/opt/bash",
871:         "--setenv", "LANG", "C.UTF-8",
872:         "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
873:         "--setenv", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1",
874:         "--setenv", "PYTHONPATH",
875:         ":".join([WORKER_ROOT] + [str(item) for item in runtime.python_deps]),
876:         "--setenv", "GIT_OPTIONAL_LOCKS", "0",
877:         "--setenv", "OPENCODE_DELEGATE_TIMEOUT_SEC", str(ttl),
878:         "--setenv", "OPENCODE_PURE", "1",
879:         "--setenv", "OPENCODE_DISABLE_PROJECT_CONFIG", "1",
880:         "--setenv", "OPENCODE_DISABLE_DEFAULT_PLUGINS", "1",
881:         "--setenv", "OPENCODE_DISABLE_EXTERNAL_SKILLS", "1",
882:         "--setenv", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "1",
883:         "--setenv", "OPENCODE_DISABLE_LSP_DOWNLOAD", "1",
884:         "--chdir", WORKER_ROOT,
885:         "--",
886:         f"{WORKER_HOME}/.local/bin/delegate-opencode",
887:         role,
888:         ticket_rel,
889:         model,
890:     ]
891:     return args

894: def _test_sandbox_command(work, home, argv, runtime=None):
895:     args = _common_mounts(work, home, runtime, [], unshare_net=True)
896:     # Independent tests get a fresh tmpfs HOME so worker-installed executables
897:     # in HOME/.local/bin can never poison a later test command.
898:     args += ["--tmpfs", WORKER_HOME]
899:     # Fresh HOME hides prior submounts; expose only public dependencies again.
900:     if runtime is not None:
901:         for dep in runtime.python_deps:
902:             if Path(dep).exists():
903:                 args += ["--ro-bind", str(dep), str(dep)]
904:     py_path = [WORKER_ROOT]
905:     if runtime is not None:
906:         py_path += [str(item) for item in runtime.python_deps]
907:     args += [
908:         "--clearenv",
909:         "--setenv", "HOME", WORKER_HOME,
910:         "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
911:         "--setenv", "LANG", "C.UTF-8",
912:         "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
913:         "--setenv", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1",
914:         "--setenv", "PYTHONPATH", ":".join(py_path),
915:         "--setenv", "GIT_OPTIONAL_LOCKS", "0",
916:         "--chdir", WORKER_ROOT,
917:         "--",
918:         *argv,
919:     ]
920:     return args

1322: def run_ticket(root, task_id, spec, *, state_dir=None, ttl_seconds=600, role="implement"):
1323:     """Execute one bounded attempt and return an untrusted receipt."""
1324:     _validate_ttl(ttl_seconds)
1325:     if not isinstance(role, str) or not role.strip():
1326:         raise ValueError("role must be a non-empty string")
1327:     root = Path(root)
1328:     packet = dev.make_packet(root, task_id, spec)
1329:     parsed_commands = parse_test_commands(packet["test_commands"])
1330:     if not packet["model"].startswith(SUPPORTED_MODEL_PREFIXES):
1331:         raise ValueError(f"unknown or unsupported model route: {packet['model']!r}")
1332:     packet_sha = _canonical_hash(packet)
1333:     origin_root = _normalized_origin(root)
1334:     state_root = _state_root(state_dir)
1335:
1336:     lock_fd = _acquire_root_lock(state_root, origin_root)
1337:     if lock_fd is None:
1338:         return {
1339:             "status": "FAILURE",
1340:             "decision": None,
1341:             "failure_class": FAILURE_LOCK,
1342:             "failure_message": "another writer holds the origin-root lock",
1343:             "task_id": task_id,
1344:             "packet_sha256": packet_sha,
1345:             "host_execution_attested": False,
1346:             "tests": {"status": "NOT_RUN", "results": []},
1347:             "provenance": dev.PROVENANCE_UNTRUSTED,
1348:         }
1349:     try:
1350:         existing = _duplicate(state_root, origin_root, packet_sha)
1351:         if existing is not None:
1352:             return {
1353:                 "status": "DUPLICATE",
1354:                 "decision": None,
1355:                 "task_id": task_id,
1356:                 "packet_sha256": packet_sha,
1357:                 "original_run_id": existing.get("run_id"),
1358:                 "host_execution_attested": False,
1359:                 "tests": {"status": "NOT_RUN", "results": []},
1360:                 "provenance": dev.PROVENANCE_UNTRUSTED,
1361:             }
1362:
1363:         dev.verify_source(root, packet)
1364:         if not _bwrap_path():
1365:             return {
1366:                 "status": "FAILURE",
1367:                 "decision": None,
1368:                 "failure_class": FAILURE_BWRAP,
1369:                 "failure_message": "bwrap is not installed; refusing to launch",
1370:                 "task_id": task_id,
1371:                 "packet_sha256": packet_sha,
1372:                 "host_execution_attested": False,
1373:                 "tests": {"status": "NOT_RUN", "results": []},
1374:                 "provenance": dev.PROVENANCE_UNTRUSTED,
1375:             }
1376:
1377:         origin_baseline = _origin_baseline(root)
1378:         run_id = (
1379:             datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
1380:             + "-"
1381:             + uuid.uuid4().hex[:12]
1382:         )
1383:         nonce = uuid.uuid4().hex
1384:         run_dir = _ensure_private_dir(state_root / run_id)
1385:         created = datetime.now(timezone.utc)
1386:         deadline = time.monotonic() + ttl_seconds
1387:         deadline_epoch = created.timestamp() + ttl_seconds
1388:         state = {
1389:             "run_id": run_id,
1390:             "nonce": nonce,
1391:             "task_id": task_id,
1392:             "origin_root": origin_root,
1393:             "created_utc": created.isoformat(),
1394:             "deadline_utc": datetime.fromtimestamp(
1395:                 deadline_epoch, timezone.utc
1396:             ).isoformat(),
1397:             "ttl_seconds": ttl_seconds,
1398:             "role": role,
1399:             "model": packet["model"],
1400:             "provider": packet["provider"],
1401:             "route": packet["route"],
1402:             "packet_sha256": packet_sha,
1403:             "source_hashes_before": dict(packet["source_hashes"]),
1404:             "origin_baseline": origin_baseline,
1405:             "status": "RUNNING",
1406:             "run_dir": str(run_dir),
1407:             "expired": False,
1408:             "cancelled": False,
1409:         }
1410:         _write_json_private(run_dir / "state.json", state)
1411:
1412:         if _is_cancelled(state_root, run_id):
1413:             return _failure_receipt(
1414:                 state, FAILURE_CANCELLED, "cancelled before launch",
1415:                 state_root=state_root,
1416:             )
1417:         if time.monotonic() >= deadline:
1418:             state["expired"] = True
1419:             return _failure_receipt(
1420:                 state, dev.FAILURE_TIMEOUT, "deadline expired before launch",
1421:                 state_root=state_root,
1422:             )
1423:         if origin_baseline.get("is_repo"):
1424:             origin_exit = origin_baseline["diff_check"].get("exit_code")
1425:             if origin_exit not in (0, 2):
1426:                 return _failure_receipt(
1427:                     state, FAILURE_DIAGNOSTICS,
1428:                     f"origin git diff --check exited unexpectedly: {origin_exit}",
1429:                     state_root=state_root,
1430:                 )
1431:
1432:         attempt = None
1433:         worker_ran = False
1434:         tests_ran = 0
1435:         try:
1436:             remaining = max(1, int(deadline - time.monotonic()))
1437:             attempt = _prepare_attempt(
1438:                 root, packet, state_root, run_id, role, ttl_seconds,
1439:                 relay_timeout=min(MAX_RELAY_TIMEOUT, remaining),
1440:             )
1441:             state["workspace"] = str(attempt["work"])
1442:             state["snapshot_baseline"] = {
1443:                 "git_diff_check": attempt["baseline_diff"],
1444:                 "manifest_count": len(attempt["baseline_manifest"]),
1445:                 "manifest_sha256": _canonical_hash(attempt["baseline_manifest"]),
1446:             }
1447:             state["runtime"] = attempt.get("runtime_observations", {})
1448:             _write_json_private(run_dir / "state.json", state)
1449:             if _is_cancelled(state_root, run_id):
1450:                 return _failure_receipt(
1451:                     state, FAILURE_CANCELLED, "cancelled before worker launch",
1452:                     state_root=state_root,
1453:                 )
1454:             if time.monotonic() >= deadline:
1455:                 state["expired"] = True
1456:                 return _failure_receipt(
1457:                     state, dev.FAILURE_TIMEOUT,
1458:                     "deadline expired during preparation",
1459:                     state_root=state_root,
1460:                 )
1461:
1462:             worker = _run_worker_process(
1463:                 attempt["worker_command"],
1464:                 work=attempt["work"],
1465:                 run_dir=run_dir,
1466:                 deadline=deadline,
1467:                 cancel_check=lambda: _is_cancelled(state_root, run_id),
1468:                 state_root=state_root,
1469:                 run_id=run_id,
1470:                 timeout_seconds=ttl_seconds,
1471:             )
1472:             worker_ran = True
1473:
1474:             raw_artifacts = _collect_opencode_artifacts(attempt["home"], run_dir)
1475:             log_paths = [worker["stdout_path"], worker["stderr_path"]]
1476:             log_paths += raw_artifacts.get("artifact_paths", [])
1477:             if attempt["secret"]:
1478:                 redactions = _scrub_secret(log_paths, attempt["secret"])
1479:                 if redactions:
1480:                     state["safety_blocker"] = True
1481:                     state["safety_redactions"] = redactions
1482:             artifacts = _collect_opencode_artifacts(attempt["home"], run_dir)
1483:             worker["dispatcher_exit_code"] = worker.get("exit_code")
1484:             worker["dispatcher_token_usage"] = worker.get("token_usage")
1485:             if artifacts.get("token_usage") is not None:
1486:                 worker["token_usage"] = artifacts["token_usage"]
1487:             worker["selected_route"] = packet["route"]
1488:             worker["observed_models"] = artifacts.get("observed_models", [])
1489:             worker["actual_weights"] = "UNKNOWN"
1490:             worker["actual_errors"] = artifacts.get("errors", [])
1491:             worker["tool_states"] = artifacts.get("tool_states", [])
1492:             worker["opencode_logs"] = artifacts.get("opencode_logs", [])
1493:             worker["metadata_files"] = artifacts.get("metadata_files", [])
1494:             worker["worker_exit_files"] = artifacts.get("worker_exit_files", [])
1495:             worker["raw_log_paths"] = log_paths
1496:             state["worker"] = worker
1497:             state["observability"] = {
1498:                 "selected_route": packet["route"],
1499:                 "observed_models": artifacts.get("observed_models", []),
1500:                 "actual_weights": "UNKNOWN",
1501:                 "actual_worker_exit": worker.get("exit_code"),
1502:                 "dispatcher_exit": worker.get("dispatcher_exit_code"),
1503:                 "raw_log_paths": log_paths,
1504:                 "tool_states": artifacts.get("tool_states", []),
1505:             }
1506:             if attempt["relay"]:
1507:                 observations = attempt["relay"].observations()
1508:                 state["relay"] = observations
1509:                 if observations.get("safety_blocker"):
1510:                     state["safety_blocker"] = True
1511:                 observed = sorted(
1512:                     set(worker.get("observed_models", []))
1513:                     | set(observations.get("served_models", []))
1514:                 )
1515:                 worker["observed_models"] = observed
1516:                 state["observability"]["observed_models"] = observed
1517:             _write_json_private(run_dir / "state.json", state)
1518:
1519:             if state.get("safety_blocker"):
1520:                 return _failure_receipt(
1521:                     state, FAILURE_SAFETY,
1522:                     "secret material or relay safety blocker detected",
1523:                     state_root=state_root,
1524:                     host_execution_attested=False,
1525:                 )
1526:
1527:             after_manifest = _workspace_manifest(attempt["work"])
1528:             after = {
1529:                 rel: after_manifest.get(rel) for rel in packet["source_hashes"]
1530:             }
1531:             state["source_hashes_after"] = after
1532:             scope_error = None
1533:             changed = []
1534:             try:
1535:                 changed = dev.check_scope(
1536:                     attempt["baseline_manifest"], after_manifest, packet["edit_paths"]
1537:                 )
1538:             except ValueError as exc:
1539:                 scope_error = str(exc)
1540:             state["changed_paths"] = changed
1541:             state["modified_hashes"] = {
1542:                 rel: after_manifest.get(rel) for rel in changed
1543:             }
1544:             new_diff = _diff_check(attempt["work"])
1545:             state["diff_check"] = {
1546:                 "baseline": attempt["baseline_diff"],
1547:                 "new": new_diff,
1548:             }
1549:             baseline_output = attempt["baseline_diff"].get("output", "")
1550:             if baseline_output:
1551:                 state["preexisting_diagnostics"] = baseline_output
1552:             patch_error = None
1553:             patch = ""
1554:             try:
1555:                 patch_before = {
1556:                     rel: packet["source_hashes"].get(rel) for rel in changed
1557:                 }
1558:                 patch_after = {
1559:                     rel: after_manifest.get(rel) for rel in changed
1560:                 }
1561:                 patch = _make_patch(
1562:                     root, attempt["work"], patch_before, patch_after
1563:                 )
1564:             except (OSError, UnicodeError) as exc:
1565:                 patch_error = str(exc)
1566:             patch_path = run_dir / "candidate.patch"
1567:             _write_private(patch_path, patch)
1568:             state["patch_path"] = str(patch_path)
1569:             _write_json_private(run_dir / "state.json", state)
1570:
1571:             if _is_cancelled(state_root, run_id) or worker.get("cancelled"):
1572:                 state["cancelled"] = True
1573:                 return _failure_receipt(
1574:                     state, FAILURE_CANCELLED, "cancelled during worker execution",
1575:                     state_root=state_root, host_execution_attested=False,
1576:                 )
1577:             if worker.get("timed_out"):
1578:                 state["expired"] = True
1579:                 return _failure_receipt(
1580:                     state, dev.FAILURE_TIMEOUT,
1581:                     "worker deadline exceeded; partial workspace preserved",
1582:                     state_root=state_root,
1583:                 )
1584:             verify_error = None
1585:             try:
1586:                 dev.verify_source(root, packet)
1587:             except ValueError as exc:
1588:                 verify_error = str(exc)
1589:             if verify_error:
1590:                 return _failure_receipt(
1591:                     state, FAILURE_SOURCE, verify_error,
1592:                     state_root=state_root,
1593:                 )
1594:             if scope_error:
1595:                 return _failure_receipt(
1596:                     state, dev.FAILURE_SCOPE, scope_error,
1597:                     state_root=state_root,
1598:                 )
1599:             if patch_error:
1600:                 return _failure_receipt(
1601:                     state, dev.FAILURE_CONTRACT, patch_error,
1602:                     state_root=state_root,
1603:                 )
1604:             if new_diff.get("exit_code") not in (0, 2):
1605:                 return _failure_receipt(
1606:                     state, FAILURE_DIAGNOSTICS,
1607:                     "new diff-check exited unexpectedly: "
1608:                     f"{new_diff.get('exit_code')}",
1609:                     state_root=state_root,
1610:                 )
1611:             if new_diff.get("output") and new_diff.get("output") != baseline_output:
1612:                 return _failure_receipt(
1613:                     state, FAILURE_DIAGNOSTICS,
1614:                     "new diff-check diagnostics detected",
1615:                     state_root=state_root,
1616:                 )
1617:             if worker.get("effective_exit_code") != 0:
1618:                 exit_code = worker.get("effective_exit_code")
1619:                 statuses = list(state.get("relay", {}).get("http_statuses", []))
1620:                 error_text = "\n".join(worker.get("actual_errors", []))
1621:                 stderr_text = _bounded_text(worker.get("stderr_path", ""))
1622:                 combined = (error_text + "\n" + stderr_text).strip()
1623:                 if 429 in statuses:
1624:                     failure_class = dev.FAILURE_RATE_LIMIT
1625:                 elif 401 in statuses or 403 in statuses:
1626:                     failure_class = dev.FAILURE_AUTH
1627:                 else:
1628:                     failure_class = dev.classify_failure(
1629:                         exit_code, combined or f"worker exited {exit_code}"
1630:                     )
1631:                 message = f"worker exited {exit_code}"
1632:                 if combined:
1633:                     message += ": " + combined.splitlines()[-1][:300]
1634:                 return _failure_receipt(
1635:                     state, failure_class, message,
1636:                     state_root=state_root,
1637:                 )
1638:
1639:             results = []
1640:             if time.monotonic() >= deadline:
1641:                 state["expired"] = True
1642:                 return _failure_receipt(
1643:                     state, dev.FAILURE_TIMEOUT,
1644:                     "deadline expired before independent tests",
1645:                     state_root=state_root,
1646:                 )
1647:             for index, (command, argv) in enumerate(
1648:                 zip(packet["test_commands"], parsed_commands), start=1
1649:             ):
1650:                 if _is_cancelled(state_root, run_id):
1651:                     state["cancelled"] = True
1652:                     return _failure_receipt(
1653:                         state, FAILURE_CANCELLED,
1654:                         "cancelled between worker and tests",
1655:                         state_root=state_root,
1656:                         host_execution_attested=False,
1657:                     )
1658:                 result = _run_test_process(
1659:                     argv,
1660:                     command=command,
1661:                     work=attempt["work"],
1662:                     home=attempt["home"],
1663:                     run_dir=run_dir,
1664:                     index=index,
1665:                     runtime=attempt["runtime"],
1666:                     deadline=deadline,
1667:                     cancel_check=lambda: _is_cancelled(state_root, run_id),
1668:                     state_root=state_root,
1669:                     run_id=run_id,
1670:                 )
1671:                 tests_ran += 1
1672:                 results.append(result)
1673:                 if result.get("cancelled") or _is_cancelled(state_root, run_id):
1674:                     state["cancelled"] = True
1675:                     state["tests"] = {"status": "NOT_VERIFIED", "results": results}
1676:                     return _failure_receipt(
1677:                         state, FAILURE_CANCELLED,
1678:                         "cancelled during independent tests",
1679:                         state_root=state_root,
1680:                         host_execution_attested=False,
1681:                     )
1682:                 if result.get("timed_out"):
1683:                     state["expired"] = True
1684:                     state["tests"] = {"status": "NOT_VERIFIED", "results": results}
1685:                     return _failure_receipt(
1686:                         state, dev.FAILURE_TIMEOUT,
1687:                         "deadline exceeded during independent tests",
1688:                         state_root=state_root,
1689:                         host_execution_attested=False,
1690:                     )
1691:                 if result.get("exit_code") != 0:
1692:                     state["tests"] = {"status": "NOT_VERIFIED", "results": results}
1693:                     _write_json_private(run_dir / "state.json", state)
1694:                     return _failure_receipt(
1695:                         state, dev.FAILURE_TEST,
1696:                         f"test command failed: {command}",
1697:                         state_root=state_root,
1698:                         host_execution_attested=False,
1699:                     )
1700:             verified = dev.check_tests(packet["test_commands"], results)
1701:             post_test_error = None
1702:             try:
1703:                 dev.verify_source(root, packet)
1704:             except ValueError as exc:
1705:                 post_test_error = str(exc)
1706:             if post_test_error:
1707:                 state["tests"] = {"status": "NOT_VERIFIED", "results": results}
1708:                 return _failure_receipt(
1709:                     state, FAILURE_SOURCE, post_test_error,
1710:                     state_root=state_root,
1711:                 )
1712:             after_tests = _workspace_manifest(attempt["work"])
1713:             if after_tests != after_manifest:
1714:                 return _failure_receipt(
1715:                     state, dev.FAILURE_SCOPE,
1716:                     "candidate changed while tests were running",
1717:                     state_root=state_root,
1718:                 )
1719:             core_receipt = dev.write_record(
1720:                 state_root, packet, results, decision=dev.DECISION_PENDING
1721:             )
1722:             state.update(
1723:                 status="PENDING_CODEX_REVIEW",
1724:                 decision=dev.DECISION_PENDING,
1725:                 tests={"status": verified["status"], "results": results},
1726:                 host_execution_attested=True,
1727:                 finished_utc=_utc_now(),
1728:                 core_receipt_path=core_receipt.get("path"),
1729:             )
1730:             _write_json_private(run_dir / "state.json", state)
1731:             return {
1732:                 "status": "PENDING_CODEX_REVIEW",
1733:                 "decision": dev.DECISION_PENDING,
1734:                 "failure_class": None,
1735:                 "run_id": run_id,
1736:                 "task_id": task_id,
1737:                 "nonce": nonce,
1738:                 "created_utc": state["created_utc"],
1739:                 "deadline_utc": state["deadline_utc"],
1740:                 "model": packet["model"],
1741:                 "provider": packet["provider"],
1742:                 "route": packet["route"],
1743:                 "selected_route": packet["route"],
1744:                 "observed_models": worker.get("observed_models", []),
1745:                 "actual_weights": "UNKNOWN",
1746:                 "packet_sha256": packet_sha,
1747:                 "host_execution_attested": True,
1748:                 "workspace": str(attempt["work"]),
1749:                 "worker": worker,
1750:                 "changed_paths": changed,
1751:                 "modified_hashes": state["modified_hashes"],
1752:                 "patch_path": str(patch_path),
1753:                 "tests": {"status": verified["status"], "results": results},
1754:                 "relay": state.get("relay", {}),
1755:                 "safety_blocker": bool(state.get("safety_blocker")),
1756:                 "origin_baseline": state.get("origin_baseline"),
1757:                 "runtime": state.get("runtime", {}),
1758:                 "observability": state.get("observability", {}),
1759:                 "core_receipt_path": core_receipt.get("path"),
1760:                 "provenance": dev.PROVENANCE_UNTRUSTED,
1761:             }
1762:         except _SourceDrift as exc:
1763:             return _failure_receipt(
1764:                 state, FAILURE_SOURCE, str(exc),
1765:                 state_root=state_root,
1766:                 host_execution_attested=False,
1767:             )
1768:         except Exception as exc:
1769:             state["finished_utc"] = _utc_now()
1770:             return _failure_receipt(
1771:                 state, dev.FAILURE_CONTRACT, str(exc),
1772:                 state_root=state_root,
1773:                 host_execution_attested=worker_ran and tests_ran == len(parsed_commands),
1774:             )
1775:         finally:
1776:             if attempt is not None and attempt["relay"] is not None:
1777:                 attempt["relay"].stop()
1778:     finally:
1779:         try:
1780:             fcntl.flock(lock_fd, fcntl.LOCK_UN)
1781:         finally:
1782:             os.close(lock_fd)
END SOURCE
