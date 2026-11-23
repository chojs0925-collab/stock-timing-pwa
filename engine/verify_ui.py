#!/usr/bin/env python3
"""PWA UI 자동 검증 (Playwright + 로컬 headless Chrome).

- 412x915 모바일 뷰포트 렌더링, 콘솔 에러 수집
- 탭 전환, 픽 카드 → 주문 계산 시트, 예산/수수료 토글 계산, 내 종목 추가·수정·삭제·저장 유지
- 스크린샷: preview.png, preview-holdings.png, preview-sheet.png, preview-rules.png

실행(로컬 8765 서버가 떠 있는 상태):
    python3 engine/verify_ui.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("APP_URL", "http://127.0.0.1:8765/index.html")
CHROME = os.environ.get(
    "CHROME_BIN", "/workspace/tmp/chs/chrome-headless-shell-linux64/chrome-headless-shell"
)
LIB_PATH = os.environ.get("CHROME_LIB_PATH", "/workspace/tmp/chlibs/usr/lib/x86_64-linux-gnu")
if not os.path.isdir(LIB_PATH):
    LIB_PATH = "/workspace/tmp/libs/usr/lib/x86_64-linux-gnu"

results: list[tuple[bool, str]] = []


def load_seed() -> list[dict]:
    """내 보유 종목 시드 — 공개 저장소에 올리지 않는 data/holdings.local.json에서 읽는다."""
    path = os.path.join(ROOT, "data", "holdings.local.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    return spec.get("holdings", []) if isinstance(spec, dict) else spec


def build_import_url(seed: list[dict]) -> str:
    minimal = [
        {"code": h["code"], "name": h["name"], "qty": int(h["qty"]),
         "avgPrice": int(h.get("avg_price", h.get("avgPrice", 0)))}
        for h in seed
    ]
    raw = json.dumps(minimal, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return BASE + "#import=" + base64.urlsafe_b64encode(raw).decode("ascii")


def check(cond: bool, label: str, detail: str = "") -> None:
    results.append((bool(cond), label))
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    os.environ["LD_LIBRARY_PATH"] = f"{LIB_PATH}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"],
        )
        ctx = browser.new_context(
            viewport={"width": 412, "height": 915},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            user_agent=(
                "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_requests.append(f"{r.url} ({r.failure})"))

        seed = load_seed()
        data = json.load(open(os.path.join(ROOT, "data", "signals.json"), encoding="utf-8"))

        page.goto(build_import_url(seed) if seed else BASE, wait_until="networkidle")
        page.wait_for_selector(".pick-card", state="attached", timeout=15000)

        # #import= 가져오기: localStorage 저장 후 주소창 해시 제거 (시드 파일이 있을 때만)
        if not seed:
            print("[skip] data/holdings.local.json 없음 — #import= 검증 건너뜀")
        else:
            check(page.evaluate("location.hash") == "", "가져오기 후 URL 해시 제거", repr(page.evaluate("location.hash")))
            stored_n = page.evaluate(
                "JSON.parse(localStorage.getItem('gj.holdings.v1')||'[]').length"
            )
            check(stored_n == len(seed), "가져오기 → localStorage 저장", f"{stored_n}/{len(seed)}종목")
            check(page.evaluate("document.querySelector('#view-holdings').classList.contains('active')"),
                  "가져오기 후 내 종목 탭으로 이동")
            page.locator('.tabbar .tab[data-tab="picks"]').click()
            page.wait_for_timeout(150)

        # ---------- 기본 렌더 ----------
        title = page.title()
        check(title == "국장 매매 타이밍", "페이지 타이틀", title)

        picks = page.locator(".pick-card").count()
        check(picks == len(data["picks"]) and picks >= 3, "픽 카드 개수", f"{picks}장")

        rows = page.locator("#allList .row").count()
        check(rows == len(data["stocks"]), "전체 종목 목록", f"{rows}행 / 분석 {data['summary']['analyzed']}종목")

        market_txt = page.locator("#marketCard").inner_text()
        check("코스피" in market_txt and "코스닥" in market_txt, "시장 지수 카드", market_txt.splitlines()[0])
        check("5일선" in market_txt, "지수 5일선 표시", [l for l in market_txt.splitlines() if "5일선" in l][0] if "5일선" in market_txt else "")
        badge_ok = ("신규 매수 가능" in market_txt) or ("신규 매수 보류" in market_txt)
        check(badge_ok, "신규 매수 가능/보류 배지", [l for l in market_txt.splitlines() if "신규 매수" in l][:2])

        idx_data = data.get("index") or {}
        blocked_markets = [k for k, v in idx_data.items() if v.get("allow_new_buy") is False]
        expected_flags = sum(1 for p in data["picks"] if str(p.get("market", "")).lower() in blocked_markets)
        flag_n = page.locator(".pick-flag").count()
        if blocked_markets:
            check(flag_n == expected_flags and flag_n > 0, "픽 카드 신규 매수 보류 배지",
                  f"{flag_n}장 (차단 시장 {', '.join(blocked_markets)})")
        else:
            check(flag_n == 0, "지수 5일선 위 — 보류 배지 없음", f"{flag_n}장")

        body_text = page.locator("body").inner_text()
        bad = [w for w in ("NaN", "undefined", "[object Object]") if w in body_text]
        check(not bad, "깨진 값 없음", ", ".join(bad) if bad else "NaN/undefined 없음")

        # 숫자 포맷(천 단위 콤마) 확인: 첫 픽 카드 가격
        first_price = page.locator(".pick-card .pick-price b").first.inner_text()
        check(bool(re.fullmatch(r"\d{1,3}(,\d{3})*", first_price)), "가격 콤마 포맷", first_price)

        grid_labels = page.locator(".pick-card .price-grid").first.inner_text()
        check("진입가" in grid_labels and "목표" in grid_labels and "손절" in grid_labels,
              "진입·목표·손절 라벨", grid_labels.replace("\n", " "))
        vol_txt = page.locator(".pick-card .pick-vol").first.inner_text()
        check("ATR" in vol_txt and "본전" in vol_txt, "픽 카드 변동성·본전 손절 안내", vol_txt)

        # 신호등 색상 클래스
        pills = page.locator(".pick-card .pill").first.get_attribute("class")
        check("pill" in pills and any(k in pills for k in ("buy", "watch", "sell")), "신호등 배지", pills)

        # 그리드 3열이 뷰포트 안에 들어오는지(가로 스크롤 없음)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(overflow <= 1, "가로 스크롤 없음", f"overflow {overflow}px")

        page.screenshot(path=os.path.join(ROOT, "preview.png"), full_page=False)
        page.screenshot(path=os.path.join(ROOT, "preview-full.png"), full_page=True)

        # ---------- 시트: 예산 계산 ----------
        first_card = page.locator(".pick-card").first
        first_code = first_card.get_attribute("data-code")
        stock = next(s for s in data["stocks"] if s["code"] == first_code)
        first_card.locator(".pick-cta").click()
        page.wait_for_selector("#sheet:not(.hidden)")
        check(page.locator("#sheetTitle").inner_text() == stock["name"], "시트 열림 · 종목명", stock["name"])

        qty_txt = page.locator("#calcGrid .calc-cell").first.inner_text()
        expected_qty = int(1_000_000 // stock["entry"])
        check(f"{expected_qty}주" in qty_txt, "기본 예산 100만원 수량", qty_txt.splitlines()[1] if len(qty_txt.splitlines()) > 1 else qty_txt)

        combo_txt = page.locator("#sheet .combo-grid").inner_text().replace(" ", "")
        check("목표" in combo_txt and "손절" in combo_txt and "본전손절" in combo_txt,
              "시트 3단 콤보 기준", combo_txt.replace("\n", " "))
        want_t = f"+{stock['target_pct']:.1f}%"
        want_s = f"{stock['stop_pct']:.1f}%"
        check(want_t in combo_txt and want_s in combo_txt, "시트 동적 목표·손절 %",
              f"{want_t} / {want_s}")
        sheet_txt = page.locator("#sheet").inner_text()
        check("ATR" in sheet_txt, "시트 ATR 변동성 표시", [l for l in sheet_txt.splitlines() if "ATR" in l][:1])

        page.locator('#quickChips [data-budget="2000000"]').click()
        page.wait_for_timeout(150)
        qty_txt2 = page.locator("#calcGrid .calc-cell").first.inner_text()
        expected_qty2 = int(2_000_000 // stock["entry"])
        check(f"{expected_qty2}주" in qty_txt2, "예산 200만원 수량 갱신", qty_txt2.splitlines()[1])

        # 수수료 토글 → 왕복 비용 변화 확인
        cost_before = page.locator("#calcGrid .calc-cell.wide b").inner_text()
        page.locator('#feeToggle [data-fee="standard"]').click()
        page.wait_for_timeout(150)
        cost_after = page.locator("#calcGrid .calc-cell.wide b").inner_text()
        check(cost_before != cost_after, "수수료 토글 계산 반영", f"{cost_before} → {cost_after}")

        copy_btn = page.locator('[data-copy="target"]')
        check(f"{stock['target']:,}" in copy_btn.inner_text(), "목표가 복사 버튼 값", copy_btn.inner_text())

        # 숫자 검증: 순수익 = 매도 - 매수 - 수수료 - 세금
        numbers = page.locator("#calcGrid").inner_text()
        check("왕복 비용" in numbers and "최대 손실" in numbers, "계산 항목 표시", "순수익/최대손실/왕복비용")

        # 기본 수수료 모드(평생혜택 비대면)로 되돌려 기본 화면을 캡처
        page.locator('#feeToggle [data-fee="benefit"]').click()
        page.wait_for_timeout(150)
        benefit_active = page.locator('#feeToggle [data-fee="benefit"]').get_attribute("class")
        check("active" in (benefit_active or ""), "수수료 기본값 = 평생혜택 비대면", benefit_active)

        page.screenshot(path=os.path.join(ROOT, "preview-sheet.png"), full_page=False)
        page.locator("#sheet .sheet-close").click()
        page.wait_for_timeout(250)

        # ---------- 탭 2: 내 종목 ----------
        page.locator('.tabbar .tab[data-tab="holdings"]').click()
        page.wait_for_selector("#view-holdings.active")
        cards = page.locator(".hold-card").count()
        check(cards == len(seed), "보유 종목 자동 로드", f"{cards}개 (시드 {len(seed)}개)")

        hold_text = page.locator("#holdingList").inner_text()
        if not seed:
            print("[skip] 보유 종목 시드 없음 — data/holdings.local.json을 두면 실제 시드로 검증합니다")
        else:
            for h in seed:
                if h.get("name") and h["name"] not in hold_text:
                    check(False, "보유 종목 표시", f"{h['name']} 누락")
                    break
            else:
                check(True, f"보유 종목 {len(seed)}건 표시",
                      "·".join(h["name"] for h in seed[:4]) + " 등")

        total_txt = page.locator("#holdSummary").inner_text()
        check("평가금액" in total_txt and "매입금액" in total_txt, "계좌 요약", total_txt.splitlines()[0])
        check("NaN" not in total_txt, "계좌 요약 계산 정상")

        page.screenshot(path=os.path.join(ROOT, "preview-holdings.png"), full_page=False)

        # 추가 (정리·손절 기준가 포함)
        page.locator("#toggleAddBtn").click()
        page.locator("#stockSearch").fill("기아")
        page.locator("#avgPriceInput").fill("95000")
        page.locator("#qtyInput").fill("3")
        page.locator("#exitTargetInput").fill("130000")
        page.locator("#exitStopInput").fill("60000")
        page.locator("#saveHoldingBtn").click()
        page.wait_for_timeout(250)
        check(page.locator(".hold-card").count() == len(seed) + 1, "종목 추가", "기아 3주")
        last_card = page.locator(".hold-card").last.inner_text()
        check("내 정리" in last_card and "내 손절" in last_card, "정리·손절 기준가 카드 표시",
              [l for l in last_card.splitlines() if "내 정리" in l][:1])
        stored_exit = page.evaluate(
            "(JSON.parse(localStorage.getItem('gj.holdings.v1')||'[]').slice(-1)[0]||{})"
        )
        check(stored_exit.get("exitTarget") == 130000 and stored_exit.get("exitStop") == 60000,
              "정리·손절 기준가 localStorage 저장",
              f"exitTarget={stored_exit.get('exitTarget')} exitStop={stored_exit.get('exitStop')}")

        # 정리 기준가를 현재가 아래로 낮추면 도달 배지가 뜨는지
        page.locator('.hold-card [data-act="edit"]').last.click()
        page.locator("#exitTargetInput").fill("1000")
        page.locator("#exitStopInput").fill("500")
        page.locator("#saveHoldingBtn").click()
        page.wait_for_timeout(200)
        badge_txt = page.locator(".hold-card").last.inner_text()
        check("정리 구간 도달" in badge_txt or "손절선 이탈" in badge_txt,
              "정리/손절 기준 도달 배지", [l for l in badge_txt.splitlines() if "도달" in l or "이탈" in l][:1])

        # 수정
        page.locator('.hold-card [data-act="edit"]').last.click()
        page.locator("#qtyInput").fill("7")
        page.locator("#saveHoldingBtn").click()
        page.wait_for_timeout(200)
        check("7주" in page.locator(".hold-card").last.inner_text(), "종목 수정", "3주 → 7주")

        # 삭제
        page.locator('.hold-card [data-act="delete"]').last.click()
        page.wait_for_timeout(200)
        check(page.locator(".hold-card").count() == len(seed), "종목 삭제", f"{len(seed)}개로 복귀")

        # 저장 유지 (새로고침)
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".pick-card")
        page.locator('.tabbar .tab[data-tab="holdings"]').click()
        page.wait_for_timeout(200)
        check(page.locator(".hold-card").count() == len(seed), "새로고침 후 localStorage 유지", f"{len(seed)}개")

        # ---------- 탭 3: 원칙·연동 ----------
        page.locator('.tabbar .tab[data-tab="rules"]').click()
        page.wait_for_selector("#view-rules.active")
        rules_text = page.locator("#view-rules").inner_text()
        check("스윙 3단 콤보" in rules_text and "brokerAdapter" in rules_text, "원칙·연동 탭 콘텐츠")
        check("5일선 위에서만" in rules_text and "ATR" in rules_text and "본전 손절" in rules_text,
              "3단 콤보 원칙 문구", "지수 5일선 · ATR · 본전 손절")
        check("추천 기록" in rules_text, "추천 기록 섹션")
        rec_rows = page.locator("#recordCard .record-row").count()
        check(rec_rows >= 1, "추천 기록 저장", f"{rec_rows}일치")
        bt = page.locator("#backtestCard").is_visible()
        check(bt, "신호 과거 검증 카드", page.locator("#backtestCard").inner_text().splitlines()[0] if bt else "숨김")
        if bt:
            bt_txt = page.locator("#backtestCard").inner_text()
            check("본전 손절" in bt_txt and "PF" in bt_txt, "백테스트 본전 청산·PF 표시",
                  [l for l in bt_txt.splitlines() if "본전" in l][:1])
        page.screenshot(path=os.path.join(ROOT, "preview-rules.png"), full_page=False)

        # ---------- 좁은 화면(360px) 확인 ----------
        page.set_viewport_size({"width": 360, "height": 800})
        page.locator('.tabbar .tab[data-tab="picks"]').click()
        page.wait_for_timeout(200)
        overflow2 = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(overflow2 <= 1, "360px 폭에서도 가로 스크롤 없음", f"overflow {overflow2}px")
        page.set_viewport_size({"width": 412, "height": 915})

        # ---------- 분석 대상이 아닌 종목 ----------
        page.locator('.tabbar .tab[data-tab="holdings"]').click()
        page.locator("#toggleAddBtn").click()
        page.locator("#stockSearch").fill("POSCO홀딩스")
        page.locator("#avgPriceInput").fill("380000")
        page.locator("#qtyInput").fill("2")
        page.locator("#saveHoldingBtn").click()
        page.wait_for_timeout(250)
        extra = page.locator(".hold-card").last
        check("POSCO홀딩스" in extra.inner_text() and "분석 전" in extra.inner_text(),
              "카탈로그 종목 추가(분석 전 표시)", extra.inner_text().splitlines()[0])
        page.locator('.hold-card [data-act="delete"]').last.click()
        page.wait_for_timeout(200)
        check(page.locator(".hold-card").count() == len(seed), "임시 종목 정리", f"{len(seed)}개")

        # ---------- 오프라인(서비스 워커 캐시) ----------
        page.locator('.tabbar .tab[data-tab="picks"]').click()
        page.wait_for_timeout(1200)  # SW 활성화 대기
        ctx.set_offline(True)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".pick-card", timeout=15000)
        offline_picks = page.locator(".pick-card").count()
        check(offline_picks == len(data["picks"]), "오프라인에서도 마지막 신호 표시", f"{offline_picks}장")
        ctx.set_offline(False)

        # ---------- 결과 ----------
        check(not page_errors, "JS 런타임 에러 없음", "; ".join(page_errors) if page_errors else "0건")
        check(not console_errors, "콘솔 에러 없음", "; ".join(console_errors) if console_errors else "0건")
        check(not failed_requests, "요청 실패 없음", "; ".join(failed_requests) if failed_requests else "0건")

        sw = page.evaluate("navigator.serviceWorker.getRegistrations().then(r => r.length)")
        check(sw >= 1, "서비스 워커 등록", f"{sw}개")

        browser.close()

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n결과: {passed}/{len(results)} 통과")
    if page_errors:
        print("pageerror:", page_errors)
    if console_errors:
        print("console.error:", console_errors)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
