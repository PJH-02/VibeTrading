# 지정 코인 수집 데이터 삭제 기록

사용자 요청에 따라 Binance, Bybit, Bitget의 BTCUSDT, ETHUSDT, SOLUSDT 수집 기록을 삭제했다. 2026-10-02 KST 검증 완료.

- 서버 코인 전용 Parquet 2,435개: 각 파일의 provider, venue, raw symbol, standard symbol 열을 읽어 위 9개 조합만 포함함을 확인한 뒤 삭제. 741,119,183 bytes.
- 서버 코인 전용 SQLite 원장과 WAL/SHM: 원장 provider/venue가 위 세 수집기뿐이고 주문 기록이 없음을 확인해 삭제. 1,808,343,040 bytes. 일반 시장 원장은 보존.
- NATS: 거래소·종목·자산 종류를 완전히 지정한 45개 subject만 purge. 종료 검증에서 CRYPTO_DATA messages=0, bytes=0. MARKET_DATA는 보존.
- 원격 운영 Parquet 69,453개: 원격 파일의 동일한 네 식별 열을 읽어 대상 종목만 포함함을 확인한 뒤 파일 및 대응 manifest 삭제. 운영 generation의 잔여 객체 0개.
- 해당 코인 원장의 원격 상태·복구·기존 백업 객체 933개도 제거. 기존 복구 prefix의 잔여 객체 0개. 버킷 versioning은 활성화되어 있지 않음.
- PC의 실제 코인 원장 분석 사본 r2-baseline/r2-candidate의 SQLite와 WAL/SHM 6개 삭제: 4,433,841,672 bytes.
- SKHYUSDT, SKhynixUSDT 등 다른 종목을 수집하는 일반 시장 데이터 저장소 및 export tar는 삭제 대상으로 지정하지 않음. 서버 export 파일 전체의 크기·mtime·inode가 작업 전과 일치함을 확인.
- 별도 qualification 합성 시험 객체 3,937개는 실제 수집 기록과 구분하여 보존. 객체 목록과 크기가 작업 전과 일치함을 확인.
- 코인 수집기 3개와 코인 저장·보관 서비스는 정지 상태를 유지하여 새 수집 데이터가 생성되지 않도록 함.

검증 중 다른 종목이 포함된 운영 코인 Parquet와 삭제 오류는 발견되지 않았다. 삭제 검증용 임시 컨테이너는 종료 후 제거했다.

원본 증거: [서버 삭제 검증](evidence/crypto-deletion-server-20261002.json), [PC 삭제 검증](evidence/crypto-deletion-pc-20261002.json). 상세 파일별 식별·삭제 로그는 서버 `/srv/vibetrading-data/runtime_data/crypto-delete-audit-20261002/`에 있다.
