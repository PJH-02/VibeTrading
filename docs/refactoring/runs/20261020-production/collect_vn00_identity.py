"""Collect bounded, read-only VN00 deployment identity metadata over SSH.

This records identity and hashes only. It never runs application/account code,
prints environment values, changes server state, or proves deployment readiness.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[4]
IDENTITY = ROOT / "docs/refactoring/vnext/server_readonly_identity.json"
TARGET = "49.50.141.106"
CONNECT_TIMEOUT = 8
SSH_TIMEOUT = 240
FULL_SOURCE_SSH_TIMEOUT = 720
REMOTE_COMMAND_TIMEOUT = 10
FULL_SOURCE_TIMEOUT = 30
MAX_COMPOSE_BYTES = 1_048_576


def _allowed_source_path(relative: str) -> bool:
    parts = relative.lower().split("/")
    name = parts[-1]
    if any(part == ".env" or part.startswith(".env.") or part in {"secrets", "private"}
           for part in parts):
        return False
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm")):
        return False
    return True


def _read_allowlists() -> tuple[list[str], dict[str, dict[str, str]], dict[str, str]]:
    prior = json.loads(IDENTITY.read_text(encoding="utf-8"))
    names = []
    sources: dict[str, dict[str, str]] = {}
    for item in prior.get("containers", []):
        name = item.get("name", "")
        if not re.fullmatch(r"vibetrading[-a-z0-9]+-[0-9]+", name):
            raise ValueError("historical identity contains an invalid container name")
        names.append(name)
        source_files = item.get("files", {})
        if not isinstance(source_files, dict):
            raise ValueError("historical identity source hashes are malformed")
        allowed_files = {}
        for relative, digest in source_files.items():
            if (not isinstance(relative, str) or relative.startswith("/") or
                    any(part in ("", ".", "..") for part in relative.split("/")) or
                    not re.fullmatch(r"[A-Za-z0-9_.\-/]+", relative)):
                raise ValueError("historical identity contains an invalid source path")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("historical identity contains an invalid source hash")
            if _allowed_source_path(relative):
                allowed_files[relative] = digest
        sources[name] = dict(sorted(allowed_files.items()))
    if len(names) != 14 or len(set(names)) != len(names):
        raise ValueError("expected the pinned 14-container identity list")
    compose = {}
    for path, digest in prior.get("compose_sha256", {}).items():
        prefix = "/opt/vibetrading/releases/telegram-recovery-20261014/"
        if not path.startswith(prefix) or Path(path).name not in {"runtime.yml", "dashboard.yml"}:
            raise ValueError("historical identity contains an unexpected Compose path")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("historical Compose hash is invalid")
        compose[path] = digest
    if set(Path(p).name for p in compose) != {"runtime.yml", "dashboard.yml"}:
        raise ValueError("expected pinned runtime.yml and dashboard.yml hashes")
    return names, sources, compose


REMOTE = r'''import datetime,hashlib,json,os,pathlib,re,subprocess
NAMES=__NAMES_JSON__
SOURCES=__SOURCES_JSON__
COMPOSE=__COMPOSE_JSON__
FULL_SOURCE=__FULL_SOURCE__
SCAN_CODE=__SCAN_CODE_JSON__
ROOT=pathlib.Path('/opt/vibetrading/releases/telegram-recovery-20261014')
MAX_COMPOSE=1048576
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def run(args,timeout=8):
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout,check=False)
        return p.returncode,p.stdout
    except subprocess.TimeoutExpired:
        return None,b''
out={'schema_version':1,'status':'READ_ONLY_IDENTITY_ONLY_NOT_CUTOVER_PROOF',
     'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
     'host':{'cpu_count':os.cpu_count(),'memory_bytes':{},'disk':[]},
     'compose_files':{},'containers':{},'collection_errors':[]}
try:
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        if line.startswith(('MemTotal:','MemAvailable:')):
            k,v,*_=line.split();out['host']['memory_bytes'][k[:-1]]=int(v)*1024
except Exception as e: out['collection_errors'].append({'area':'meminfo','error':type(e).__name__})
rc,raw=run(['df','-B1','-P','--','/','/srv/vibetrading-data/runtime_data','/srv/vibetrading-crypto-state','/srv/vibetrading-data/nats'],timeout=5)
if rc==0:
    lines=raw.decode('utf-8','replace').splitlines()[1:]
    for line in lines:
        parts=line.split()
        if len(parts)>=6:
            try: out['host']['disk'].append({'filesystem':parts[0],'size_bytes':int(parts[1]),'used_bytes':int(parts[2]),'available_bytes':int(parts[3]),'use_percent':parts[4],'mount':parts[5]})
            except ValueError: pass
else: out['collection_errors'].append({'area':'disk','error':'command_failed_or_timeout'})
for path,old_hash in COMPOSE.items():
    item={'historical_sha256':old_hash,'max_bytes':MAX_COMPOSE}
    try:
        p=pathlib.Path(path);st=p.stat()
        if st.st_size>MAX_COMPOSE: raise ValueError('size_limit_exceeded')
        with p.open('rb') as f: b=f.read(MAX_COMPOSE+1)
        if len(b)>MAX_COMPOSE: raise ValueError('size_limit_exceeded')
        item.update({'size_bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'matches_historical':hashlib.sha256(b).hexdigest()==old_hash})
    except Exception as e: item['error']=type(e).__name__
    out['compose_files'][path]=item
for name in NAMES:
    role={'status':'ERROR'}
    fields=['.Id','.Image','(index .Config "Image")','(index .Config "WorkingDir")',
            '(index .Config "User")','(index .Config "Labels")',
            '(index .Config "Healthcheck")','(index .Config "StopSignal")',
            '(index .Config "Tty")','(index .Config "OpenStdin")',
            '(index .Config "Env")','.HostConfig','.Mounts','.State','.RestartCount']
    template='\n'.join('{{json '+f+'}}' for f in fields)
    rc,raw=run(['docker','inspect','--format',template,name],timeout=5)
    if rc!=0:
        role['error']='inspect_failed_or_timeout';out['containers'][name]=role;continue
    try:
        vals=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
        if len(vals)!=len(fields): raise ValueError('inspect_field_count_mismatch')
        (container_id,image_id,image_tag,working_dir,user,labels,healthcheck,stop_signal,tty,
         open_stdin,env,host_config,mount_data,state,restart_count)=vals
        labels=labels or {}
        mounts=[]
        for m in mount_data or []:
            mounts.append({'source':m.get('Source'),'target':m.get('Destination'),'read_only':not bool(m.get('RW'))})
        safe_cfg={'Image':image_tag,'WorkingDir':working_dir,'User':user,'Labels':labels,
                  'Healthcheck':healthcheck,'StopSignal':stop_signal,'Tty':tty,'OpenStdin':open_stdin}
        role={'status':'OK','container_id':container_id,'image_id':image_id,'image_tag':image_tag,
              'service':labels.get('com.docker.compose.service'),'project':labels.get('com.docker.compose.project'),
              'state':(state or {}).get('Status'),'health':((state or {}).get('Health') or {}).get('Status'),
              'restart_count':restart_count,'working_dir':working_dir,
              'config_sha256':digest({'Config':safe_cfg,'HostConfig':host_config,'Mounts':mount_data}),
              'process_config_sha256':digest({k:safe_cfg.get(k) for k in ('User','WorkingDir','Healthcheck','StopSignal','Tty','OpenStdin')}),
              'env_identity_sha256':digest(sorted(env or [])),
              'mounts':mounts,'source_hashes':{},'source_errors':[]}
        expected_sources=SOURCES.get(name,{})
        paths=sorted(expected_sources)
        if paths:
            wd=working_dir or '/app'
            if not wd.startswith('/') or '..' in pathlib.PurePosixPath(wd).parts: raise ValueError('invalid_working_dir')
            args=['docker','exec',container_id,'sha256sum','--']+[str(pathlib.PurePosixPath(wd)/x) for x in paths]
            src_rc,src_raw=run(args,timeout=10)
            for line in src_raw.decode('utf-8','replace').splitlines():
                m=re.fullmatch(r'([0-9a-f]{64})\s+(.+)',line)
                if m:
                    rel=m.group(2).removeprefix(wd.rstrip('/')+'/')
                    current=m.group(1)
                    role['source_hashes'][rel]={'sha256':current,
                        'historical_sha256':expected_sources.get(rel),
                        'matches_historical':current==expected_sources.get(rel)}
            if src_rc!=0: role['source_errors'].append('source_hash_command_failed_or_timeout')
            absent=sorted(set(paths)-set(role['source_hashes']))
            if absent: role['source_errors'].append({'unhashed_paths':absent})
            if role['source_errors']: role['status']='PARTIAL'
        if FULL_SOURCE:
            if not paths:
                role['python_source_manifest']={'status':'NOT_APPLICABLE_NO_PYTHON_SOURCE_BASELINE','complete':None,'files':[]}
            elif (working_dir or '/app').rstrip('/')!='/app':
                role['python_source_manifest']={'status':'ERROR','root':'/app/vibetrading','complete':False,'files':[],'errors':['working_directory_not_app']}
                role['status']='PARTIAL'
            else:
                scan_rc,scan_raw=run(['docker','exec',container_id,'python3','-c',SCAN_CODE],timeout=30)
                if scan_rc==0:
                    try:
                        role['python_source_manifest']=json.loads(scan_raw.decode('utf-8'))
                        if not role['python_source_manifest'].get('complete'): role['status']='PARTIAL'
                    except Exception as e:
                        role['python_source_manifest']={'status':'ERROR','root':'/app/vibetrading','complete':False,'files':[],'errors':[type(e).__name__]}
                        role['status']='PARTIAL'
                else:
                    role['python_source_manifest']={'status':'ERROR','root':'/app/vibetrading','complete':False,'files':[],'errors':['source_scan_failed_or_timeout']}
                    role['status']='PARTIAL'
    except Exception as e:
        role={'status':'ERROR','error':type(e).__name__}
    out['containers'][name]=role
if out['collection_errors'] or any(v.get('status')!='OK' for v in out['containers'].values()) or any('error' in v for v in out['compose_files'].values()):
    out['status']='PARTIAL_IDENTITY'
print(json.dumps(out,sort_keys=True,separators=(',',':')))
'''


SCAN_CODE = r'''import hashlib,json,os,stat
ROOT='/app/vibetrading';MAX_ENTRIES=4096;MAX_FILES=2048;MAX_FILE_BYTES=1048576;MAX_TOTAL_BYTES=33554432
files=[];errors=[];entries=0;total=0;pending=[];stopped=False
try:
    dflags=os.O_RDONLY|os.O_DIRECTORY|getattr(os,'O_NOFOLLOW',0)
    if not hasattr(os,'O_NOFOLLOW'): raise OSError('nofollow_unavailable')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    for component in ('app','vibetrading'):
        nxt=os.open(component,dflags,dir_fd=fd);os.close(fd);fd=nxt
    pending=[(fd,'')]
except Exception as e:
    try: os.close(fd)
    except Exception: pass
    print(json.dumps({'status':'ERROR','root':ROOT,'complete':False,'limits':{'entries':MAX_ENTRIES,'files':MAX_FILES,'file_bytes':MAX_FILE_BYTES,'total_bytes':MAX_TOTAL_BYTES},'entries_seen':0,'files_hashed':0,'total_bytes':0,'files':[],'errors':[type(e).__name__]},sort_keys=True,separators=(',',':')))
    raise SystemExit(0)
while pending and not stopped:
    dirfd,rel_dir=pending.pop()
    try:
        with os.scandir(dirfd) as it:
            names=[]
            for entry in it:
                entries+=1
                if entries>MAX_ENTRIES:
                    errors.append({'path':rel_dir,'reason':'entry_limit'});stopped=True;break
                names.append(entry.name)
    except Exception as e:
        errors.append({'path':rel_dir,'reason':type(e).__name__});os.close(dirfd);continue
    names.sort()
    dirs=[]
    for name in names:
        rel=(rel_dir+'/'+name).lstrip('/')
        try: info=os.stat(name,dir_fd=dirfd,follow_symlinks=False)
        except Exception as e: errors.append({'path':rel,'reason':type(e).__name__});continue
        if stat.S_ISLNK(info.st_mode): errors.append({'path':rel,'reason':'symlink_skipped'});continue
        if stat.S_ISDIR(info.st_mode):
            try: dirs.append((os.open(name,dflags,dir_fd=dirfd),rel))
            except Exception as e: errors.append({'path':rel,'reason':type(e).__name__})
            continue
        if not stat.S_ISREG(info.st_mode) or not name.endswith('.py'): continue
        if len(files)>=MAX_FILES: errors.append({'path':rel,'reason':'file_limit'});stopped=True;break
        if info.st_size>MAX_FILE_BYTES: errors.append({'path':rel,'reason':'file_size_limit'});continue
        if total+info.st_size>MAX_TOTAL_BYTES: errors.append({'path':rel,'reason':'total_size_limit'});stopped=True;break
        fd=None
        try:
            flags=os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0)
            if not hasattr(os,'O_NOFOLLOW'): raise OSError('nofollow_unavailable')
            fd=os.open(name,flags,dir_fd=dirfd)
            with os.fdopen(fd,'rb') as stream:
                fd=None;actual=os.fstat(stream.fileno())
                if not stat.S_ISREG(actual.st_mode) or actual.st_size>MAX_FILE_BYTES: raise ValueError('file_changed_or_not_regular')
                data=stream.read(MAX_FILE_BYTES+1)
                if len(data)>MAX_FILE_BYTES or len(data)!=actual.st_size: raise ValueError('file_changed_or_over_limit')
                if total+len(data)>MAX_TOTAL_BYTES: raise ValueError('total_size_limit')
            files.append({'path':rel,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()});total+=len(data)
        except Exception as e: errors.append({'path':rel,'reason':type(e).__name__})
        finally:
            if fd is not None: os.close(fd)
    os.close(dirfd)
    pending.extend(reversed(dirs))
if stopped:
    for fd,_ in pending:
        try: os.close(fd)
        except Exception: pass
files.sort(key=lambda x:x['path'])
complete=not errors and not stopped
print(json.dumps({'status':'OK' if complete else 'PARTIAL','root':ROOT,'complete':complete,
 'limits':{'entries':MAX_ENTRIES,'files':MAX_FILES,'file_bytes':MAX_FILE_BYTES,'total_bytes':MAX_TOTAL_BYTES},
 'entries_seen':entries,'files_hashed':len(files),'total_bytes':total,'files':files,'errors':errors},sort_keys=True,separators=(',',':')))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="JSON result path under docs/refactoring")
    parser.add_argument("--full-source", action="store_true",
                        help="also hash bounded Python files under each observed /app/vibetrading")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "docs/refactoring"):
        parser.error("--output must remain under docs/refactoring")
    names: list[str] = []
    try:
        names, sources, compose = _read_allowlists()
        remote = (REMOTE.replace("__NAMES_JSON__", json.dumps(names))
                  .replace("__SOURCES_JSON__", json.dumps(sources))
                  .replace("__COMPOSE_JSON__", json.dumps(compose))
                  .replace("__FULL_SOURCE__", "True" if args.full_source else "False")
                  .replace("__SCAN_CODE_JSON__", json.dumps(SCAN_CODE)))
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
             TARGET, "python3", "-"],
            input=remote, text=True, encoding="utf-8", capture_output=True,
            timeout=FULL_SOURCE_SSH_TIMEOUT if args.full_source else SSH_TIMEOUT, check=False)
        if result.returncode != 0:
            data = {"schema_version": 1, "status": "COLLECTION_FAILED",
                    "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "collection_errors": [{"area": "ssh", "error": "nonzero_exit"}],
                    "containers": {name: {"status": "ERROR", "error": "ssh_failed"} for name in names}}
        else:
            data = json.loads(result.stdout)
        data["collector"] = {"target": TARGET, "identity_input": str(IDENTITY.relative_to(ROOT)),
                             "identity_observed_utc": json.loads(IDENTITY.read_text(encoding="utf-8"))["observed_utc"],
                             "ssh_timeout_seconds": FULL_SOURCE_SSH_TIMEOUT if args.full_source else SSH_TIMEOUT,
                             "per_remote_command_timeout_seconds": REMOTE_COMMAND_TIMEOUT,
                             "full_source_requested": args.full_source,
                             "full_source_timeout_seconds": FULL_SOURCE_TIMEOUT if args.full_source else None,
                             "environment_values_emitted": False,
                             "application_code_executed": False}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "status": data.get("status"),
                          "container_count": len(data.get("containers", {})),
                          "errors": len(data.get("collection_errors", []))}, ensure_ascii=False))
        return 0 if data.get("status") == "READ_ONLY_IDENTITY_ONLY_NOT_CUTOVER_PROOF" else 1
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"VN00 identity collection failed: {type(exc).__name__}", file=sys.stderr)
        failure = {"schema_version": 1, "status": "COLLECTOR_FAILURE",
                   "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "collection_errors": [{"area": "collector", "error": type(exc).__name__}],
                   "containers": {name: {"status": "ERROR", "error": "collection_unavailable"}
                                  for name in names}}
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
