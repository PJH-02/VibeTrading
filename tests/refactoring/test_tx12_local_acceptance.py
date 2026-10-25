"""Codex local acceptance: independently pinned evidence, never a live badge."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from scripts.refactoring.build_release_manifest import build_local
from test_vn19_local_release import tmp_git_repo_src

ROOT=Path(__file__).resolve().parents[2]
PREFIX=Path('docs/refactoring/runs/20261025-v3/core5')


@pytest.mark.parametrize('ticket',['VN17','VN18','VN19','TX11'])
def test_predecessors_have_actual_independent_execution_and_pinned_sources(ticket):
    receipt=json.loads((ROOT/PREFIX/ticket.lower()/'acceptance.json').read_text())
    assert receipt['task_id']==ticket and receipt['decision'] in {'PASS_LOCAL_SCOPED','PASS'}
    assert receipt['independent_exit']==0 and receipt['scoped_test_passed']>0
    assert len(receipt['source_sha'])==40 and not receipt['live_readiness']
    # Failed or undispatched inference is never promoted to a successful worker.
    assert receipt['worker_acceptance'] in {'FAILED_PARTIAL_NOT_ACCEPTED','NOT_DISPATCHED_AFTER_PROVIDER_LIMITS'}
    if receipt['worker_acceptance']=='FAILED_PARTIAL_NOT_ACCEPTED':
        assert receipt['worker_exit']!=0
    else:assert receipt['worker_exit'] is None
    assert receipt['product_and_test_sha256']
    for relative,expected in receipt['product_and_test_sha256'].items():
        path=ROOT/relative
        assert path.is_file() and not path.is_symlink()
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected


def test_local_review_package_cannot_accept_dirty_copy_or_stale_commit(tmp_git_repo_src):
    root,head=tmp_git_repo_src;relative='src/vibetrading/module_src.py';path=root/relative
    before=path.read_bytes()
    result=build_local(root,expected_head=head,paths=[relative])
    assert result['files']=={relative:hashlib.sha256(before).hexdigest()}
    assert result['state']=='LOCAL_REVIEW_PACKAGE_ONLY'
    assert not any(result[k] for k in ('server_deployed','live_active','build_image_verified'))
    assert set(result['external_gates'].values())=={'NOT_RUN'}
    path.write_bytes(before+b'\n# copied source from a different revision\n')
    with pytest.raises(ValueError,match='drift'):build_local(root,expected_head=head,paths=[relative])
    path.write_bytes(before)
    with pytest.raises(ValueError,match='HEAD mismatch'):
        build_local(root,expected_head=('1' if head[0]!='1' else '2')+head[1:],paths=[relative])
    assert path.read_bytes()==before


def test_runbook_matrix_resolves_existing_roles_and_external_gates():
    root=ROOT/'docs/refactoring/vnext/reuse_audit'
    document=(root/'local_acceptance_tx12.md').read_text()
    for name in ('local_paper_assembly.md','local_cutover_vn19.md','local_rollback_vn19.md'):
        assert name in document and (root/name).is_file()
    for relative in ('src/vibetrading/local_runtime.py','src/vibetrading/runtime_checkpoint.py',
            'src/vibetrading/strategy_runtime.py','src/vibetrading/local_storage.py',
            'src/vibetrading/dashboard/projection.py','src/vibetrading/telegram.py'):
        assert relative in document and (ROOT/relative).is_file()
    for gate in ('server_deployed=false','live_active=false','VN20_APPLIED=false',
                 'NOT_RUN','exact-cut','Codex','worker'):
        assert gate in document
