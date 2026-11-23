#!/usr/bin/env python3
"""지표·호가단위·비용 계산 단위 테스트. 실행: python3 engine/test_indicators.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import (  # noqa: E402
    BREAKEVEN_TRIGGER_PCT, DEEP_REBOUND_RET5_MAX, REBOUND_RET5_MAX, REBOUND_RSI_MAX,
    STOP_MAX_PCT, STOP_MIN_PCT, TARGET_RR, atr, ceil_to_tick,
    dynamic_stop_pct, evaluate, floor_to_tick, order_prices, rsi, round_to_tick, sma,
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

# --- ATR(14) -----------------------------------------------------------
def bar(h, l, c, v=1000):
    return {"high": h, "low": l, "close": c, "volume": v}

# 고가-저가 20, 전일종가와의 차이 10씩 → True Range는 계속 20
flat = []
for i in range(16):
    flat.append(bar(110, 90, 100))
eq(atr(flat, 14), 20.0, "ATR: TR이 20으로 일정하면 20")
eq(atr(flat[:14], 14), None, "ATR: 15봉 미만이면 None")
eq(atr(flat[:15], 14), 20.0, "ATR: 딱 15봉이면 계산")

# 상승 갭: 둘째 봉부터 시가 갭으로 TR이 커진다 (단순평균 ATR 검산)
rows_atr = [bar(100, 98, 99), bar(112, 108, 110), bar(111, 109, 110), bar(115, 110, 114)]
eqa = atr(rows_atr, 3)  # TR: [max(4,13,9)=13, max(2,1,1)=2, max(5,5,0)=5] → (13+2+5)/3
if abs((eqa or 0) - 20 / 3) > 1e-9:
    failures.append(f"ATR(3) Wilder 검산: {eqa} != {20/3}")
    print(f"[FAIL] ATR(3) 검산: {eqa}")
else:
    print(f"[PASS] ATR(3) Wilder 검산: {round(eqa, 4)}")

# --- 변동성 기반 손절·목표 ---------------------------------------------
if not (STOP_MIN_PCT <= dynamic_stop_pct(10_000, 50) <= STOP_MIN_PCT + 1e-12):
    failures.append("저변동 손절은 하한 3.5%")
    print("[FAIL] 저변동 손절 하한")
else:
    print("[PASS] 저변동 손절은 하한 3.5%")
if not (STOP_MAX_PCT - 1e-12 <= dynamic_stop_pct(10_000, 1_000) <= STOP_MAX_PCT + 1e-12):
    failures.append("고변동 손절은 상한 7%")
    print("[FAIL] 고변동 손절 상한")
else:
    print("[PASS] 고변동 손절은 상한 7%")
if abs(dynamic_stop_pct(10_000, 300) - 0.045) > 1e-12:
    failures.append(f"ATR 1.5배 손절: {dynamic_stop_pct(10_000, 300)}")
    print("[FAIL] ATR 1.5배 손절")
else:
    print("[PASS] ATR 1.5배 손절: 4.5%")

p = order_prices(10_000, atr14=300)
eq((p["stop"], p["target"], p["basis"]), (9_550, 10_900, "atr"), "ATR 4.5% → 손절 9,550 / 목표 10,900")
p = order_prices(10_000, atr14=50)
eq((p["stop"], p["target"]), (9_650, 10_700), "저변동 ATR → 하한 3.5%/7.0%")
p = order_prices(10_000, atr14=1_000)
eq((p["stop"], p["target"]), (9_300, 11_400), "고변동 ATR → 상한 7.0%/14.0%")
p = order_prices(10_000, 0.08, 0.04, atr14=1_000)
eq((p["stop"], p["target"], p["basis"]), (9_600, 10_800, "fixed"), "비율을 직접 주면 고정 비율")
eq(round(TARGET_RR, 1), 2.0, "목표 = 손절 2배")
eq(BREAKEVEN_TRIGGER_PCT, 0.03, "본전 손절 트리거 +3%")

# evaluate() 가 ATR 동적 목표·손절을 쓰는지 (65봉 이상 필요)
series = []
for i in range(70):
    c = 10_000 + i * 20
    series.append(bar(c + 120, c - 120, c))
ev = evaluate(series)
eq(ev["basis"], "atr", "evaluate() 기본은 ATR 기준")
if not (ev["atr14"] and ev["atr_pct"] and ev["atr_pct"] > 0):
    failures.append(f"evaluate() atr14/atr_pct 누락: {ev.get('atr14')}, {ev.get('atr_pct')}")
    print("[FAIL] evaluate() atr14/atr_pct")
else:
    print(f"[PASS] evaluate() atr14={ev['atr14']} atr_pct={ev['atr_pct']}%")
if not (-7.3 <= ev["stop_pct"] <= -3.4 and 6.8 <= ev["target_pct"] <= 14.5):
    failures.append(f"evaluate() 동적 손절·목표 범위 이탈: {ev['stop_pct']} / {ev['target_pct']}")
    print("[FAIL] evaluate() 동적 손절·목표 범위")
else:
    print(f"[PASS] evaluate() 동적 손절·목표: {ev['stop_pct']}% / {ev['target_pct']}%")
if "변동성" not in ev["reason"]:
    failures.append("evaluate() 근거 문구에 변동성 언급 없음")
    print("[FAIL] evaluate() 근거 문구")
else:
    print("[PASS] evaluate() 근거 문구에 변동성 기준 포함")

# --- 5일 수익률(ret5) / 과매도 반등 관찰(rebound_watch) ------------------
def series(closes):
    """종가 리스트 → evaluate() 용 일봉(고가·저가 종가 ±0.5%)."""
    return [bar(c * 1.005, c * 0.995, c) for c in closes]


# 64봉 횡보 뒤 5봉 하락 → 5거래일 전(6번째 뒤) 종가 10,000 대비 정확히 -8%
eq(REBOUND_RET5_MAX, -8.0, "반등 관찰 5일 기준 -8.0%")
eq(REBOUND_RSI_MAX, 30.0, "반등 관찰 RSI 기준 30 미만")
eq(DEEP_REBOUND_RET5_MAX, -12.0, "깊은 과매도 -12.0%")

ev_flat = evaluate(series([10000] * 64 + [9900, 9800, 9600, 9400, 9200]))
eq(ev_flat["ret5"], -8.0, "ret5 계산(5일 -8.00%)")
eq(ev_flat["rebound_watch"], True, "ret5 -8.0 + RSI 0 → 관찰 대상")

# 경계 바로 위(+0.01%p)는 대상 아님
ev_up = evaluate(series([10000] * 64 + [9900, 9800, 9600, 9400, 9201]))
eq(ev_up["ret5"], -7.99, "ret5 -7.99%")
eq(ev_up["rebound_watch"], False, "ret5 -7.99% → 관찰 대상 아님")

# 상승 뒤 급락: ret5 -8.0% 이지만 RSI 30.0 → RSI는 30 미만이어야 하므로 대상 아님
ev_rsi30 = evaluate(series([8000 + i * 30 for i in range(60)] + [9614, 9457, 9301, 9145, 8988]))
eq(ev_rsi30["rsi14"], 30.0, "RSI 경계값 30.0")
eq(ev_rsi30["ret5"], -8.0, "RSI 경계 케이스 ret5")
eq(ev_rsi30["rebound_watch"], False, "RSI 30.0 → 관찰 대상 아님(30 미만)")

# 같은 상승 뒤 9% 하락: ret5 -9.0% + RSI 27.6 → 대상
ev_rsi27 = evaluate(series([8000 + i * 30 for i in range(60)] + [9594, 9418, 9242, 9067, 8891]))
eq(ev_rsi27["rsi14"], 27.6, "RSI 27.6")
eq(ev_rsi27["ret5"], -9.0, "RSI 27.6 케이스 ret5")
eq(ev_rsi27["rebound_watch"], True, "ret5 -9.0 + RSI 27.6 → 관찰 대상")

# 같은 ret5 -9.0% 에서 RSI 30.1 → 대상 아님 (RSI 경계 양쪽 확인)
ev_rsi301 = evaluate(series([8000 + i * 35 for i in range(60)] + [9884, 9703, 9521, 9340, 9159]))
eq(ev_rsi301["ret5"], -9.0, "RSI 30.1 케이스 ret5")
eq(ev_rsi301["rsi14"], 30.1, "RSI 30.1")
eq(ev_rsi301["rebound_watch"], False, "ret5 -9.0 + RSI 30.1 → 대상 아님")

# 깊은 과매도 경계(-12.0%)도 5일 기준과 같은 방식으로 계산
ev_deep = evaluate(series([10000] * 64 + [9800, 9600, 9400, 9200, 8800]))
eq(ev_deep["ret5"], -12.0, "ret5 -12.0%(깊은 과매도 경계)")
eq(ev_deep["rebound_watch"], True, "ret5 -12.0 → 관찰 대상")

# 절대 원칙: 반등 관찰 플래그는 매수 신호·주문가에 영향 없음
if ev_flat["signal"] == "BUY":
    failures.append("rebound_watch 가 🟢 매수로 승격됨")
    print("[FAIL] rebound_watch 매수 승격")
else:
    print(f"[PASS] rebound_watch 는 매수 승격 없음: signal={ev_flat['signal']} / score={ev_flat['score']}")
eq(ev_flat["basis"], "atr", "반등 관찰 종목도 주문가는 기존 ATR 기준 그대로")
# 플래그 유무와 무관하게 점수·신호가 같아야 한다(임계값만 바꿔 재계산해 확인)
import indicators as _ind  # noqa: E402
_prev = (_ind.REBOUND_RET5_MAX, _ind.REBOUND_RSI_MAX)
try:
    _ind.REBOUND_RET5_MAX, _ind.REBOUND_RSI_MAX = 1e9, 0.0  # 어떤 종목도 조건 미충족
    ev_off = evaluate(series([10000] * 64 + [9900, 9800, 9600, 9400, 9200]))
finally:
    _ind.REBOUND_RET5_MAX, _ind.REBOUND_RSI_MAX = _prev
eq((ev_off["rebound_watch"], ev_off["score"], ev_off["signal"], ev_off["entry"], ev_off["target"], ev_off["stop"]),
   (False, ev_flat["score"], ev_flat["signal"], ev_flat["entry"], ev_flat["target"], ev_flat["stop"]),
   "반등 조건 on/off 에도 점수·신호·주문가 동일")

print()
if failures:
    print(f"실패 {len(failures)}건")
    sys.exit(1)
print("전체 통과")
