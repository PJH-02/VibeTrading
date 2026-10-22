from pathlib import Path
import importlib.util,json,subprocess,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from types import SimpleNamespace
sp=importlib.util.spec_from_file_location('bfixtures','/work/tests/refactoring/test_agent_run.py');f=importlib.util.module_from_spec(sp);sp.loader.exec_module(f);r=f.runner

def test_untracked_file_is_scope_failure(tmp_path,monkeypatch):
 root=f._prepare(tmp_path,monkeypatch);monkeypatch.setattr(r,'_run_worker_process',f._fake_worker({'src/target.py':'changed\n','surprise.py':'unapproved\n'}));monkeypatch.setattr(r,'_run_test_process',f._fake_test())
 out=r.run_ticket(root,'VN01',f.harness._default_spec(),state_dir=tmp_path/'runs')
 assert out['status']=='FAILURE' and out['failure_class']=='SCOPE_VIOLATION'

def test_origin_changed_during_tests_is_source_failure(tmp_path,monkeypatch):
 root=f._prepare(tmp_path,monkeypatch);monkeypatch.setattr(r,'_run_worker_process',f._fake_worker({'src/target.py':'changed\n'}));monkeypatch.setattr(r,'_run_test_process',f._fake_test(mutate=lambda work:(root/'src/target.py').write_text('concurrent edit\n')))
 out=r.run_ticket(root,'VN01',f.harness._default_spec(),state_dir=tmp_path/'runs')
 assert out['status']=='FAILURE' and out['failure_class']=='SOURCE_CHANGED'

def test_pure_and_project_config_are_disabled(tmp_path):
 rt=f._runtime(tmp_path);work=tmp_path/'work';work.mkdir();home=tmp_path/'home';home.mkdir();shell=tmp_path/'bash';shell.touch()
 cmd=r._worker_command(rt,work,home,shell,[],'ticket.md','implement','opencode/x',30)
 env={cmd[i+1]:cmd[i+2] for i,x in enumerate(cmd) if x=='--setenv'}
 assert env.get('OPENCODE_PURE')=='1' and env.get('OPENCODE_DISABLE_PROJECT_CONFIG')=='1'

def test_test_path_excludes_worker_home_bins(tmp_path):
 work=tmp_path/'work';work.mkdir();home=tmp_path/'home';(home/'.local/bin').mkdir(parents=True)
 cmd=r._test_sandbox_command(work,home,['python3','-c','import os; assert "/home/pjh/.local/bin" not in os.environ["PATH"]'])
 proc=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
 assert proc.returncode==0,proc.stderr

def test_secret_echo_is_failure_not_pending(tmp_path,monkeypatch):
 root=f._prepare(tmp_path,monkeypatch);original=r._prepare_attempt
 class Relay:
  safety_blocker=True
  def observations(self):return {'redactions':1,'safety_blocker':True,'http_statuses':[200]}
  def stop(self):pass
 def prep(*args,**kwargs):
  result=original(*args,**kwargs);result['relay']=Relay();result['secret']='synthetic-private';return result
 monkeypatch.setattr(r,'_prepare_attempt',prep);monkeypatch.setattr(r,'_run_worker_process',f._fake_worker({'src/target.py':'changed\n'}));monkeypatch.setattr(r,'_run_test_process',f._fake_test())
 out=r.run_ticket(root,'VN01',f.harness._default_spec(),state_dir=tmp_path/'runs')
 assert out['status']=='FAILURE' and out['failure_class']=='SAFETY_BLOCKER'

def test_relay_does_not_forward_auth_to_redirect_destination():
 hits=[]
 class Victim(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def do_GET(self):
   hits.append(self.headers.get('Authorization'));self.send_response(200);self.end_headers();self.wfile.write(b'{}')
 victim=ThreadingHTTPServer(('127.0.0.1',0),Victim);threading.Thread(target=victim.serve_forever,daemon=True).start()
 class Redirect(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def do_POST(self):
   self.rfile.read(int(self.headers.get('Content-Length',0)));self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{victim.server_port}/steal');self.end_headers()
 upstream=ThreadingHTTPServer(('127.0.0.1',0),Redirect);threading.Thread(target=upstream.serve_forever,daemon=True).start();relay=r.FreeLLMRelay(f'http://127.0.0.1:{upstream.server_port}/v1','SYNTHETIC_ONLY_SECRET','x').start()
 try:
  req=Request(relay.url+'/chat/completions',data=json.dumps({'model':'x'}).encode(),headers={'Content-Type':'application/json'})
  try:urlopen(req,timeout=5).read()
  except HTTPError:pass
  assert hits==[], 'Authorization was forwarded outside the configured gateway endpoint'
 finally:relay.stop();upstream.shutdown();upstream.server_close();victim.shutdown();victim.server_close()
