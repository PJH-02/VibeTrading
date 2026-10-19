"""Record scoped evidence; never convert local tests into deployment approval."""
from __future__ import annotations
import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'docs/refactoring/runs/20261018-stage-assignment'


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat()
    queue=json.loads((RUN/'tasks.json').read_text(encoding='utf-8'))
    phases={'P02':'IMPLEMENTED_SCOPED_OFFLINE','P03':'IMPLEMENTED_REPLAY_SHADOW',
        'P04':'IMPLEMENTED_OFFLINE_LIVE_GATES_OPEN','P05':'IMPLEMENTED_OFFLINE_NATS_FREE',
        'P06':'IMPLEMENTED_SCOPED_RECOVERY','P07':'IMPLEMENTED_REVIEW_CORRECTIONS',
        'P08':'IMPLEMENTED_REVIEW_CORRECTIONS','P09':'SERVER_RELEASE_PREPARATION',
        'P10':'OFFLINE_PASS_PRODUCTION_OPEN','P11':'REVIEW_PACKAGE_NOT_DEPLOYED','P12':'NOT_STARTED'}
    for task in queue['tasks']:
        if task['task_id'].endswith('-PHASE') and task['phase'] in phases:task['status']=phases[task['phase']]
        if task['task_id']=='P00-LONG-MEASURE':
            task['status']='COVERAGE_GAP_NOT_72H_COMPLETE'
            task['evidence']=['measurement-72h-v1/coverage_gap.json','measurement-72h-v2/coverage_gap.json']
        if task['task_id'] in {'P02-SCHEMA-CACHE','P02-ENCODE-PACKET'}:
            task['status']='PASS_SCOPED_OFFLINE'
            task['dispatch_state']='IMPLEMENTED_REVIEWED_OFFLINE'
    extra=[
        ('P05-LOCAL-RUNTIME','P05','LOCAL_IMPLEMENTED_SCOPED_TESTS','root',
         ['market_spool.py','local_transport.py','local_pipeline.py','local_engine.py','local_observation.py']),
        ('P07-LOCAL-STORAGE','P07','REVIEW_CORRECTIONS_IMPLEMENTED','control_review_2',
         ['local_storage.py','tests/refactoring/test_local_storage.py']),
        ('P08-LOCAL-FACTS','P08','REVIEW_CORRECTIONS_IMPLEMENTED','storage_fanout_review_2',
         ['local_events.py','telegram.py','tests/refactoring/test_local_events.py']),
        ('P09-SERVER-OWNER-BASE','P09','SERVER_OWNER_REBASE_VERIFIED','root + storage_fanout_review_2',
         ['server_source_inventory.json','build_server_base.py','build_server_candidate.py']),
        ('KR-SIGNAL-CONTINUITY','P10','FAIL_SCOPED_AVAILABILITY','control_review_2',
         ['docs/evidence/refactoring_20261019/korean_strategy_continuity_audit.md',
          'docs/evidence/refactoring_20261019/korean_strategy_gap_detail.json']),
        ('KR-HISTORICAL-GAP-REPAIR','P07','DEFERRED_BY_USER_20261019','execution_review_2',
         ['tmp/refactoring/server-hotfix-20261019/etf-history-v1']),
    ]
    index={task['task_id']:task for task in queue['tasks']}
    for name,phase,status,owner,paths in extra:
        item={'task_id':name,'phase':phase,'status':status,'owner_role':owner,
              'output_paths_or_contracts':paths,'production_status':'NOT_DEPLOYED'}
        if name in index:index[name].update(item)
        else:queue['tasks'].append(item)
    queue['updated_kst']=now
    queue['latest_user_direction']='Pause missing-minute work; resume original refactoring plan, reconcile completed work to server before next phase. Simple reviews use Luna; read diffs only; KISS/DRY/YAGNI.'
    queue['current_work']='P09 role-specific server release, P10 operational evidence, P11 incremental rollout; P02 schema-only first active unit'
    write(RUN/'tasks.json',queue)
    gates=json.loads((RUN/'gates.json').read_text(encoding='utf-8'))
    gates['updated_kst']=now
    g=gates['gates']
    g['G-NATS']={'status':'SUPERSEDED_BY_NATS_FREE_TARGET','decision':'ADR-002 final runtime requires no NATS; new unused bridge removed'}
    proofs={}
    for label,filename in [('integrated-windows-v5','result.json'),('integrated-linux-v5','linux_result.json')]:
        path=RUN/label/filename
        if not path.exists():continue
        result=json.loads(path.read_text(encoding='utf-8'))
        if result['exit_code']!=0:raise ValueError('cannot record failing suite as PASS')
        drift=[]
        for name,digest in result['tested_source_hashes'].items():
            source=ROOT/name
            if name.startswith('vibetrading/') and source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
                drift.append(name)
        proofs[label]={'status':'PASS_SCOPED_FROZEN_SOURCE','evidence':str(path.relative_to(ROOT)),
            'elapsed_seconds':result['elapsed_seconds'],'current_product_drift':drift}
    g['G10-OFFLINE-V5']={'status':'PASS_SCOPED_FROZEN_SOURCE','proofs':proofs,
        'windows':'1078 passed,9 skipped,2 deselected','linux':'1087 passed,2 deselected',
        'limitations':['Synthetic traffic/fake broker; no production account, peak/RSS or wall-clock 30-minute outage proof',
            'Later source changes and server-owner candidates require fresh tests']}
    g['G-NATS-FREE']={'status':'IMPLEMENTED_OFFLINE_PRODUCTION_OPEN',
        'decision':'Final local spool -> Linux UDS engine / direct Parquet worker; local facts and dashboard snapshot',
        'evidence':['local-runtime-linux-v4/linux_result.json','integrated-linux-v5/linux_result.json'],
        'unverified':['Production ingress capacity and representative 72-hour coverage',
            'Continuous spool/fact archival and safe GC proof','Role-specific immutable image and mount/resource budget',
            'Production cutover, old NATS backlog and rollback preservation']}
    g['G-KR-SIGNAL-CONTINUITY']={'status':'FAIL_SCOPED_AVAILABILITY','session':'2026-10-19 XKRX',
        'confirmed':['C3 decision at09:01:05; conditions did not authorize entry',
            'KP/KD each10 no_entry decisions,13:00 and13:30 absent',
            'Both base ETF historical2026-10-15 12:54..13:08 missing; affected prior windows13/14',
            'Current-session affected windows30/30;381 rows can be380 continuous+auction'],
        'unverified':['The exact runtime silent-return branch was not instrumented at the time'],
        'repair':'DEFERRED_BY_USER_20261019; no hotfix deployed, no data repair, no retroactive trades'}
    g['G-P02-SERVER-RELEASE']={'status':'PREPARED_NOT_APPLIED','scope':'One schema-cache file in storage/crypto-storage only; v5 refactor inactive candidate',
        'evidence':['p02-schema-v1/result.json','p02-server-preflight.json'],
        'review':'Luna: no schema diff findings; rollback verification finding fixed before staging'}
    g['G00']['long_measurement']='Two interrupted local measurement processes; coverage gaps preserved; not72hcomplete'
    g['G11']={'status':'BLOCKED_OPERATIONAL_EVIDENCE','source_basis':'Active module owner inventory2026-10-19',
        'prohibitions':['No wholesale local Compose rollout','No old DB restore over fills','No unverified raw/source deletion']}
    applied=RUN/'p02-server-resume-receipt.json'
    if applied.is_file():
        receipt=json.loads(applied.read_text())
        if receipt['status']!='P02_SCHEMA_ACTIVE_TWO_STORAGE_SERVICES':raise ValueError('unexpected deployment result')
        g['G-P02-SERVER-RELEASE']={'status':'APPLIED_SCOPED_TWO_STORAGE_SERVICES',
            'image_id':receipt['image_id'],'release':receipt['release'],
            'evidence':['p02-server-resume-receipt.json','p02-server-health.jsonl'],
            'scope':'Schema cache only; packet/shared engine/storage/control changes are inactive candidates',
            'operational_finding':'Existing crypto event-index startup revalidation took minutes; initial 30s readiness timeout exceeded, same container resumed without repeated restart'}
        queue['current_work']='P02 schema cache active; P03-P08 offline implemented; P08 WAL cap verified; P09 next candidate rebased onto actual post-P02 server; P10 production evidence and P11 full cutover remain open'
        for task in queue['tasks']:
            if task['task_id']=='P02-SCHEMA-CACHE':task.update(status='APPLIED_SCOPED_TWO_STORAGE_SERVICES',production_status='ACTIVE_SCHEMA_CACHE_ONLY')
            if task['task_id']=='P09-SERVER-OWNER-BASE':task.update(status='POST_P02_SERVER_REBASE_VERIFIED',output_paths_or_contracts=['server-active-owner-after-p02.json','server-active-owner-candidate-v6.json'])
    wal_proofs={}
    for label,filename in [('local-facts-wal-windows-v6','result.json'),('local-facts-wal-linux-v6','linux_result.json')]:
        path=RUN/label/filename
        if path.is_file():
            evidence=json.loads(path.read_text(encoding='utf-8'))
            if evidence['exit_code']!=0:raise ValueError('WAL change tests did not pass')
            wal_proofs[label]={'exit_code':0,'evidence':str(path.relative_to(ROOT))}
    if len(wal_proofs)==2:
        g['G08-FACTS-WAL-V6']={'status':'PASS_SCOPED_OFFLINE_NOT_DEPLOYED','proofs':wal_proofs,
            'windows':'46 passed,1 skipped','linux':'47 passed',
            'review':'Luna minimal diff, no findings','limits':'No receipt deletion/GC; main DB cap plus separately derived WAL/SHM reserve; no live control cutover'}
    queue['updated_kst']=now
    write(RUN/'tasks.json',queue)
    write(RUN/'gates.json',gates)
    handoff='''# 현재 실행 인계 — 2026-10-19

최신 사용자 지시: 누락 분봉 작업을 보류하고 원 계획대로 리팩토링한다. 완료분을 서버에 반영한 뒤 다음 작업을 진행한다. 단순 리뷰는 Luna, 재독 대신 diff·기존 receipt, KISS/DRY/YAGNI를 적용한다.

현재 위치는 **P09 역할별 서버 전환 후보 구성**이다. P02 schema cache는 storage/crypto-storage에 실제 적용됐다. 두 서비스 healthy·실행 파일 SHA·저장/ACK 증가·다른 컨테이너 불변을 확인했다. 릴리스 경로는 `/opt/vibetrading/releases/refactoring-20261019-p02-schema`다. 기존 약 3,050만 crypto 이벤트 인덱스 재검증으로 최초 readiness timeout이 발생했지만 같은 컨테이너의 초기화 진행을 확인해 재시작 반복 없이 완료했다. DB 복원/마이그레이션과 주문/수집기 재시작은 없었다. 관련 receipt는 `p02-server-resume-receipt.json`, `p02-server-health.jsonl`이다.

P03~P08은 격리 구현·검증 상태이며 운영 전환은 아직 아니다. 통합 v5 전체 Windows1078 PASS/9 SKIP/2 DESELECT, Linux1087 PASS/2 DESELECT. 이후 P08 체크포인트 WAL pin/재시작 상한을 보완했고 v6 관련 Windows46 PASS/1 SKIP, Linux47 PASS와 Luna 리뷰를 통과했다. 최근 두 제품 파일만 변경됐으며 전체 v6 suite를 돌린 것으로 보고하지 않는다.

`server-active-owner-after-p02.json`은 실제 모듈 소유자 기준선에 적용된 한 파일을 반영한다. `server-active-owner-candidate-v6.json`은 그 기준에 리팩토링을 합성한 후보이며 충돌0, v6의 vibetrading 제품130파일과 바이트 동일하다. 후보는 비활성이며 full NATS-free runtime을 활성화하지 않았다.

다음 작업은 P09의 역할별 mount/자원/용량 계약과 이미지 구성, P10 실제 지속 실행·복구/부하 증거, P11 점진 전환이다. 72시간 측정은 두 번 중단돼 coverage gap이 있다. 가상1801초를 실제30분 운영으로 대체하지 않는다. 생산 peak·대표 부하·archive/GC·구형 NATS backlog 보존·전체 cutover는 아직 열려 있다. 기동 인덱스 재검증과 backlog 영향도 운영 검증에 포함한다.

누락 분봉 hotfix는 `DEFERRED_BY_USER`; 원본 native/remote 조사 결과와 후보만 보존했고 서버에 적용하지 않았다. 원본 `.env`가 있는 작업공간에서 제품 import/pytest를 금지한다. credential-free copy+clean environment/guard만 사용한다. 사용자 파일 reset/clean/stash, 실주문, DB 덮어쓰기, 원본 삭제를 하지 않는다. 최초 배정 인계는 `handoff-initial-20261018.md`에 보존했다.
'''
    (RUN/'handoff.md').write_text(handoff,encoding='utf-8')
    print(json.dumps({'updated_kst':now,'tasks':len(queue['tasks']),'offline_proofs':list(proofs)}))


if __name__=='__main__':main()
