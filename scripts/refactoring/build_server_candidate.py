"""Build a credential-free candidate FROM an actual container source export.

Keep server-only changes. Apply local refactoring deltas using the frozen
pre-refactor baseline; retain the server file and expose any merge conflicts.
This never uploads, builds an image, modifies a server, or edits the workspace.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'tmp/refactoring/20261018-stage-assignment/baseline'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def product(relative):
    return relative.as_posix()=='main.py' or relative.parts[0] in {'vibetrading','strategies'}


def copy_file(source,target):
    if source.is_symlink():raise ValueError('source symlink forbidden')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)


def build(server,local,output,delta_paths=None):
    for path in (server,local):
        if not path.is_relative_to(ROOT/'tmp/refactoring') or not path.is_dir() or (path/'.env').exists():
            raise ValueError('use a credential-free source export under tmp/refactoring')
    if not output.is_relative_to(ROOT/'tmp/refactoring') or output.exists():
        raise ValueError('output must be a new candidate under tmp/refactoring')
    output.mkdir(parents=True)
    for path in local.rglob('*'):
        if not path.is_file():continue
        relative=path.relative_to(local)
        if (product(relative) or '__pycache__' in relative.parts or path.name=='sitecustomize.py'
                or path.name.startswith('.env')):continue
        copy_file(path,output/relative)
    server_paths={}
    for path in server.rglob('*.py'):
        relative=path.relative_to(server)
        if not product(relative) or '__pycache__' in relative.parts:continue
        copy_file(path,output/relative)
        server_paths[relative.as_posix()]=digest(path)
    if not server_paths:raise ValueError('server product export is empty')
    changes={}
    for path in local.rglob('*.py'):
        relative=path.relative_to(local)
        if not product(relative) or '__pycache__' in relative.parts:continue
        if delta_paths is not None and relative.as_posix() not in delta_paths:continue
        before=BASE/relative
        observed=server/relative
        target=output/relative
        base_sha,server_sha,local_sha=digest(before),digest(observed),digest(path)
        if base_sha==local_sha:continue # The server version wins unchanged.
        entry={'baseline_sha256':base_sha,'server_preimage_sha256':server_sha,'local_delta_sha256':local_sha}
        if server_sha==local_sha:entry['state']='ALREADY_PRESENT_ON_SERVER'
        elif base_sha is None and server_sha is None:
            copy_file(path,target);entry['state']='NEW_REFACTOR_MODULE'
        elif server_sha==base_sha and base_sha is not None:
            copy_file(path,target);entry['state']='EXACT_SERVER_PREIMAGE_PATCH'
        elif server_sha is None:
            entry['state']='BLOCKED_SERVER_MODULE_MISSING'
        elif base_sha is None:
            entry['state']='BLOCKED_NEW_MODULE_NAME_COLLISION'
        else:
            result=subprocess.run(['git','merge-file','-p',str(observed),str(before),str(path)],
                capture_output=True,timeout=20)
            if result.returncode==0:
                ast.parse(result.stdout,filename=str(relative))
                target.write_bytes(result.stdout)
                entry['state']='SERVER_AND_REFACTOR_THREE_WAY_MERGE_REVIEW_REQUIRED'
            else:
                conflict=output/'_merge_conflicts'/relative.with_suffix('.merge.txt')
                conflict.parent.mkdir(parents=True,exist_ok=True)
                conflict.write_bytes(result.stdout)
                entry['state']='BLOCKED_CONFLICT_SERVER_SOURCE_RETAINED'
                entry['conflict_artifact']=conflict.relative_to(output).as_posix()
        entry['candidate_sha256']=digest(target)
        changes[relative.as_posix()]=entry
    for path in output.rglob('*.py'):
        if '__pycache__' not in path.parts:ast.parse(path.read_bytes(),filename=str(path))
    blocked=[name for name,entry in changes.items() if entry['state'].startswith('BLOCKED')]
    return {'schema_version':1,'basis':'ACTUAL_RUNNING_CONTAINER_SOURCE','server_source_root':str(server),
        'local_refactoring_source':str(local),'candidate':str(output),'server_preimages':server_paths,
        'changes':changes,'selected_delta_paths':sorted(delta_paths) if delta_paths is not None else None,
        'blocked_files':blocked,'production_status':'NOT_UPLOADED_NOT_BUILT_NOT_DEPLOYED',
        'server_only_changes':'PRESERVED; unchanged local modules never replace server variants',
        'status':'MERGE_BLOCKED' if blocked else 'CANDIDATE_REQUIRES_ROLE_SPECIFIC_TESTS_AND_REVIEW'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--server-source',type=Path,required=True)
    parser.add_argument('--local-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--delta-path',action='append',default=None,
                        help='Apply only these explicit product paths; other server owners win')
    args=parser.parse_args()
    report=args.report.resolve()
    if not report.is_relative_to(ROOT/'docs/refactoring'):raise ValueError('report must stay under docs/refactoring')
    selected=set(args.delta_path) if args.delta_path is not None else None
    if selected is not None:
        for name in selected:
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or not product(relative) or relative.suffix!='.py':
                raise ValueError('unsafe or non-product delta path')
            if not (args.local_source/relative).is_file():raise ValueError('selected delta is absent')
    result=build(args.server_source.resolve(),args.local_source.resolve(),args.output.resolve(),selected)
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'candidate':str(args.output),'files':len(result['server_preimages']),
        'changes':len(result['changes']),'blocked':len(result['blocked_files']),'status':result['status']}))


if __name__=='__main__':main()
