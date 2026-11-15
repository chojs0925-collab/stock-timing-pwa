# 국장 매매 타이밍 (PWA)

국장 **일봉 스윙 매매 타이밍** 앱. 네이버 금융 공개 일봉으로 25종목을 분석해 신호등(🟢🟡🔴)과
진입가·목표가·손절가를 보여주고, 증권사 앱에서 바로 주문할 수 있도록 수량·수수료·세금까지 계산한다.
설치형 PWA(홈 화면 추가)로 오프라인에서도 마지막 신호를 볼 수 있다.

- 배포 주소: <https://chojs0925-collab.github.io/stock-timing-pwa/>
- 화면: 3개 탭 — 오늘의 픽 / 내 종목 / 원칙·연동 (412×915 모바일 기준, 360px까지 대응)

## 폴더 구조

```
.
├─ index.html            화면 골격 (3개 탭 + 주문 계산 시트)
├─ styles.css            다크 핀테크 모바일 UI
├─ app.js                렌더링·계산·localStorage·#import=·서비스워커 등록
├─ sw.js                 오프라인 캐시 (앱 셸 캐시 우선, data/ 네트워크 우선)
├─ manifest.webmanifest  PWA 설치 정보
├─ icons/                192·512·maskable·apple-touch 아이콘
├─ data/
│  ├─ signals.json       분석 결과 (앱이 읽는 유일한 실시간 파일)
│  ├─ catalog.json       코스피·코스닥 시총 상위 200종목 (종목코드 입력·검색용)
│  ├─ holdings.json      공개용 보유 종목 자리 (빈 배열)
│  ├─ backtest.json      신호 과거 검증 결과
│  ├─ raw/{code}.json    종목별 일봉 캐시 (오프라인 재계산용)
│  └─ history/           기준일별 추천 스냅샷 + index.json
└─ engine/
   ├─ naver.py           네이버 금융 일봉/종목목록 수집
   ├─ indicators.py      지표·KRX 호가단위·점수·수수료 계산
   ├─ analyze.py         분석 실행 → data/signals.json
   ├─ backtest.py        신호 과거 검증 → data/backtest.json
   ├─ universe.json      분석 대상 25종목
   ├─ make_icons.py      아이콘 생성
   └─ verify_ui.py       Playwright UI 자동 검증
```

## 실행

```bash
python3 engine/analyze.py          # 최신 일봉으로 signals.json 갱신 (약 30초)
python3 engine/analyze.py --offline # 네트워크 없이 data/raw 캐시로 재계산
python3 engine/backtest.py         # 신호 과거 검증 갱신
python3 -m http.server 8765        # 로컬 확인: http://127.0.0.1:8765/index.html
```

## 보유 종목은 어디에도 저장되지 않는다

보유 종목·평균단가는 **브라우저 localStorage에만** 남는다. 서버도 저장소도 거치지 않는다.

- 공개 저장소의 `data/holdings.json`은 빈 배열(`[]`)이다. 실제 보유분은 `data/holdings.local.json`에만
  두며 `.gitignore`로 제외한다. `engine/analyze.py`도 기본값으로는 보유 종목을 `signals.json`에 넣지 않는다
  (`--include-holdings`는 로컬 확인용).
- 기기를 바꾸거나 초기 세팅할 때는 **가져오기 링크**를 쓴다.

  ```
  https://<배포주소>/#import=<base64(UTF-8 JSON)>
  JSON 형식: [{"code":"005930","name":"삼성전자","qty":10,"avgPrice":70000}, ...]
  ```

  URL 해시는 HTTP 요청으로 서버에 전송되지 않으므로, 링크를 열면 앱이 곧바로 localStorage에 저장하고
  `history.replaceState`로 주소창의 해시를 지운 뒤 "보유 종목 N종목 불러오기 완료" 안내를 띄운다.
  이후에는 어디에도 그 값이 남지 않는다.

## 분석 규칙

- **지표**: 20일/60일 이동평균 배열과 20일선 5거래일 기울기, Wilder RSI(14),
  20일 평균 대비 당일 거래량 배수, 최근 20일 고점 대비 눌림폭.
- **점수(0~100)**: 추세 40 + RSI 25 + 거래량 20 + 눌림 15.
  - 70점 이상 🟢 매수, 45~69 🟡 관망, 45 미만 🔴 주의
  - 20·60일선 역배열은 42점 상한, RSI 78 이상은 68점 상한, 고점 대비 -25% 이하는 50점 상한.
- **주문가**: 진입가=현재가, 목표가=+8%(호가 올림), 손절가=-4%(호가 내림).
  KRX 호가단위(2,000원 미만 1원 / 5,000원 미만 5원 / 2만원 미만 10원 / 5만원 미만 50원 /
  20만원 미만 100원 / 50만원 미만 500원 / 이상 1,000원)로 반올림.
- **비용**: 수수료 0.00364% 기본(일반 온라인 0.015% 토글),
  매도세는 코스피·코스닥 모두 0.20%(2026년: 증권거래세 0.05% + 농특세 0.15%).

## 신호 과거 검증 (최근 결과)

- 구간 2026-06-26 ~ 2026-11-11, 매수 신호 145건
- 목표(+8%) 먼저 도달 28.3%, 손절(-4%) 먼저 도달 68.3%, 평균 수익률 -0.67%
- 즉 그 구간에서 '무조건 신호대로 사는' 규칙은 마이너스였다. 앱은 이 수치를 그대로 보여준다.
  → 신호는 후보를 좁히는 용도로만 쓰고, 매수는 시장 국면·분할·손절선 관리와 함께 판단할 것.

## 검증

```bash
python3 engine/verify_ui.py       # 로컬 서버가 떠 있어야 함
```

412×915 모바일 뷰포트에서 렌더링, 탭 전환, 주문 계산(예산·수수료 토글), `#import=` 가져오기와 해시 제거,
내 종목 추가·수정·삭제·새로고침 유지, 카탈로그 종목(분석 전) 추가, 오프라인 캐시, 360px 폭,
콘솔/런타임 에러 0건을 확인한다. (마지막 실행 39/39 통과)

검증 스크립트는 Playwright + 로컬 headless Chrome을 쓴다. Chrome 실행 파일 경로는 `CHROME_BIN` 환경변수로 지정한다.

```bash
curl -L -o chs.zip https://cdn.playwright.dev/builds/cft/159.0.8154.6/linux64/chrome-headless-shell-linux64.zip
unzip chs.zip -d ./tmp/chs && chmod +x ./tmp/chs/chrome-headless-shell-linux64/chrome-headless-shell
pip install playwright
```

## 유의

- 신호는 **일봉 종가 기준**이며 장중 실시간 가격이 아니다. 투자 자문이 아니다.
- 실제 주문은 증권사 앱·HTS에서 직접 한다. 자동 주문 연동은 `app.js`의 `brokerAdapter` 규격을 구현하면 된다.
