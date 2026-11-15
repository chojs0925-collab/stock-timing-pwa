#!/usr/bin/env python3
"""신호 과거 검증(백테스트).

data/raw 에 저장된 일봉으로, 과거 각 시점의 점수/신호를 다시 계산해
'매수 신호가 나온 날 진입 → 목표 +8% / 손절 -4% / 10거래일 후 종료'를 반복 검증한다.

실행: python3 engine/backtest.py
결과: data/backtest.json (앱 '원칙·연동' 탭의 신호 과거 검증 카드)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import DEFAULT_FEE_RATE, TAX_RATE, evaluate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
HOLD_DAYS = 10


def net_return(entry: int, exit_price: float, market: str) -> float:
    fee = DEFAULT_FEE_RATE
    tax = TAX_RATE.get(market, 0.002)
    gross = (exit_price - entry) / entry
    cost = fee * 2 + tax
    return (gross - cost) * 100


def run_stock(code: str, market: str) -> list[dict]:
    path = os.path.join(RAW, f"{code}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)["rows"]
    if len(rows) < 80:
        return []

    trades = []
    i = 65
    while i < len(rows) - 2:
        window = rows[: i + 1]
        result = evaluate(window)
        if not result or result["signal"] != "BUY":
            i += 1
            continue

        entry = result["entry"]
        target = result["target"]
        stop = result["stop"]
        exit_px, exit_idx, outcome = None, None, None

        for j in range(i + 1, min(i + 1 + HOLD_DAYS, len(rows))):
            bar = rows[j]
            if bar["low"] <= stop:          # 같은 날 둘 다 닿으면 손절 먼저(보수적)
                exit_px, exit_idx, outcome = stop, j, "stop"
                break
            if bar["high"] >= target:
                exit_px, exit_idx, outcome = target, j, "target"
                break

        if exit_px is None:
            exit_idx = min(i + HOLD_DAYS, len(rows) - 1)
            exit_px = rows[exit_idx]["close"]
            outcome = "time"

        trades.append({
            "code": code,
            "date": rows[i]["date"],
            "entry": entry,
            "exit": int(exit_px),
            "outcome": outcome,
            "days": exit_idx - i,
            "return_pct": round(net_return(entry, exit_px, market), 2),
            "score": result["score"],
        })
        i = exit_idx + 1  # 같은 흐름을 중복 계산하지 않도록 종료 다음 날부터
    return trades


def main() -> int:
    with open(os.path.join(ROOT, "engine", "universe.json"), encoding="utf-8") as f:
        universe = json.load(f)["stocks"]

    trades: list[dict] = []
    for item in universe:
        got = run_stock(item["code"], item.get("market", "KOSPI"))
        if got:
            for t in got:
                t["name"] = item["name"]
            trades.extend(got)

    if not trades:
        print("[error] 검증할 매수 신호가 없습니다.", file=sys.stderr)
        return 1

    trades.sort(key=lambda t: t["date"])
    wins = sum(1 for t in trades if t["outcome"] == "target")
    losses = sum(1 for t in trades if t["outcome"] == "stop")
    times = len(trades) - wins - losses
    avg_ret = sum(t["return_pct"] for t in trades) / len(trades)
    avg_days = sum(t["days"] for t in trades) / len(trades)

    # 검증 구간의 시장 상황(코스피) — 결과 해석에 함께 보여준다
    market_ctx = None
    try:
        from naver import fetch_daily  # 지연 임포트(오프라인 실행 대비)
        idx = fetch_daily("KOSPI", 200)["rows"]
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
        "timeouts": times,
        "win_rate": round(wins / len(trades) * 100, 1),
        "loss_rate": round(losses / len(trades) * 100, 1),
        "avg_return": round(avg_ret, 2),
        "avg_days": round(avg_days, 1),
        "best": max(trades, key=lambda t: t["return_pct"])["return_pct"],
        "worst": min(trades, key=lambda t: t["return_pct"])["return_pct"],
    }

    payload = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone(__import__("datetime").timedelta(hours=9))
        ).isoformat(timespec="seconds"),
        "period": f"{trades[0]['date']} ~ {trades[-1]['date']}",
        "rule": f"매수 신호 진입, 목표 +8% / 손절 -4% / 최대 {HOLD_DAYS}거래일 보유, 수수료·매도세 반영",
        "summary": summary,
        "market_context": market_ctx,
        "note": "과거 일봉으로 같은 규칙을 되돌려 본 결과예요. 미래 수익을 보장하지 않습니다.",
        "trades": trades,
    }
    with open(os.path.join(DATA, "backtest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"검증 구간 {payload['period']} · 매수 신호 {summary['trades']}건")
    print(f"목표 도달 {wins} / 손절 {losses} / 기간 종료 {times} → 승률 {summary['win_rate']}%")
    print(f"평균 수익률 {summary['avg_return']}% · 평균 보유 {summary['avg_days']}일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
