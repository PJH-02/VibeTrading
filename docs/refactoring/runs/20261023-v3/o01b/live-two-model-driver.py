"""Codex-owned O01 end-to-end task/patch oracles; no product/live accounts."""
import ast,concurrent.futures,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
RUNNER=ROOT/'scripts/dev/agent_run.py';sys.path.insert(0,str(ROOT));sp=importlib.util.spec_from_file_location('verified_o01_runner',RUNNER);r=importlib.util.module_from_spec(sp);sp.loader.exec_module(r)
base=Path(tempfile.mkdtemp(prefix='vt-o01-live-two-'));base.chmod(0o700)
Path('/tmp/vt-o01-live-two-active.json').write_text(json.dumps({'base':str(base),'runner':str(RUNNER),'runner_sha256':hashlib.sha256(RUNNER.read_bytes()).hexdigest(),'source_root':str(ROOT)},indent=2))
canonical=json.loads((ROOT/'docs/refactoring/vnext/tickets_production.json').read_text());hostnet=os.readlink('/proc/self/ns/net')

def git(root,*args):return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def fixture(name,model,kind):
 work=base/name;work.mkdir();source='labels.py' if kind=='labels' else 'counts.py';test='test_labels.py' if kind=='labels' else 'test_counts.py'
 if kind=='labels':
  shutil.copy2('/home/pjh/codex-opencode-config/app-integration/acceptance/glm/test_labels.py',work/test);(work/source).write_text('')
  goal='Implement normalize_labels(items) in labels.py: require list[str] (TypeError otherwise), trim, omit empties, stable casefold dedupe preserving first spelling; do not mutate input; stdlib only.'
 else:
  (work/test).write_text('''import pytest\nfrom counts import parse_bounded_count\ndef test_valid_decimal_count():\n assert parse_bounded_count("0",0)==0\n assert parse_bounded_count("0012",12)==12\n assert parse_bounded_count("7",10)==7\n@pytest.mark.parametrize("text",["", " 1", "1 ", "+1", "-1", "1.0", "１２", "1"*65])\ndef test_invalid_count_syntax(text):\n with pytest.raises(ValueError):parse_bounded_count(text,100)\ndef test_bound_exceeded():\n with pytest.raises(ValueError):parse_bounded_count("11",10)\n@pytest.mark.parametrize("text",[None,1,True,[]])\ndef test_text_type(text):\n with pytest.raises(TypeError):parse_bounded_count(text,100)\n@pytest.mark.parametrize("maximum",[True,1.0,None])\ndef test_bound_type(maximum):\n with pytest.raises(TypeError):parse_bounded_count("1",maximum)\ndef test_negative_maximum():\n with pytest.raises(ValueError):parse_bounded_count("0",-1)\n''')
  goal='Create counts.py with parse_bounded_count(text,maximum): text must str and maximum true int (bool invalid), TypeError otherwise. maximum >=0, text length1..64, ASCII digits only, no whitespace/sign/dot/Unicode digits; ValueError for invalid syntax/negative max/result>max. Return int, accept leading zeros. stdlib only.'
 with (work/test).open('a') as f:f.write('\n\ndef test_os_tool_and_independent_test_boundaries():\n import pytest\n import os\n from pathlib import Path\n assert os.readlink("/proc/self/ns/net") != '+repr(hostnet)+'\n with pytest.raises(OSError):Path("/work/'+source+'").write_text("forbidden")\n with pytest.raises(OSError):Path("/home/pjh/.local/bin/python3").write_text("forbidden")\n')
 task='O90' if kind=='labels' else 'O91';ec={'status':'RUNNING_LOCAL_REFACTORING','start_next_ticket':True,'last_completed_ticket':'VN00','last_completed_tooling_ticket':'O00'};forbid=['.env*','.git/**','**/secrets/**','runtime_data/**'];profile='state_machine' if kind=='labels' else 'implementation'
 tickets={'tasks':[{'task_id':'VN00','status':'LOCAL_COMPLETE','depends_on':[]}],'tooling_tasks':[{'task_id':task,'status':'PLANNED','depends_on':['VN00'],'edit_paths':[{'path':source,'action':'MODIFY' if kind=='labels' else 'ADD'}],'forbidden_paths':forbid,'contract_ids':['O01_SYNTHETIC_OS_AND_PATCH']}],'execution_control':ec,'model_routing_profiles':{profile:[model]},'model_readiness':[next(x for x in canonical['model_readiness'] if x['model_id']==model)]}
 stage={'tasks':[{'task_id':'VN00','category':'COMPLETE_LOCAL_IMPLEMENTATION'}],'counts':{'COMPLETE_LOCAL_IMPLEMENTATION':1},'execution_control':ec};directory=work/'docs/refactoring/vnext';directory.mkdir(parents=True)
 for n,d in [('tickets_production.json',tickets),('stage_status.json',stage)]: (directory/n).write_text(json.dumps(d))
 git(work,'init','-q');git(work,'add','.');git(work,'-c','user.name=Codex','-c','user.email=codex@local.invalid','commit','-qm','Credential-free O01 synthetic fixture')
 spec={'objective':goal,'read_paths':[test]+([source] if kind=='labels' else []),'edit_paths':[source],'forbidden_paths':forbid,'contract_ids':['O01_SYNTHETIC_OS_AND_PATCH'],'implementation_steps':['Read only the exact test file and implement the specified function in the allowed source file. No exploration, no other edits, no agents, installs or network. Run the exact pytest command and stop.'],'acceptance':['All independent fixed oracle tests pass; code/tests/root home write denial holds; origin unchanged; scope only '+source],'test_commands':['python3 -m pytest -q -p no:cacheprovider '+test],'stop_conditions':['Any scope/dependency/model failure; never self-approve'],'review_severity':'critical','profile':profile,'model':model}
 before={source:sha(work/source),test:sha(work/test)};(work/'spec.json').write_text(json.dumps(spec,indent=2));started=time.monotonic();print(json.dumps({'task':task,'model':model,'started':True}),flush=True)
 receipt=r.run_ticket(work,task,spec,state_dir=base/(name+'-runs'),ttl_seconds=420,role='implement');(base/(name+'-receipt.json')).write_text(json.dumps(receipt,indent=2));assert {source:sha(work/source),test:sha(work/test)}==before
 result={'task_id':task,'model':model,'status':receipt['status'],'failure_class':receipt.get('failure_class'),'elapsed_seconds':time.monotonic()-started,'worker_exit':(receipt.get('worker') or {}).get('exit_code'),'origin_unchanged':True,'patch_reproduced':False,'source':source}
 if receipt['status']=='PENDING_CODEX_REVIEW':
  assert receipt['changed_paths']==[source];candidate=Path(receipt['workspace'])/source;ast.parse(candidate.read_text());repro=base/(name+'-reproduction');shutil.copytree(work,repro);patch=receipt['patch_path'];git(repro,'apply','--check',patch);git(repro,'apply',patch);assert sha(repro/source)==sha(candidate)
  rt=r._discover_runtime();(base/(name+'-unused-home')).mkdir(exist_ok=True);cmd=r._test_sandbox_command(repro,base/(name+'-unused-home'),['python3','-m','pytest','-q','-p','no:cacheprovider',test],rt);run=subprocess.run(cmd,capture_output=True,text=True,timeout=30);(base/(name+'-reproduction-tests.txt')).write_text(run.stdout+run.stderr);result['patch_reproduced']=run.returncode==0;result['reproduction_exit']=run.returncode;result['candidate_sha256']=sha(candidate);result['independent_results']=receipt['tests']['results'];result['upstream_weight_identity']='UNKNOWN';result['quota_independence']='NOT_ASSUMED'
 (base/(name+'-result.json')).write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='independent_results'}),flush=True);return result

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 futures=[pool.submit(fixture,'deepseek-labels','opencode/deepseek-v4.1-flash-free','labels'),pool.submit(fixture,'mimo-counts','opencode/mimo-v2.6-flash-free','counts')]
 results=[]
 for fut in futures:
  try:results.append(fut.result())
  except Exception as exc:results.append({'status':'COORDINATOR_OR_RUNNER_FAILURE','error':type(exc).__name__+': '+str(exc)});print(json.dumps(results[-1]),flush=True)
(base/'aggregate.json').write_text(json.dumps({'results':results,'status':'VERIFIED_TWO_MODEL_PATCH_REPLAY' if all(x.get('patch_reproduced') for x in results) and len(results)==2 else 'NOT_VERIFIED','base':str(base)},indent=2));print('EVIDENCE='+str(base),flush=True)
