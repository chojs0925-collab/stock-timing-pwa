#!/usr/bin/env python3
"""장중 실시간 현재가를 data/signals.json에 가볍게 반영한다.
- 목표·손절·점수 등 일봉 기준 값은 유지하고 close, change_pct, generated_at만 업데이트한다.
- holdings_seed는 항상 빈 상태로 유지한다.
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS_PATH = os.path.join(ROOT, "data", "signals.json")


def fetch_realtime(codes: list[str]) -> dict[str, tuple[int, float]]:
    live: dict[str, tuple[int, float]] = {}
    for i in range(0, len(codes), 30):
        chunk = codes[i : i + 30]
        url = "https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(chunk)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8"))
        for d in payload.get("datas", []):
            code = d.get("itemCode")
            p_str = str(d.get("closePrice", "0")).replace(",", "")
            try:
                price = int(p_str)
                rate = float(str(d.get("fluctuationsRatio", "0")).replace(",", ""))
            except ValueError:
                continue
            if code and price > 0:
                live[code] = (price, rate)
    return live


def main() -> None:
    with open(SIGNALS_PATH, encoding="utf-8") as f:
        sig = json.load(f)

    codes = sorted(
        {s["code"] for s in sig.get("stocks", [])}
        | {p["code"] for p in sig.get("picks", [])}
    )
    live = fetch_realtime(codes)
    if not live:
        raise RuntimeError("실시간 시세를 받아오지 못했습니다.")

    updated = 0
    for group in ("stocks", "picks"):
        for item in sig.get(group, []):
            c = item.get("code")
            if c in live:
                price, rate = live[c]
                item["close"] = price
                item["change_pct"] = rate
                updated += 1

    kst = datetime.timezone(datetime.timedelta(hours=9))
    sig["generated_at"] = datetime.datetime.now(kst).isoformat(timespec="seconds")
    sig["holdings_seed"] = {"holdings": []}

    tmp = SIGNALS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sig, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SIGNALS_PATH)
    print(f"updated {updated} entries at {sig['generated_at']}")


if __name__ == "__main__":
    main()
