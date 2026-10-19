# 통합 보고서 재현과 검수

이 디렉터리의 `REPORT_KO.md`와 `output/pdf/crypto_strategy_complete_report_20261012.pdf`는 같은 보고서다. `catalogue.json`, `integrity.json`, `validation.json`, `pdf_checks.json`은 등록 목록과 검수 근거다. 기존 시장 데이터와 매매 원장을 읽기만 하며 주문·서비스 실행 명령은 포함하지 않는다.

저장된 근거를 다시 취합하려면 기본 Python의 pandas와 보고서 전용 Korean spacing dependency가 필요하다. 전용 패키지는 프로젝트의 실행 의존성을 변경하지 않고 임시 경로에 설치했다.

```powershell
python -m pip install --target tmp/pdfs/report_spacing_runtime kiwipiepy==0.24.0
python -X utf8 scripts/build_all_strategy_report.py
```

원안 회수에는 이 채팅의 보존된 session 기록을 사용했다. 회수된 문서는 `source_recovery`에도 보존되어 있다. 문서 원본의 byte-identical 해시라는 주장은 하지 않는다.

PDF 작성과 검수에는 Codex bundled Python의 reportlab, pypdf, PIL, Windows Malgun 폰트와 Poppler를 사용했다. 정확한 runtime 경로는 현재 호스트의 `load_workspace_dependencies` 결과를 사용한다.

```powershell
# 아래 python은 reportlab/pypdf/PIL이 있는 bundled Python으로 실행한다.
python scripts/render_strategy_report_pdf.py
pdftoppm -r 80 -png output/pdf/crypto_strategy_complete_report_20261012.pdf tmp/pdfs/all-strategies-20261012/page
python scripts/validate_all_strategy_report.py
```

검수는 89개 ID 및 카드 필드, 12계열의 coverage, 44개 출처 해시, 310개 본문 링크, 테스트넷 8경로·최초 공개 pilot 16경로·최종 상태 재생 69케이스와 957 fill events를 대조한다. 전체 PDF 페이지를 렌더링해 레이아웃을 확인한 뒤 `pdf_checks.json`의 시각 검수 상태를 확정했다. 보고서 작성 과정의 새로운 거래 및 새로운 백테스트는 없다.
