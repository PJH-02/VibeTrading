# LS증권 선물 데이터 수집 준비 (코드만, 2026-09-27)

## 범위와 공식 근거

LS의 [공식 TR 명세 JSON](https://openapi.ls-sec.co.kr/api/apis/public/tr-guides.json?download=true) 버전 `20260923083222998`과 [인증·WebSocket 시작 안내](https://openapi.ls-sec.co.kr/howto-use)에 맞춰 read-only 코드를 작성했다.

| 데이터 | LS TR | 코드 처리 |
| --- | --- | --- |
| 코스피200 선물 주간 외인 잠정 매수·매도 | `DBT`, 키 `DFK2I` | 명세가 외국인으로 지정한 `msvolume2`, `mdvolume2`, `msvol2`, `msvalue2`, `mdvalue2`, `msval2`를 원 단위 변환 없이 저장 |
| 코스피200 선물 야간 외인 잠정 매수·매도 | `DBT`, 키 `NFK2I` | 주간과 별도 세션·거래일로 저장. [KRX 야간시장](https://open.krx.co.kr/contents/OPN/01/01041402/OPN01041402.jsp)의 18:00~익일 06:00 일정 사용 |
| CME ES·NQ 시세 | `o3121` 마스터, `o3105` 현재가 | LS 계약 코드 확인 후 가격·체결수량·`KorDate`/`RcvTm`·서버 수신시각·관측 지연 밀리초 저장. 10~60분 범위의 관측 지연인 레코드만 발행 |

`DBT`의 `tjjtime`은 공급자 **수신시간**으로 명시돼 있어 거래소 발생시각으로 바꾸지 않았다. `DBT` 거래대금의 명세에는 화폐 단위 배율이 없어 `value_unit=LS_native_unverified`를 함께 기록한다. 장중 수치는 잠정 관측값이다. **장후 공식 확정 외인값은 기존 원칙대로 KRX 공식 공개 API만 허용**하며 LS 장중값을 확정값으로 승격하지 않는다.

ES/NQ `o3105`의 관측 지연은 `KorDate`·`RcvTm`과 서버 수신시각의 차이다. 시장에 실제 체결이 한동안 없어서 발생한 데이터 연령과 공급자의 지연시세 권한은 이 숫자만으로 구별할 수 없다. LS 공개 문서는 이 TR의 **무료 지연 권한과 추가 시세료 면제**를 보증하지 않는다. 따라서 실제 호출은 계정별 조건 확인 전까지 차단한다. 다른 CME 실시간 WebSocket TR은 사용하지 않는다.

## 활성화 조건

기본 Compose 운영 서비스에는 LS 프로세스를 넣지 않았다. `config/ls_market_runtime.yaml`과 선택형 `deploy/ls-market-profile.yml`만 추가했다. 이 프로세스는 코인 수집기와 분리되고 기존 시장 `MARKET_DATA` 저장 경로를 사용한다. LS 주문 TR은 호출하지 않는다.

키만 넣어도 네트워크 호출은 일어나지 않는다. 다음 대화에서 계정의 이용조건을 확인한 후 서버 `.env`의 `LS_APP_KEY`, `LS_APP_SECRET`을 설정하고, 다음 세 플래그를 **각각 근거가 확인된 경우에만** 켠다.

- `LS_KR_FUTURES_NO_EXTRA_FEE_CONFIRMED=true`
- `LS_OVERSEAS_DELAYED_NO_EXTRA_FEE_CONFIRMED=true`
- `LS_OVERSEAS_DELAYED_ENTITLEMENT_CONFIRMED=true`

`o3121`에서 ES 또는 NQ의 계약월이 여러 개 반환되면 임의의 최근월을 선택하지 않고 중단한다. 마스터 검토 후 `LS_ES_SYMBOL`·`LS_NQ_SYMBOL`로 정확한 계약을 지정한다. 계약이 변경되면 재확인해야 한다.

## 남은 현장 검증

API 키와 무료 시세 권한 확인 후 토큰 발급, `DBT` 주·야간 응답의 필드와 단위, `o3121` 계약 목록, `o3105` 실제 지연·시각대·권한 오류를 각각 확인한다. 그때까지 `market-source-status`는 모두 비활성으로 표시한다. 2026-09-27에는 서버 LS 연결·주문·수집 서비스 기동을 수행하지 않았다.
