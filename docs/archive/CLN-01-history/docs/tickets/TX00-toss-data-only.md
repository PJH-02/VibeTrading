# TX00/TOSS DATA_ONLY — Close legacy mutation path

task_id: TX00/toss-data-only (boundedsubunit, parentTX00 remainsINCOMPLETE)
base_sha: 1d0b60d (exactcurrentHEAD recordedbyouterreceipt)
prerequisites: VN00/R00 localcomplete; v3TX00 explicit TOSSDATA_ONLY policy
role: implement
model_profile: implementation; exact freellmapi/glm-4.5-flash (O00 qualified; selectedFreeLLMAPI worker, no paid/accountchanges)
read_paths: vibetrading/brokers.py::TossBrokerAdapter, vibetrading/providers/toss.py::TossAuthClient, tests/test_collector_recovery_20261007.py, vibetrading/trading_contracts.py::validate_scope
edit_paths: vibetrading/brokers.py, vibetrading/providers/toss.py, tests/test_collector_recovery_20261007.py, tests/refactoring/test_tx00_toss_data_only.py (ADD)
forbidden_paths: allotherfiles, .env/secrets/credentials/realaccounts/data, ordernetwork, canonicalmetadata
contract_ids: C10 TOSSDATA_ONLY, existing read-refreshboundedness
review_severity: criticalpolicyboundary; Codex independentreview
stop_conditions: policyambiguity, outofscopechange, missingtestdependency, anyrealnetwork/accountneed

## Objective / implementation_steps
Thegenericnewtrading_contracts.validate_scope rejectsTOSS, but legacybrokers.TossBrokerAdapter.submit_order builds POST/api/v1/orders and TossAuthClient.request/_request_once stillallow it. ThiscontradictsuserTOSSDATA_ONLY. Implementactualnegativegates, notjustcapabilitylabel. Noaccount orproviderAPIaccessneeded.
1. Replace ONLY TossBrokerAdapter.submit_order body with immediate RuntimeError containingTOSS andDATA_ONLY, BEFOREanyintentattribute/client/accountlookup. Keepfactory/GETreadmethods/intents and otherbrokersunchanged; do notremoveTosscollector/authentication. A syntheticintent=None muststillfailDATA_ONLY without sideeffects.
2. Add earlyGET-onlymutationgate BOTH TossAuthClient.request (beforeaccess_token) and _request_once (beforeheaders/session/network), RuntimeErrorTOSSDATA_ONLY. UnknownnonGETmethods includingPOST/PUT/PATCH/DELETE/HEAD mustreject. ExistingOAuth tokenPOST is in separate_authmethod and MUSTremainavailableforread-onlymarketdata; do notblanketbanallhttpPOSTelsewhere orchangeauthcacherefresh. ExistingGETexactrequestpath/params/body/accountcontext andone401refreshretrybehaviorunchanged. Normalizecaseexplicitly, invalidmethodfailsbeforeauth. No newroute overrideflags or bypasssettings.
3. Existingparameterized test_rest_refresh_bounded_and_orders_not_replayed includesPOSTexpecting401afteronecall. Split GETrefreshcases from mutation-policycase: GETmuststilltryatmost2andpreservebehavior; mutationnowblockedwithZERO access_token/refresh/_request_once calls. Thisisintentionalcontractstrengthening, do notdeleteGETnegativecases orweakenassertions. Add focusednewregressions parameterizedmutatingmethods/case andbroker.submit_order withbombclient/intent provingnoattribute/clientcallbacks; direct_request_once rejects beforeaiohttp.ClientSession. Add evidenceGETdispatchstillworkswithsynthetictransportandOAuthauthpath notremoved. No networkserver/accounts in tests; monkeypatch fakeobjects only.

## Acceptance / exact test_commands
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_tx00_toss_data_only.py tests/test_collector_recovery_20261007.py
Only4allowedfilesmaychange. Do notedittrading_contracts.py/otherbrokers/strategies/metadata/deps. No globalformatting, workeriimportsaccounts, installs, Gitcommands oragents. Readselectedfunctionsfilesdirectly; otherpackagefilesare readonlyimportdependencies. CurrentBashOSprivateNETblocksrealnetwork, publicpytestdepsareprovided; no needtoinstall. Return actualcounts/exit, changedfunctions, note5Venuecapabilityauditstillpending; no selfapproval. Max2repairs.
