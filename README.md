# VibeTrading

공유 시장 데이터와 전략 파일, 계좌별 실행 권한을 분리하는 Python 트레이딩 시스템입니다.
현재 작업의 범위와 수락 여부는 아래 최종 문서에서 확인합니다.

## 개발 권위와 진행 상태

- [최종 계획](docs/final/01_FINAL_PLAN.md): 범위, 우선순위, 릴리스 게이트
- [개발 티켓](docs/final/02_DEVELOPER_TICKETS.md): 구현과 수락 기준
- [아키텍처 요구사항](docs/final/03_ARCHITECTURE_REQUIREMENTS.md): 안전 계약과 지원 범위
- [불변 완료 이력](docs/final/04_COMPLETED_MANIFEST.json): 과거 수락 사실
- [현재 상태와 DAG](docs/final/05_TASK_STATUS.json): 유일한 변경 가능한 작업 상태

[상태 안내](docs/status.md)는 이 문서들로 연결합니다. 과거 계획과 작업자의 실행 지시는
[보존 인덱스](docs/archive/README.md)에 역사 자료로 분리되어 있습니다.

## 소스와 로컬 확인

Python 3.12 이상과 기존 requirements.txt 의존성을 사용하는 환경이 필요합니다.
정식 소스는 `src/` 아래의 `core`, `feeds`, `market`, `storage`, `strategies`, `execution`,
`portfolio`, `runtime`, `operations`, `timefolio`의 열 개 도메인에 있습니다.
`src/vibetrading` 호환 패키지는 배포하지 않습니다. 과거 연구 모듈은 pytest에서만
명시적으로 접근하는 [검증용 원본](docs/archive/MOV-05-legacy-oracles/README.md)으로 보존합니다.

```bash
PYTHONPATH=src python3 -m runtime.cli --help
python3 scripts/dev/status.py --root . --json
```

`status.py`는 최종 문서가 존재하면 05의 상태를 투영합니다. 전략 경제 규칙은 전략 파일이,
실행 사실과 계좌 안전은 실행 영역이 소유합니다. C3/KP/KD와 KIS Mock/Mock2는 기존
계산·운용 경로를 보존하며, TOSS 데이터 수집은 유지하고 주문은 DATA_ONLY로 차단합니다.
실제 파일 전략·공통 Owner 연결의 완성 여부는 각 후속 티켓과 지원 범위를 확인합니다.

## 로컬 검증과 운영 경계

명시적인 NATS-free primary는 [deploy/primary-compose.yml](deploy/primary-compose.yml)입니다.
역할별 프로파일과 검증된 이미지·로컬 설정을 선택해야 하며, 문서에 명령이 있다는 사실이
계좌 활성화나 배포 승인을 의미하지 않습니다. Timefolio 주문·모니터는 별도 경로이며
기본 제출 차단과 계좌별 승인을 유지합니다.

과거 NATS 배치의 [고정 롤백 목록](deploy/rollback/MOV-06/manifest.json), 수락 기록,
Golden, 원본 검증 자료를 보존합니다. 로컬 fake 테스트·이미지 기동은 실제 거래소 주문,
현재 서버 상태나 운영 준비 완료의 증거와 구분합니다.

## 증거 저장

새 원시 로그와 JUnit·AI 응답은 `.local/artifacts/` 또는 CI/private run artifacts에 두고,
Git에는 소스와 검토 가능한 compact 수락 기록·보존 manifest를 남깁니다. 기존 자료의
분류·해시·복구 경로·보존 예외는 [CLN-01 보존 목록](docs/archive/CLN-01-retention.json)을
따릅니다. 운영 데이터는 문서 정리 대상이 아닙니다.
