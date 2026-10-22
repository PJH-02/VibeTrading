"""Coordinator bootstrap, not accepted O01 runner; exact scopes, no auto apply."""
import argparse,json,os,shutil,subprocess,tempfile,hashlib,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('config');a=p.parse_args();cfg=json.loads(Path(a.config).read_text())
root=Path(cfg['root']);base=Path(tempfile.mkdtemp(prefix='vt-bounded-'));work=base/'work';home=base/'home';work.mkdir();home.mkdir();os.chmod(base,0o700)
paths=set(cfg['read_paths']+cfg['edit_paths']+[cfg['ticket']])
for rel in sorted(paths):
 src=root/rel;dst=work/rel;dst.parent.mkdir(parents=True,exist_ok=True)
 if src.is_file():shutil.copy2(src,dst)
 elif rel in cfg['edit_paths']:dst.touch()
 else:raise ValueError('Missing approved input '+rel)
for rel,source in cfg.get('extra_inputs',{}).items():
 dst=work/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dst)
if cfg.get('seed_work'):
 for rel in cfg['edit_paths']:
  shutil.copy2(Path(cfg['seed_work'])/rel,work/rel)
subprocess.run(['git','init','-q',str(work)],check=True)
subprocess.run(['git','-C',str(work),'add','.'],check=True)
subprocess.run(['git','-C',str(work),'-c','user.name=Codex','-c','user.email=codex@local.invalid','commit','-qm','Pinned bounded worker inputs'],check=True)
bins=home/'.local/bin';bins.mkdir(parents=True)
shutil.copy2('/home/pjh/.local/bin/delegate-opencode',bins/'delegate-opencode')
(bins/'opencode').write_text('#!/bin/sh\nexec /opt/opencode "$@"\n');(bins/'opencode').chmod(0o700)
configdir=home/'.config/opencode';configdir.mkdir(parents=True)
model=cfg['model'];provider={}
from types import SimpleNamespace
import threading,urllib.request,urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
args=SimpleNamespace(model=model)
served_models=set();relay_statuses=[];secret_redactions=[];relay=None
if args.model.startswith('freellmapi/'):
    configured=json.loads(Path('/home/pjh/.config/opencode/opencode.json').read_text())['provider']['freellmapi']
    reference=configured['options']['apiKey']
    assert reference.startswith('{file:') and reference.endswith('}')
    gateway_key=Path(reference[6:-1]).expanduser().read_text().strip()
    selected=args.model.split('/',1)[1]
    class Relay(BaseHTTPRequestHandler):
        def log_message(self,*unused): pass
        def do_POST(self):
            length=int(self.headers.get('Content-Length','0'))
            if self.path!='/v1/chat/completions' or not 0<length<1048576:
                self.send_error(403);return
            body=self.rfile.read(length)
            if json.loads(body).get('model')!=selected:
                self.send_error(403);return
            request=urllib.request.Request(configured['options']['baseURL'].rstrip('/')+'/chat/completions',data=body,headers={'Authorization':'Bearer '+gateway_key,'Content-Type':'application/json'})
            try:
                try: response=urllib.request.urlopen(request,timeout=75)
                except urllib.error.HTTPError as exc: response=exc
                with response:
                    code=response.status;content_type=response.headers.get('Content-Type','application/json');retry_after=response.headers.get('Retry-After');raw=response.read(4194304)
                relay_statuses.append(code)
                if gateway_key.encode() in raw:
                    secret_redactions.append(True);raw=raw.replace(gateway_key.encode(),b'[REDACTED]')
                for line in raw.decode(errors='replace').splitlines():
                    try:
                        item=json.loads(line[6:] if line.startswith('data: ') else line)
                        if isinstance(item,dict) and item.get('model'): served_models.add(item['model'])
                    except ValueError: pass
                self.send_response(code);self.send_header('Content-Type',content_type)
                if retry_after:self.send_header('Retry-After',retry_after)
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            except (OSError,TimeoutError):
                self.send_error(502,'Temporary inference relay unavailable')
    relay=ThreadingHTTPServer(('127.0.0.1',0),Relay)
    threading.Thread(target=relay.serve_forever,daemon=True).start()
    provider={'freellmapi':{'npm':'@ai-sdk/openai-compatible','options':{'baseURL':f'http://127.0.0.1:{relay.server_port}/v1','apiKey':'o00-local-relay-placeholder'},'models':{selected:configured['models'][selected]}}}
assert model.startswith(('opencode/','freellmapi/'))

permission={'*':'deny','task':'deny','question':'deny','external_directory':'deny','read':'allow','edit':'allow','bash':{'*':'deny','python3 -m pytest*':'allow'},'glob':'deny','grep':'deny','webfetch':'deny','websearch':'deny','skill':'deny'}
(configdir/'opencode.json').write_text(json.dumps({'$schema':'https://opencode.ai/config.json','model':model,'small_model':model,'lsp':False,'formatter':False,'permission':permission,'provider':provider,'agent':{'plan':{'prompt':'Read-only bounded audit. Read only the exact frozen source excerpt named by the attached ticket, then immediately return concrete findings. No exploration, metadata search, Bash, agents, edits, or user questions. The excerpt plus ticket is complete context. Codex alone accepts.'},'build':{'prompt':'You are a bounded implementation worker. Read the attached ticket and exact relevant source files directly. No repository exploration, no subagents, no metadata changes. Implement exact scope, run the specified pytest command, and stop. The orchestrator alone accepts work.'}}}))
shell=base/'bash';shell.write_text('#!/bin/sh\nexec /usr/bin/bwrap --unshare-user --unshare-pid --unshare-net --die-with-parent --new-session --cap-drop ALL --ro-bind / / --proc /proc --dev /dev --bind /tmp /tmp -- /bin/bash "$@"\n');shell.chmod(0o700)
common=['bwrap','--unshare-user','--unshare-pid','--die-with-parent','--new-session','--cap-drop','ALL','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc']
for f in ['/etc/ssl/certs','/etc/resolv.conf','/etc/hosts','/etc/nsswitch.conf','/etc/ld.so.cache','/etc/alternatives']:common+=['--ro-bind',f,f]
common+=['--dir','/home','--bind',str(home),'/home/pjh','--ro-bind','/home/pjh/.local/lib/python3.12/site-packages','/home/pjh/.local/lib/python3.12/site-packages','--ro-bind','/home/pjh/.cache/vt-refactor-deps','/home/pjh/.cache/vt-refactor-deps','--ro-bind',str(work),'/work','--dir','/opt','--ro-bind',str(Path('/home/pjh/.nvm/versions/node/v22.22.0/bin/opencode').resolve()),'/opt/opencode','--ro-bind',str(shell),'/opt/bash']
for f in cfg['edit_paths']:common+=['--bind',str(work/f),'/work/'+f]
common+=['--clearenv','--setenv','HOME','/home/pjh','--setenv','PATH','/home/pjh/.local/bin:/usr/local/bin:/usr/bin:/bin','--setenv','SHELL','/opt/bash','--setenv','LANG','C.UTF-8','--setenv','PYTHONDONTWRITEBYTECODE','1','--setenv','PYTEST_DISABLE_PLUGIN_AUTOLOAD','1','--setenv','PYTHONPATH','/work:/home/pjh/.cache/vt-refactor-deps','--setenv','GIT_OPTIONAL_LOCKS','0','--setenv','OPENCODE_DELEGATE_TIMEOUT_SEC',str(cfg.get('timeout',600)),'--chdir','/work']
meta={**cfg,'base':str(base),'work':str(work),'home':str(home),'common':common,'input_hashes':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() if (root/f).is_file() else None for f in paths},'base_sha':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()}
(base/'metadata.json').write_text(json.dumps(meta,indent=2));Path(cfg['active_file']).write_text(json.dumps(meta,indent=2));print(json.dumps({'base':str(base),'model':model,'ticket':cfg['ticket']}),flush=True)
started=time.monotonic()
with (base/'dispatcher.txt').open('wb') as out:r=subprocess.run(common+['--','/home/pjh/.local/bin/delegate-opencode',cfg.get('role','implement'),cfg['ticket'],model],stdout=out,stderr=subprocess.STDOUT)
if relay:
 relay.shutdown()
 for log in (home/'.local/state/codex-opencode/runs').glob('*/opencode.log'):
  assert gateway_key not in log.read_text(errors='replace'),'Credential found in worker output; quarantine artifacts'
(base/'exit.json').write_text(json.dumps({'exit_code':r.returncode,'elapsed_seconds':time.monotonic()-started,'relay_http_statuses':relay_statuses,'served_models':sorted(served_models),'key_redaction_events':len(secret_redactions)}));print((base/'exit.json').read_text(),flush=True)
