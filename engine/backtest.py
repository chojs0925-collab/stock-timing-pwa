#!/usr/bin/env python3
"""3단 콤보 규칙 과거 검증(백테스트).

data/raw 에 저장된 일봉으로, 과거 각 시점의 점수/신호를 다시 계산해 다음 규칙을 검증한다.

    ① 손절 = 1.5 × ATR14 (3.5%~7.0% 클램프), 목표 = 손절 × 2 (7%~14%)
    ② +3% 도달 시 손절가를 본전(진입가)으로 이동
    ③ 해당 시장(코스피/코스닥) 지수가 5일선 위일 때만 신규 진입
    - 최대 10거래일 보유, 같은 날 목표·손절이 모두 닿으면 손절(보수적)
    - 장중에 갱신된 부분봉(오늘 봉이 장 마감 전일 때)은 검증에서 제외

실행: python3 engine/backtest.py
결과: data/backtest.json (앱 '원칙·연동' 탭의 신호 과거 검증 카드)
"""
from __future__ import annotations

import json
import os
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import naver  # noqa: E402
from indicators import (  # noqa: E402
    BREAKEVEN_TRIGGER_PCT,
    DEFAULT_FEE_RATE,
    STOP_ATR_MULT,
    STOP_MAX_PCT,
    STOP_MIN_PCT,
    TARGET_RR,
    TAX_RATE,
    evaluate,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
HOLD_DAYS = 10
INDEX_MA = 5
MARKET_CLOSE = 1535  # 이 시각 전이면 오늘 봉은 장중 부분봉으로 본다


def net_return(entry: int, exit_price: float, market: str) -> float:
    fee = DEFAULT_FEE_RATE
    tax = TAX_RATE.get(market, 0.002)
    gross = (exit_price - entry) / entry
    cost = fee * 2 + tax
    return (gross - cost) * 100


def drop_partial(rows: list[dict]) -> list[dict]:
    """오늘 봉이 아직 장 마감 전(부분봉)이면 검증에서 제외한다."""
    if not rows:
        return rows
    now = naver.now_kst()
    if rows[-1]["date"] == now.strftime("%Y-%m-%d") and (now.hour * 100 + now.minute) < MARKET_CLOSE:
        return rows[:-1]
    return rows


def index_rows(symbol: str) -> list[dict]:
    """지수 일봉 — 새로 받아 오고, 실패하면 data/raw/index_{symbol}.json 캐시를 쓴다."""
    cache = os.path.join(RAW, f"index_{symbol}.json")
    try:
        payload = naver.fetch_daily(symbol, 260)
        rows = payload["rows"]
        if rows:
            os.makedirs(RAW, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            return rows
    except Exception as exc:
        print(f"[warn] {symbol} 지수 조회 실패 — 캐시 사용: {exc}", file=sys.stderr)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f).get("rows") or []
    return []


class IndexGuard:
    """날짜별 '지수 > 5일선' 상태. 없는 날짜는 직전 거래일 값으로 판단한다."""

    def __init__(self, rows: list[dict]):
        self.dates: list[str] = []
        self.allow: dict[str, bool] = {}
        closes = [r["close"] for r in rows]
        for i, row in enumerate(rows):
            if i + 1 < INDEX_MA:
                continue
            ma5 = sum(closes[i + 1 - INDEX_MA: i + 1]) / INDEX_MA
            self.dates.append(row["date"])
            self.allow[row["date"]] = closes[i] > ma5

    def ok(self, date: str) -> bool:
        if not self.dates:
            return True  # 지수 데이터가 없으면 막지 않는다
        pos = bisect_right(self.dates, date) - 1
        if pos < 0:
            return True
        return self.allow[self.dates[pos]]


def run_stock(code: str, market: str, guard: IndexGuard) -> tuple[list[dict], int]:
    path = os.path.join(RAW, f"{code}.json")
    if not os.path.exists(path):
        return [], 0
    with open(path, encoding="utf-8") as f:
        rows = drop_partial(json.load(f)["rows"])
    if len(rows) < 80:
        return [], 0

    trades = []
    blocked = 0
    i = 65
    while i < len(rows) - 2:
        window = rows[: i + 1]
        result = evaluate(window)
        if not result or result["signal"] != "BUY":
            i += 1
            continue
        if not guard.ok(rows[i]["date"]):
            # 지수가 5일선 아래면 신규 매수 보류 — 신호가 있어도 진입하지 않는다
            blocked += 1
            i += 1
            continue

        entry = result["entry"]
        target = result["target"]
        stop = result["stop"]
        cur_stop = stop
        mfe = 0.0
        exit_px, exit_idx, outcome = None, None, None

        for j in range(i + 1, min(i + 1 + HOLD_DAYS, len(rows))):
            bar = rows[j]
            mfe = max(mfe, (bar["high"] / entry - 1) * 100)
            if bar["low"] <= cur_stop:          # 같은 날 둘 다 닿으면 손절 먼저(보수적)
                exit_px, exit_idx = cur_stop, j
                outcome = "stop" if cur_stop < entry else "breakeven"
                break
            if bar["high"] >= target:
                exit_px, exit_idx, outcome = target, j, "target"
                break
            if mfe >= BREAKEVEN_TRIGGER_PCT * 100:   # +3% 도달 → 손절가를 본전으로
                cur_stop = max(cur_stop, entry)

        if exit_px is None:
            exit_idx = min(i + HOLD_DAYS, len(rows) - 1)
            exit_px = rows[exit_idx]["close"]
            outcome = "time"

        trades.append({
            "code": code,
            "market": market,
            "date": rows[i]["date"],
            "entry": entry,
            "target": target,
            "stop": stop,
            "exit": int(exit_px),
            "outcome": outcome,
            "days": exit_idx - i,
            "return_pct": round(net_return(entry, exit_px, market), 2),
            "score": result["score"],
            "atr_pct": result.get("atr_pct"),
            "stop_pct": result["stop_pct"],
            "target_pct": result["target_pct"],
            "mfe": round(mfe, 2),
        })
        i = exit_idx + 1  # 같은 흐름을 중복 계산하지 않도록 종료 다음 날부터
    return trades, blocked


def main() -> int:
    with open(os.path.join(ROOT, "engine", "universe.json"), encoding="utf-8") as f:
        universe = json.load(f)["stocks"]

    guards = {
        "KOSPI": IndexGuard(index_rows("KOSPI")),
        "KOSDAQ": IndexGuard(index_rows("KOSDAQ")),
    }
    for key, g in guards.items():
        print(f"[info] {key} 지수 5일선 데이터 {len(g.dates)}일 (구간 "
              f"{g.dates[0] if g.dates else '-'}~{g.dates[-1] if g.dates else '-'})")

    trades: list[dict] = []
    blocked_total = 0
    for item in universe:
        market = item.get("market", "KOSPI")
        got, blocked = run_stock(item["code"], market, guards.get(market, IndexGuard([])))
        blocked_total += blocked
        for t in got:
            t["name"] = item["name"]
        trades.extend(got)

    if not trades:
        print("[error] 검증할 매수 신호가 없습니다.", file=sys.stderr)
        return 1

    trades.sort(key=lambda t: (t["date"], t["code"]))
    wins = sum(1 for t in trades if t["outcome"] == "target")
    losses = sum(1 for t in trades if t["outcome"] == "stop")
    bes = sum(1 for t in trades if t["outcome"] == "breakeven")
    times = len(trades) - wins - losses - bes
    avg_ret = sum(t["return_pct"] for t in trades) / len(trades)
    avg_days = sum(t["days"] for t in trades) / len(trades)
    gains = [t["return_pct"] for t in trades if t["return_pct"] > 0]
    pains = [t["return_pct"] for t in trades if t["return_pct"] <= 0]
    pf = (abs(sum(gains) / sum(pains)) if pains and sum(pains) else None)

    # 검증 구간의 시장 상황(코스피) — 결과 해석에 함께 보여준다
    market_ctx = None
    try:
        idx = index_rows("KOSPI")
        start, end = trades[0]["date"], trades[-1]["date"]
        s = next((r for r in idx if r["date"] >= start), None)
        e = next((r for r in reversed(idx) if r["date"] <= end), None)
        if s and e:
            market_ctx = {
                "start": s["date"], "start_level": round(s["close"], 2),
                "end": e["date"], "end_level": round(e["close"], 2),
                "change_pct": round((e["close"] / s["close"] - 1) * 100, 1),
            }
    except Exception as exc:  # 지수 조회 실패는 검증 결과를 막지 않는다
        print(f"[warn] 지수 맥락 생략: {exc}", file=sys.stderr)

    summary = {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakevens": bes,
        "timeouts": times,
        "win_rate": round(wins / len(trades) * 100, 1),
        "loss_rate": round(losses / len(trades) * 100, 1),
        "breakeven_rate": round(bes / len(trades) * 100, 1),
        "timeout_rate": round(times / len(trades) * 100, 1),
        "blocked_by_index": blocked_total,
        "avg_return": round(avg_ret, 2),
        "avg_days": round(avg_days, 1),
        "avg_win": round(sum(gains) / len(gains), 2) if gains else 0,
        "avg_loss": round(sum(pains) / len(pains), 2) if pains else 0,
        "pf": round(pf, 2) if pf else None,
        "avg_stop_pct": round(sum(t["stop_pct"] for t in trades) / len(trades), 2),
        "avg_target_pct": round(sum(t["target_pct"] for t in trades) / len(trades), 2),
        "best": max(t["return_pct"] for t in trades),
        "worst": min(t["return_pct"] for t in trades),
    }

    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        "period": f"{trades[0]['date']} ~ {trades[-1]['date']}",
        "rule": (
            "지수 5일선 위에서만 진입 · 손절 1.5×ATR14(3.5~7.0%) / 목표 2배(7~14%) · "
            "+3% 도달 시 본전 손절 · 최대 10거래일 보유, 수수료·매도세 반영"
        ),
        "params": {
            "hold_days": HOLD_DAYS,
            "index_ma": INDEX_MA,
            "atr_period": 14,
            "atr_mult": STOP_ATR_MULT,
            "stop_min_pct": STOP_MIN_PCT,
            "stop_max_pct": STOP_MAX_PCT,
            "target_rr": TARGET_RR,
            "breakeven_trigger_pct": BREAKEVEN_TRIGGER_PCT,
        },
        "summary": summary,
        "market_context": market_ctx,
        "note": "과거 일봉으로 같은 규칙을 되돌려 본 결과예요. 미래 수익을 보장하지 않습니다.",
        "trades": trades,
    }
    with open(os.path.join(DATA, "backtest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print()
    print(f"검증 구간 {payload['period']} · 매수 신호 진입 {summary['trades']}건 "
          f"(지수 5일선 아래로 건너뛴 신호 {blocked_total}건)")
    print(f"목표 도달 {wins} / 손절 {losses} / 본전 손절 {bes} / 기간 종료 {times} "
          f"→ 목표 도달률 {summary['win_rate']}% · 손절률 {summary['loss_rate']}%")
    print(f"평균 수익률 {summary['avg_return']}% · PF {summary['pf']} · 평균 보유 {summary['avg_days']}일 "
          f"· 평균 손절 {summary['avg_stop_pct']}% / 평균 목표 {summary['avg_target_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
