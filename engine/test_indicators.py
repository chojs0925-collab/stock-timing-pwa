#!/usr/bin/env python3
"""지표·호가단위·비용 계산 단위 테스트. 실행: python3 engine/test_indicators.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import (  # noqa: E402
    ceil_to_tick, floor_to_tick, order_prices, rsi, round_to_tick, sma,
    tick_size, trade_estimate,
)

failures = []


def eq(actual, expected, label):
    if actual != expected:
        failures.append(f"{label}: {actual} != {expected}")
        print(f"[FAIL] {label}: {actual} != {expected}")
    else:
        print(f"[PASS] {label}: {actual}")


# --- KRX 호가단위 -------------------------------------------------------
eq(tick_size(1_999), 1, "1,999원 호가 1원")
eq(tick_size(2_000), 5, "2,000원 호가 5원")
eq(tick_size(4_999), 5, "4,999원 호가 5원")
eq(tick_size(5_000), 10, "5,000원 호가 10원")
eq(tick_size(19_999), 10, "19,999원 호가 10원")
eq(tick_size(20_000), 50, "20,000원 호가 50원")
eq(tick_size(49_999), 50, "49,999원 호가 50원")
eq(tick_size(50_000), 100, "50,000원 호가 100원")
eq(tick_size(199_999), 100, "199,999원 호가 100원")
eq(tick_size(200_000), 500, "200,000원 호가 500원")
eq(tick_size(499_999), 500, "499,999원 호가 500원")
eq(tick_size(500_000), 1_000, "500,000원 호가 1,000원")
eq(tick_size(1_233_000), 1_000, "1,233,000원 호가 1,000원")

eq(round_to_tick(47_577), 47_600, "반올림 47,577 → 47,600")
eq(floor_to_tick(51_354), 51_300, "내림 51,354 → 51,300 (호가 100원)")
eq(ceil_to_tick(51_354), 51_400, "올림 51,354 → 51,400")

# --- 진입·목표·손슬가 ---------------------------------------------------
p = order_prices(47_550)
eq((p["entry"], p["target"], p["stop"]), (47_550, 51_400, 45_600), "삼현 47,550 주문가")
p = order_prices(209_000)
eq((p["entry"], p["target"], p["stop"]), (209_000, 226_000, 200_500), "삼성전자 209,000 주문가")
p = order_prices(8_400)
eq((p["entry"], p["target"], p["stop"]), (8_400, 9_080, 8_060), "아시아나 8,400 주문가")
p = order_prices(1_827)
eq((p["entry"], p["target"], p["stop"]), (1_827, 1_974, 1_753), "SK증권 1,827 주문가(1원 단위)")
p = order_prices(2_265)
eq((p["entry"], p["target"], p["stop"]), (2_265, 2_450, 2_170), "스트라드비젼 2,265 주문가(5원 단위)")

# 목표/손슬이 진입가와 겹치지 않는지 (아주 싼 종목)
p = order_prices(1)
if not (p["stop"] < p["entry"] < p["target"]):
    failures.append(f"최저가 종목 주문가 순서 오류: {p}")
    print("[FAIL] 최저가 종목 주문가 순서", p)

# --- 수수료·세금 --------------------------------------------------------
t = trade_estimate(47_550, 51_400, 45_600, 42, "KOSDAQ", 0.0000364)
eq((t["net_profit"], t["max_loss"]), (157_230, 85_873), "삼현 42주 순수익/최대손실")
eq(t["sell_tax"], 4_318, "매도세 0.20% (코스닥)")
t2 = trade_estimate(47_550, 51_400, 45_600, 42, "KOSDAQ", 0.00015)
if t2["net_profit"] >= t["net_profit"]:
    failures.append("일반 수수료가 평생혜택보다 순수익이 크면 안 됨")
    print("[FAIL] 수수료 프리셋 비교")
else:
    print(f"[PASS] 수수료 프리셋 비교: 평생혜택 {t['net_profit']:,} > 일반 {t2['net_profit']:,}")

# --- RSI / SMA ---------------------------------------------------------
eq(round(rsi([100 + i for i in range(30)]), 1), 100.0, "계속 상승 RSI 100")
eq(round(rsi([100] * 30), 1), 50.0, "횡보 RSI 50")
eq(round(rsi([100 - i for i in range(30)]), 1), 0.0, "계속 하락 RSI 0")
eq(sma([1, 2, 3, 4, 5], 5), 3.0, "SMA(5)")
eq(sma([1, 2], 5), None, "SMA 데이터 부족 → None")

print()
if failures:
    print(f"실패 {len(failures)}건")
    sys.exit(1)
print("전체 통과")
