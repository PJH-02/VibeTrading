"""Independent TX00 acceptance probes; arguments: snapshot JSON with work path, receipt JSON path. Never executes product modules. Run in an offline credential-free snapshot."""
from pathlib import Path
import json,ast,importlib.util,sys,types
sys.dont_write_bytecode=True
active=Path(sys.argv[1]);m=json.loads(active.read_text());root=Path(m['work']);p=root/'docs/refactoring/vnext/reuse_audit/tx00_current/check.py';spec=importlib.util.spec_from_file_location('tx00check',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
target='vibetrading.brokers.broker_for_name';cases=[
 ('module_call',"from vibetrading.brokers import broker_for_name as f\nf('toss')\n",[target]),
 ('parameter_shadow',"from vibetrading.brokers import broker_for_name as f\ndef g(f): f()\n",[]),
 ('local_import',"def g():\n from vibetrading.brokers import broker_for_name as f\n f('toss')\n",[target]),
 ('reassigned',"from vibetrading.brokers import broker_for_name as f\ndef g():\n f=lambda:None\n f()\n",[]),
 ('nested_once',"def outer():\n from vibetrading.brokers import broker_for_name as f\n def inner(): f('toss')\n",[target]),
 ('class_body',"from vibetrading.brokers import broker_for_name as f\nclass C:\n x=f('toss')\n",[target]),
 ('unknown_factory_receiver',"from vibetrading.brokers import broker_for_name as f\ndef g(): f('toss').submit_order(None)\n",[target]),
 ('ambiguous_import',"from vibetrading.brokers import broker_for_name as f\nfrom somewhere_unknown import something as f\nf()\n",[]),
 ('ambiguous_branch_import',"if flag:\n from somewhere_unknown import something as f\nelse:\n from vibetrading.brokers import broker_for_name as f\nf()\n",[]),
 ('wildcard_shadow',"from vibetrading.brokers import broker_for_name as f\nfrom somewhere_unknown import *\nf()\n",[]),
 ('lambda_shadow',"from vibetrading.brokers import broker_for_name as f\nx=lambda f:f()\n",[]),
 ('relative_package_init',"from ..brokers import broker_for_name as f\nf('toss')\n",[target]),
]
results=[]
for name,source,expected in cases:
 path='src/vibetrading/probe/__init__.py' if name=='relative_package_init' else 'tests/probe.py';tree=ast.parse(source);c=mod.CallCollector(path,tree,{'vibetrading.brokers'})
 for n in tree.body:c.visit(n)
 actual=[x['target'] for x in c.calls];results.append({'case':name,'passed':actual==expected,'expected':expected,'actual':actual})
real=mod.subprocess.run
try:
 mod.subprocess.run=lambda *a,**k:types.SimpleNamespace(returncode=1,stdout='',stderr='simulated git unavailable')
 outputs,summary=mod.build(root);results.append({'case':'git_failure_must_not_scan_untracked','passed':bool(summary.get('errors')),'actual_errors':summary.get('errors',[]),'actual_input_source':outputs.get('callers.json',{}).get('candidate_input_source')})
 mod.subprocess.run=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout='tests/probe_missing.py\n',stderr='')
 outputs,summary=mod.build(root);results.append({'case':'missing_tracked_candidate_blocks_success','passed':bool(summary.get('errors')),'actual_errors':summary.get('errors',[]),'actual_parse_errors':outputs.get('callers.json',{}).get('parse_errors')})
finally:mod.subprocess.run=real
r={'decision':'PASS' if all(x['passed'] for x in results) else 'FAIL','cases':len(results),'passed':sum(x['passed'] for x in results),'failed':sum(not x['passed'] for x in results),'results':results,'fault_scope':'Synthetic AST snippets and mocked git results only; no product source/account/network mutation'}
out=Path(sys.argv[2]);out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='results'}));print('failed_cases',[x['case'] for x in results if not x['passed']])

sys.exit(0 if r["decision"]=="PASS" else 1)
