# 운영 전환 패키지

상태: 로컬 구현·검증 진행 중. 운영 미배포. `release_manifest.json`은 역할별 현재 image ID, 실제 Compose, mount, source 차이와 차단 사유를 고정한다. candidate digest가 비어 있거나 gate가 미통과인 상태에서는 실행 가능한 전환 명세가 아니다.

운영 기준 Compose는 `/opt/vibetrading/releases/telegram-recovery-20261014/runtime.yml` 및 관측된 dashboard Compose다. 오래된 로컬 default를 운영 전체에 적용하지 않는다. collector·crypto·ETF·Telegram image의 코드가 서로 달라 실제 소유 역할의 preimage와 candidate diff를 각각 대조한다.

1. 72시간의 장중·장외 입력량, 디스크 증가, CPU/IO·swap·메모리 및 저장 지연을 측정하고 G00/G10을 판정한다. 가짜 ACK 로컬 benchmark를 이 측정으로 대체하지 않는다.
2. 실제 mountinfo/device 또는 검증한 volume marker를 바탕으로 역할별 mount contract를 작성한다. `VIBETRADING_ENFORCE_MOUNTS=true`와 `VIBETRADING_STORAGE_MOUNTS_FILE`을 함께 지정한다. data_root/state_dir/parquet_root 각각의 mount binding이 필요하다. marker를 구현 과정에서 운영 볼륨에 임의 생성하지 않았다.
3. 역할별 preimage 차이를 해소하고 immutable candidate image digest와 검증된 rollback image를 고정한다. 전체 stack의 동시 메모리 예산과 Linux 파일·SQLite 내구성 검증을 통과한다.
4. 신규 위험을 halt하고 미체결·UNKNOWN·예약·실제 계좌를 대조한다. 일관 백업의 복원을 별도 경로에서 검증한다. 과거 장부로 현재 체결 사실을 덮지 않는다.
5. legacy 주문 프로세스와 자동 재시작을 멈추고, 같은 계좌·환경의 실제 owner가 하나임을 확인한다. 파일 lease는 비협조 프로세스나 다른 호스트를 차단하지 못한다.
6. candidate를 blocked/shadow로 시작하고 원장·계좌·feed snapshot/window·epoch·lease를 다시 검증한다. 실제 mutation permit seam이 없는 adapter는 차단 상태를 유지한다.
7. 승인된 역할·feed·계좌 범위만 전환하고, raw/native/전체 book·경제적 결정·저장 지연·자원 사용을 관측한다. 비활성 전략과 retired provider는 활성화하지 않는다.

이 순서의 검증이 끝나기 전 운영 재시작·실계좌 제출·Telegram 메시지 전송을 수행하지 않는다. 새 Core live/storage 분리와 공유 전략 엔진은 명시적 route flag 및 shadow 검증으로 전환한다. NATS 자체 장애 독립 30분은 구현·입증된 계약이 아니며 ADR-002의 별도 결정이 남아 있다.
