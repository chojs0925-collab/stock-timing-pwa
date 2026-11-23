#!/usr/bin/env python3
"""국장 일봉 스윙 타이밍 분석 엔진.

실행:
    python3 engine/analyze.py            # data/signals.json, data/catalog.json, data/history/{기준일}.json 생성
    python3 engine/analyze.py --offline  # 네트워크 없이 기존 data/raw 캐시로 재계산
    python3 engine/analyze.py --include-holdings  # 보유 종목 시드까지 포함(로컬 전용, 공개 배포 금지)

보유 종목: 실제 보유분은 data/holdings.local.json에만 두고(공개 저장소 제외 대상),
공개 배포용 data/holdings.json은 빈 배열로 둔다.

데이터 출처: 네이버 금융 공개 일봉 (fchart.stock.naver.com/sise.nhn)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import naver  # noqa: E402
from indicators import DEFAULT_FEE_RATE, FEE_PRESETS, TAX_RATE, evaluate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
HIST = os.path.join(DATA, "history")

TARGET_PCT = 0.08   # 기존 호환용 기본값 — 실제 손절·목표는 14일 ATR 로 종목별 산출
STOP_PCT = 0.04
FETCH_COUNT = 160  # 약 8개월치 일봉
INDEX_MA = 5       # 지수 5일선 위에서만 신규 매수


def load_universe() -> list[dict]:
    with open(os.path.join(ROOT, "engine", "universe.json"), encoding="utf-8") as f:
        return json.load(f)["stocks"]


def load_raw(code: str) -> list[dict] | None:
    path = os.path.join(RAW, f"{code}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rows"]


def save_raw(code: str, payload: dict) -> None:
    os.makedirs(RAW, exist_ok=True)
    with open(os.path.join(RAW, f"{code}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def build_catalog() -> list[dict]:
    """종목코드 입력용 카탈로그: 코스피/코스닥 시총 상위 100종목씩."""
    rows: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            for s in naver.fetch_market_list(market, size=100, pages=1):
                if s["code"] and s["name"]:
                    rows.setdefault(s["code"], s)
        except Exception as exc:  # 카탈로그는 부가 정보 — 실패해도 본 분석은 진행
            print(f"[warn] {market} 종목 목록 실패: {exc}", file=sys.stderr)
    return sorted(rows.values(), key=lambda s: (-s.get("market_cap_eok", 0), s["code"]))


def index_summary(symbol: str, offline: bool = False) -> dict | None:
    """시장 지수(KOSPI/KOSDAQ) 국면 요약 + 5일선 기준 신규 매수 허용 여부.

    지수가 5일선 아래면 그 시장 종목의 신규 매수는 보류(allow_new_buy=False)한다.
    일봉은 data/raw/index_{심볼}.json 에 캐시해 두어 오프라인·백테스트에서도 같은 5일선을 쓴다.
    """
    cache_path = os.path.join(RAW, f"index_{symbol}.json")
    rows = None
    if not offline:
        try:
            payload = naver.fetch_daily(symbol, FETCH_COUNT)
            rows = payload["rows"]
            os.makedirs(RAW, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
        except Exception as exc:
            print(f"[warn] {symbol} 지수 조회 실패: {exc}", file=sys.stderr)
            rows = None
    if rows is None and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            rows = json.load(f).get("rows") or []
    if not rows or len(rows) < 65:
        return None
    closes = [r["close"] for r in rows]
    ma5 = sum(closes[-INDEX_MA:]) / INDEX_MA
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    level = closes[-1]
    if level > ma20 > ma60:
        phase = "상승 추세"
    elif level > ma20:
        phase = "반등 시도"
    elif ma20 > ma60:
        phase = "조정"
    else:
        phase = "하락 추세"
    return {
        "symbol": symbol,
        "level": round(level, 2),
        "change_pct": round((level / closes[-2] - 1) * 100, 2),
        "ma5": round(ma5, 2),
        "allow_new_buy": bool(level > ma5),
        "above_ma5_pct": round((level / ma5 - 1) * 100, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "week_change_pct": round((level / closes[-6] - 1) * 100, 2) if len(closes) > 6 else 0,
        "phase": phase,
        "date": rows[-1]["date"],
    }


def holdings_path() -> str | None:
    """보유 종목 파일 경로. 개인 보유분은 data/holdings.local.json 에 두고 공개하지 않는다."""
    for name in ("holdings.local.json", "holdings.json"):
        path = os.path.join(DATA, name)
        if os.path.exists(path):
            return path
    return None


def load_holdings(stocks: list[dict]) -> dict:
    """실제 보유 종목에 현재가·신호등을 붙여 앱 초기 데이터 형태로 만든다."""
    path = holdings_path()
    if not path:
        return {"holdings": []}
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if isinstance(spec, list):
        spec = {"holdings": spec}
    by_code = {s["code"]: s for s in stocks}
    out = []
    for h in spec.get("holdings", []):
        if not h or "code" not in h:
            continue
        avg = h.get("avg_price", h.get("avgPrice", 0))
        s = by_code.get(str(h["code"]))
        item = {
            "code": str(h["code"]),
            "name": h.get("name") or (s["name"] if s else str(h["code"])),
            "qty": int(h.get("qty") or 0),
            "avg_price": int(avg or 0),
        }
        if s:
            item.update({
                "market": s["market"],
                "close": s["close"],
                "signal": s["signal"],
                "signal_label": s["signal_label"],
                "score": s["score"],
                "target": s["target"],
                "stop": s["stop"],
                "rsi14": s["rsi14"],
                "vol_ratio": s["vol_ratio"],
            })
        out.append(item)
    spec["holdings"] = out
    return spec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="data/raw 캐시만 사용")
    ap.add_argument(
        "--include-holdings",
        action="store_true",
        help="보유 종목 시드를 signals.json에 넣는다(로컬 확인용). 공개 저장소로 올릴 때는 쓰지 말 것",
    )
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    universe = load_universe()
    stocks: list[dict] = []
    errors: list[dict] = []
    market_date = ""

    # 지수 5일선 상태를 먼저 구해 두어야 종목별 신규 매수 차단을 적용할 수 있다.
    indexes: dict[str, dict] = {}
    for sym, key in (("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")):
        info = index_summary(sym, offline=args.offline)
        if info:
            indexes[key] = info
    allow_new_buy = {key.upper(): bool(v.get("allow_new_buy", True)) for key, v in indexes.items()}
    for key, allowed in allow_new_buy.items():
        if not allowed:
            print(f"[info] {key} 지수가 {INDEX_MA}일선 아래 — {key} 종목 신규 매수 보류", file=sys.stderr)

    for i, item in enumerate(universe, 1):
        code = item["code"]
        rows = None
        fetched_name = ""
        if not args.offline:
            try:
                payload = naver.fetch_daily(code, FETCH_COUNT)
                rows = payload["rows"]
                fetched_name = payload.get("name", "")
                if len(rows) < 65:
                    raise RuntimeError(f"일봉이 {len(rows)}개뿐 (60일선 계산 불가)")
                save_raw(code, payload)
            except Exception as exc:
                print(f"[warn] {code} {item['name']} 조회 실패: {exc}", file=sys.stderr)
                rows = None
        if rows is None:
            rows = load_raw(code)
        if rows is None:
            errors.append({"code": code, "name": item["name"], "error": "데이터 없음"})
            continue

        try:
            result = evaluate(rows)  # 손절·목표는 14일 ATR 변동성 기준
        except Exception as exc:
            errors.append({"code": code, "name": item["name"], "error": f"계산 실패: {exc}"})
            continue
        if result is None:
            errors.append({"code": code, "name": item["name"], "error": "데이터 부족"})
            continue

        market = item.get("market", "KOSPI")
        blocked = allow_new_buy.get(str(market).upper()) is False
        result["market_blocked"] = blocked
        if blocked and result["signal"] == "BUY":
            # 지수가 5일선 아래면 점수가 높아도 신규 매수 차단 → 관망으로 내린다
            result["signal"] = "WATCH"
            result["signal_label"] = "🟡 관망"
            result["flags"] = list(result.get("flags", [])) + ["지수5일선하회"]
            result["blocked_from"] = "BUY"

        last_date = rows[-1]["date"]
        if last_date > market_date:
            market_date = last_date
        stocks.append({
            "code": code,
            "name": item.get("name") or fetched_name or code,
            "market": market,
            "sector": item.get("sector", ""),
            "date": last_date,
            **result,
        })
        print(
            f"[{i:2d}/{len(universe)}] {item['name']:<12} {result['close']:>9,}원 "
            f"{result['signal_label']} {result['score']:>3}점 "
            f"(MA20 {result['ma20']:,} / RSI {result['rsi14']} / 거래량 {result['vol_ratio']}배)"
        )
        time.sleep(0.25)

    if not stocks:
        print("[error] 분석된 종목이 없습니다. 네트워크/캐시를 확인하세요.", file=sys.stderr)
        return 1

    stocks.sort(key=lambda s: (-s["score"], s["code"]))
    # 오늘의 픽: 매수 적합도(점수) 상위 5종목. 신규 매수 차단 시장이 있으면
    # 그 시장 종목은 "신규 매수 보류" 참고용으로 최대 5개까지 보여준다.
    blocked_markets = [k for k, v in allow_new_buy.items() if not v]
    fill_target = 5 if blocked_markets else 3
    buys = [s for s in stocks if s["signal"] == "BUY"][:5]
    picks = list(buys)
    for s in stocks:
        if len(picks) >= fill_target:
            break
        if s not in picks:
            picks.append(s)
    picks.sort(key=lambda s: -s["score"])
    # picks 를 그대로 담으면 stocks 객체와 섞이므로 얇은 사본에 표시용 필드를 붙인다
    # advisory_only = 오늘 매수 신호가 아니라 "점수 참고용/보류" 카드
    picks = [{**s, "advisory_only": s["signal"] != "BUY"} for s in picks]

    catalog = build_catalog() if not args.offline else []
    if catalog:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": naver.now_kst().isoformat(timespec="seconds"),
                "count": len(catalog),
                "stocks": catalog,
            }, f, ensure_ascii=False, indent=1)

    counts = {k: sum(1 for s in stocks if s["signal"] == k) for k in ("BUY", "WATCH", "SELL")}
    holdings = load_holdings(stocks) if args.include_holdings else {"holdings": []}
    payload = {
        "app": "국장 매매 타이밍",
        "version": 1,
        "generated_at": naver.now_kst().isoformat(timespec="seconds"),
        "market_date": market_date,
        "source": "네이버 금융 일봉 (fchart.stock.naver.com/sise.nhn)",
        "params": {
            "target_pct": TARGET_PCT,
            "stop_pct": STOP_PCT,
            "atr_period": 14,
            "atr_mult": 1.5,
            "stop_min_pct": 0.035,
            "stop_max_pct": 0.070,
            "target_rr": 2.0,
            "breakeven_trigger_pct": 0.03,
            "index_ma": INDEX_MA,
            "rules": (
                "3단 콤보 — ① 손절 = 1.5×ATR14(3.5~7.0% 클램프) · 목표 = 손절×2(7~14%) "
                "② +3% 도달 시 손절가를 본전(진입가)으로 이동 "
                "③ 지수(코스피·코스닥) 5일선 위에서만 신규 매수"
            ),
            "budget_default": 1_000_000,
            "fee_rate": DEFAULT_FEE_RATE,
            "fee_presets": FEE_PRESETS,
            "tax_rate": TAX_RATE,
            "tick_table": "KRX 호가단위: <2,000원 1원 / ~5,000원 5원 / ~20,000원 10원 / ~50,000원 50원 / ~200,000원 100원 / ~500,000원 500원 / 이상 1,000원",
        },
        "summary": {
            "analyzed": len(stocks),
            "buy": counts["BUY"],
            "watch": counts["WATCH"],
            "sell": counts["SELL"],
            "avg_change_pct": round(sum(s["change_pct"] for s in stocks) / len(stocks), 2),
            "avg_score": round(sum(s["score"] for s in stocks) / len(stocks), 1),
            "blocked_buy": sum(1 for s in stocks if s.get("blocked_from") == "BUY"),
            "blocked_markets": blocked_markets,
        },
        "index": indexes,
        "holdings_seed": holdings,
        "picks": picks,
        "stocks": stocks,
        "errors": errors,
    }

    with open(os.path.join(DATA, "signals.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # 추천 기록(성과 트래킹용) — 기준일별 스냅샷 저장, 같은 기준일은 덮어씀
    os.makedirs(HIST, exist_ok=True)
    snapshot = {
        "market_date": market_date,
        "generated_at": payload["generated_at"],
        "index": {k: {"level": v["level"], "ma5": v["ma5"], "allow_new_buy": v["allow_new_buy"]}
                  for k, v in indexes.items()},
        "picks": [
            {k: s[k] for k in ("code", "name", "market", "close", "signal", "signal_label",
                               "score", "entry", "target", "stop", "target_pct", "stop_pct",
                               "atr_pct", "market_blocked", "advisory_only") if k in s}
            for s in picks
        ],
        "all": [
            {k: s[k] for k in ("code", "name", "signal", "score", "close", "entry", "target",
                               "stop", "atr_pct", "market_blocked") if k in s}
            for s in stocks
        ],
    }
    with open(os.path.join(HIST, f"{market_date}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    hist_files = sorted(x for x in os.listdir(HIST) if x.endswith(".json") and x != "index.json")
    index = []
    for name in hist_files:
        with open(os.path.join(HIST, name), encoding="utf-8") as f:
            snap = json.load(f)
        index.append({"market_date": snap["market_date"], "picks": snap["picks"]})
    with open(os.path.join(HIST, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": payload["generated_at"], "records": index}, f,
                  ensure_ascii=False, indent=1)

    print()
    print(f"기준일 {market_date} · 분석 {len(stocks)}종목 · 매수 {counts['BUY']} / 관망 {counts['WATCH']} / 주의 {counts['SELL']}")
    print("오늘의 픽:", ", ".join(f"{s['name']}({s['score']})" for s in picks))
    if errors:
        print(f"실패 {len(errors)}건:", errors, file=sys.stderr)
    print(f"저장: {os.path.join('data', 'signals.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
