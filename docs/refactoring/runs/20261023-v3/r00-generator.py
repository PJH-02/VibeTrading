import ast
import collections
import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import yaml

ROOT = Path('/mnt/c/Users/박제형/Desktop/VibeTrading')
OUT = ROOT / 'docs/refactoring/vnext/repo_inventory.json'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

head = git('rev-parse', 'HEAD').decode().strip()
entries = {}
for line in git('ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
    if line:
        meta, name = line.split(b'\t', 1)
        mode, kind, sha = meta.decode().split()
        entries[name.decode()] = {'mode': mode, 'kind': kind, 'blob': sha}
status = [x.decode() for x in git('status', '--porcelain=v1', '-z').split(b'\0') if x]
changed = [x[3:] for x in status if x[:2] != '??']
selected = [p for p in changed if p in entries and entries[p]['kind'] == 'blob' and (ROOT/p).is_file()]
payload = ''.join(entries[p]['blob']+'\n' for p in selected).encode()
raw = subprocess.check_output(['git', 'cat-file', '--batch'], input=payload, cwd=ROOT)
stream = io.BytesIO(raw)
deltas = []
for name in selected:
    header = stream.readline().split()
    original = stream.read(int(header[2])); stream.read(1)
    current = (ROOT/name).read_bytes()
    row = {'path': name, 'head_sha256': digest(original), 'worktree_sha256': digest(current)}
    if original == current:
        row['classification'] = 'GIT_METADATA_ONLY'
    elif original.replace(b'\r\n', b'\n') == current.replace(b'\r\n', b'\n'):
        row['classification'] = 'LINE_ENDINGS_ONLY'
    elif name.endswith('.py'):
        try:
            same = ast.dump(ast.parse(original), include_attributes=False) == ast.dump(ast.parse(current), include_attributes=False)
            row['classification'] = 'PYTHON_AST_EQUIVALENT' if same else 'SEMANTIC_DELTA_REVIEW_REQUIRED'
        except SyntaxError:
            row['classification'] = 'PARSE_ERROR_REVIEW_REQUIRED'
    else:
        row['classification'] = 'CONTENT_DELTA_REVIEW_REQUIRED'
    deltas.append(row)

py_names = [p for p in entries if p.endswith('.py') and (ROOT/p).is_file() and not (ROOT/p).is_symlink()]
modules = {p: p[:-3].replace('/', '.').removesuffix('.__init__') for p in py_names}
known = set(modules.values())
imports = {}; dynamic = []; entrypoints = []; errors = []; source_hashes = {}
for name in py_names:
    raw = (ROOT/name).read_bytes(); source_hashes[name] = digest(raw)
    try:
        tree = ast.parse(raw, filename=name)
    except SyntaxError as exc:
        errors.append({'path': name, 'line': exc.lineno, 'type': type(exc).__name__}); continue
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(x.name for x in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = modules[name] if name.endswith('/__init__.py') else modules[name].rpartition('.')[0]
                parts = package.split('.') if package else []
                prefix = '.'.join(parts[:len(parts)-node.level+1])
                base = '.'.join(x for x in [prefix, node.module] if x)
            else:
                base = node.module or ''
            targets.add(base)
            targets.update(base+'.'+x.name for x in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            label = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ''
            if label in {'__import__','import_module','spec_from_file_location','load_module','entry_points'}:
                first = node.args[0] if node.args else None
                dynamic.append({'path': name, 'line': node.lineno, 'loader': label, 'target': first.value if isinstance(first, ast.Constant) and isinstance(first.value,str) else 'UNKNOWN_DYNAMIC_EXPRESSION'})
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == '__name__':
            if any(isinstance(x, ast.Constant) and x.value == '__main__' for x in node.comparators):
                entrypoints.append({'path': name, 'line': node.lineno})
    imports[name] = sorted(t for t in targets if t in known or any(k.startswith(t+'.') for k in known if t))

compose = {}
compose_paths = [p for p in entries if p.endswith(('.yml','.yaml')) and ('compose' in p or p.startswith('deploy/'))]
for name in compose_paths:
    try:
        data = yaml.safe_load((ROOT/name).read_text())
    except (OSError, yaml.YAMLError):
        continue
    if not isinstance(data,dict) or not isinstance(data.get('services'),dict): continue
    compose[name] = {service:{'command': cfg.get('command'), 'entrypoint': cfg.get('entrypoint'), 'profiles':cfg.get('profiles',[]), 'volumes':cfg.get('volumes',[]), 'environment_keys':sorted(cfg.get('environment',{})) if isinstance(cfg.get('environment',{}),dict) else 'LIST_FORM', 'build':cfg.get('build')} for service,cfg in data['services'].items()}
dockerfiles = {}
for name in ['Dockerfile','Dockerfile.crypto']:
    if (ROOT/name).is_file():
        dockerfiles[name] = [line.strip() for line in (ROOT/name).read_text().splitlines() if line.startswith(('COPY ','WORKDIR ','CMD ','ENTRYPOINT '))]

roots = ['main.py','run_strategy.py','vibetrading/cli.py','vibetrading/local_runtime.py','vibetrading/runtime.py']
by_module = {v:k for k,v in modules.items()}
reachable = set(); todo = list(roots)
while todo:
    name=todo.pop()
    if name in reachable: continue
    reachable.add(name)
    todo.extend(by_module[x] for x in imports.get(name,[]) if x in by_module and by_module[x] not in reachable)
ownership = {}
for name in py_names:
    if name.startswith('tests/'): group='TEST'
    elif name.startswith(('backtest/','research/')): group='RESEARCH'
    elif name == 'scripts/refactoring/deploy_p02_schema.py': group='PROTECTED_ROLLBACK'
    elif name in reachable: group='STATIC_RUNTIME_REACHABLE'
    elif name.startswith(('services/','shared/','strategy/','strategies/','vibetrading/fv_mm/legacy/')): group='COMPAT_REQUIRED_OR_RESEARCH_REVIEW'
    else: group='UNKNOWN_UNCLASSIFIED_NOT_DELETABLE'
    ownership[name]=group
stage=json.loads((ROOT/'docs/refactoring/vnext/stage_status.json').read_text())
tickets=json.loads((ROOT/'docs/refactoring/vnext/tickets_production.json').read_text())
missing_receipts=[]
for task in tickets['tasks']:
    for key,value in task.items():
        if isinstance(value,str) and value.startswith('docs/') and value.endswith(('.json','.md')) and not (ROOT/value).exists():
            missing_receipts.append({'task_id':task['task_id'],'field':key,'path':value})
protected=['.env*','**/secrets/**','**/*.pem','**/*.key','runtime_data/**','ops-ledger/**','data/**','.git/**','docs/evidence/**','docs/refactoring/runs/20261018-stage-assignment/**','docs/refactoring/runs/20261020-production/**','scripts/refactoring/deploy_p02_schema.py']
report={
 'schema_version':1,'task_id':'R00','created_kst':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
 'source_sha':head,'document_baseline_sha':'a9a83af43897fdf8badc303aaf3b6ebf2eda87c5','head_matches_document_baseline':head=='a9a83af43897fdf8badc303aaf3b6ebf2eda87c5',
 'branch':git('branch','--show-current').decode().strip(),'worktrees':git('worktree','list','--porcelain').decode(),
 'tracked_paths':len(entries),'initial_worktree_status':status,'dirty_deltas':deltas,'dirty_classification_counts':dict(collections.Counter(x['classification'] for x in deltas)),
 'source_set_sha256':digest(json.dumps(source_hashes,sort_keys=True,separators=(',',':')).encode()),'python_source_hashes':source_hashes,
 'authority_documents':{p:{'tracked_at_head':p in entries,'sha256':digest((ROOT/p).read_bytes())} for p in ['docs/refactoring/vnext/VibeTrading_Refactoring_Final_Plan_v3.md','docs/refactoring/vnext/VibeTrading_Developer_Ticket_Spec_v3.md','docs/refactoring/vnext/VibeTrading_Architecture_Requirements_v3.md']},
 'python_entrypoints':entrypoints,'python_parse_errors':errors,'static_local_import_graph':imports,'dynamic_imports':dynamic,'ownership':ownership,'ownership_counts':dict(collections.Counter(ownership.values())),
 'compose_entrypoints':compose,'dockerfile_paths':dockerfiles,'runtime_authority':{'deployed':'UNKNOWN_CURRENT_SERVER_NOT_CONTACTED','recorded_server_scope':'P02 only; historical receipts are not current operational proof','nats_compose':'docker-compose.yml retained; NATS not stopped','candidate_nats_free':['vibetrading/local_runtime.py','vibetrading/local_engine.py','vibetrading/local_transport.py'],'production_mutations':0},
 'protected_paths':protected,'rollback':{'path':'scripts/refactoring/deploy_p02_schema.py','exists':(ROOT/'scripts/refactoring/deploy_p02_schema.py').is_file(),'sha256':digest((ROOT/'scripts/refactoring/deploy_p02_schema.py').read_bytes())},
 'canonical_stage_counts':stage['counts'],'last_completed_ticket':stage['last_completed_ticket'],'legacy_pause_superseded_by_current_user_request':True,
 'original_task_ids':[x['task_id'] for x in tickets['tasks']],'missing_direct_receipt_references':missing_receipts,'deletion_candidates':[],
 'acceptance_limits':['Static Python AST coverage only; dynamic UNKNOWN retained','No current server/production/account inspection','No implementation completion inferred from previous test counts','No product file edits'],
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['task_id','source_sha','tracked_paths','dirty_classification_counts','source_set_sha256','ownership_counts','canonical_stage_counts']},ensure_ascii=False))
print('entrypoints',len(entrypoints),'dynamic_imports',len(dynamic),'parse_errors',len(errors),'compose_files',len(compose),'missing_direct_receipts',len(missing_receipts))
