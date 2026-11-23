"""지표 계산 · KRX 호가단위 · 신호등 점수 · 수수료/세금.

모든 함수는 표준 라이브러리만 사용한다.
"""
from __future__ import annotations

# ---------------------------------------------------------------- 기본 지표
def sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def atr(rows: list[dict], n: int = 14) -> float | None:
    """Wilder ATR(Average True Range). 일봉 high/low/close 사용.

    True Range = max(고가-저가, |고가-전일종가|, |저가-전일종가|)
    처음 n개의 단순평균으로 시작한 뒤 (이전 ATR×(n-1) + TR)/n 으로 평활한다.
    rows 는 오래된 순 정렬이어야 한다.
    """
    if len(rows) < n + 1:
        return None
    trs = []
    for i in range(1, len(rows)):
        high = rows[i].get("high", rows[i]["close"])
        low = rows[i].get("low", rows[i]["close"])
        prev_close = rows[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    value = sum(trs[:n]) / n
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder RSI."""
    if len(closes) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain, avg_loss = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
    if avg_loss == 0:
        # 모두 상승이면 100, 완전히 횡보(상승·하락 0)면 중립 50
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ------------------------------------------------------------ KRX 호가단위
# 2023-01-25 이후 KRX 호가단위(틱) 구간.
TICK_TABLE = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
]


def tick_size(price: float) -> int:
    for upper, tick in TICK_TABLE:
        if price < upper:
            return tick
    return 1_000


def round_to_tick(price: float) -> int:
    """가장 가까운 호가로 반올림."""
    if price <= 0:
        return 0
    tick = tick_size(price)
    return int((price + tick / 2) // tick * tick)


def floor_to_tick(price: float) -> int:
    if price <= 0:
        return 0
    tick = tick_size(price)
    return int(price // tick * tick)


def ceil_to_tick(price: float) -> int:
    if price <= 0:
        return 0
    tick = tick_size(price)
    return int(-(-price // tick) * tick)


# ------------------------------------------------- 변동성 기반 손절·목표
# 백테스트(scratch/bt-exp/REPORT.md)에서 고정 -4% 손절은 이 종목들의 14일 ATR(중앙 6.7%)의
# 0.6배에 불과해 하루 변동에도 걸렸다. 손절을 1.5×ATR(3.5~7% 클램프), 목표를 2배로 두면
# 평균 수익률이 개선됐다. 그 값을 여기 기본값으로 둔다.
STOP_ATR_MULT = 1.5
STOP_MIN_PCT = 0.035
STOP_MAX_PCT = 0.070
TARGET_RR = 2.0              # 목표 = 손절의 2배 (3.5~7% → 7~14%)
BREAKEVEN_TRIGGER_PCT = 0.03  # +3% 도달 시 손절가를 본전(진입가)으로 올린다
LEGACY_TARGET_PCT = 0.08
LEGACY_STOP_PCT = 0.04

# ------------------------------------------- 과매도 반등 관찰(참고 전용)
# 백테스트(scratch/bt-rebound/REPORT.md): 5일 -8% 이하 + RSI14 30 미만은
# 6년 1,110건에서 5일 평균 +2.0%, 승률 58%로 아무 날 매수(+0.32%)보다 나았다.
# 다만 6건 중 1건은 5일 뒤 -5% 이하로 더 빠졌고, ATR 손절·목표를 기계적으로 붙이면
# 기대값이 떨어졌다(+2.55% → +1.55%). 그래서 매수 신호(signal/score/order_prices)와
# 완전히 분리한 관찰 목록으로만 쓴다.
REBOUND_RET5_MAX = -8.0      # 5거래일 수익률(%)이 이 값 이하
REBOUND_RSI_MAX = 30.0       # RSI14가 이 값 미만
DEEP_REBOUND_RET5_MAX = -12.0  # UI에서 '깊은 과매도'로 강조(성과가 가장 좋았던 구간)


def dynamic_stop_pct(close: float, atr14: float | None,
                     mult: float = STOP_ATR_MULT,
                     floor_pct: float = STOP_MIN_PCT,
                     cap_pct: float = STOP_MAX_PCT) -> float:
    """1.5 × ATR14 / 종가 를 3.5%~7.0% 로 자른 손절 폭."""
    if not close or close <= 0 or not atr14 or atr14 <= 0:
        return LEGACY_STOP_PCT
    return max(floor_pct, min(cap_pct, mult * atr14 / close))


def order_prices(close: int, target_pct: float | None = None,
                 stop_pct: float | None = None, atr14: float | None = None) -> dict:
    """진입가·목표가·손절가를 KRX 호가에 맞춰 산출.

    - 진입가: 현재가 호가 정렬(그대로 두면 이미 호가 단위)
    - 목표가: 목표 % 를 호가로 올림 (지정가가 시장가보다 낮게 걸리지 않도록)
    - 손절가: 손절 % 를 호가로 내림 (손절은 조금 더 가까운 쪽이 안전)

    비율 결정:
    - atr14 를 주면 변동성 기준: 손절 = clamp(1.5×ATR14/종가, 3.5%, 7.0%), 목표 = 손절×2
    - 비율을 직접 주면 그 고정 비율(기존 동작 호환)
    - 아무것도 없으면 기존 기본값 +8% / -4%
    """
    basis = "atr" if (atr14 and stop_pct is None) else "fixed"
    if stop_pct is None:
        stop_pct = dynamic_stop_pct(close, atr14) if atr14 else LEGACY_STOP_PCT
    if target_pct is None:
        target_pct = TARGET_RR * stop_pct if basis == "atr" else LEGACY_TARGET_PCT

    entry = round_to_tick(close)
    if entry <= 0:
        entry = close
    target = ceil_to_tick(round(entry * (1 + target_pct), 6))
    stop = floor_to_tick(round(entry * (1 - stop_pct), 6))
    # 같은 호가가 되어버리는 경우 한 틱씩 벌린다.
    if target <= entry:
        target = ceil_to_tick(entry + tick_size(entry))
    if stop >= entry:
        stop = floor_to_tick(entry - tick_size(entry))
    return {
        "entry": entry,
        "target": target,
        "stop": stop,
        "target_pct": round((target / entry - 1) * 100, 2),
        "stop_pct": round((stop / entry - 1) * 100, 2),
        "basis": basis,
    }


# --------------------------------------------------------------- 비용 구조
# 2026-01-01 부터 코스피·코스닥 매도 시 증권거래세 0.05% + 농특세 0.15% = 0.20%
# (코스닥은 거래세 0.20%, 농특세 없음 — 합계 동일 0.20%)
TAX_RATE = {"KOSPI": 0.0020, "KOSDAQ": 0.0020}
# 삼성증권 종합(평생혜택 비대면) 계좌 기준: 유관기관 제비용 포함 약 0.00364%
DEFAULT_FEE_RATE = 0.0000364
FEE_PRESETS = {
    "benefit": {"label": "평생혜택 비대면", "rate": 0.0000364},
    "standard": {"label": "일반 온라인", "rate": 0.00015},
}


def trade_estimate(entry: int, target: int, stop: int, qty: int, market: str,
                   fee_rate: float = DEFAULT_FEE_RATE, tax_rate: float | None = None) -> dict:
    """수수료·세금 반영 예상 순수익 / 최대 손실 (매수·매도 왕복)."""
    tax = TAX_RATE.get(market, 0.002) if tax_rate is None else tax_rate
    buy_amount = entry * qty
    sell_amount = target * qty
    buy_fee = round(buy_amount * fee_rate)
    sell_fee = round(sell_amount * fee_rate)
    sell_tax = round(sell_amount * tax)
    gross = sell_amount - buy_amount
    net = gross - buy_fee - sell_fee - sell_tax

    stop_amount = stop * qty
    stop_fee = round(stop_amount * fee_rate)
    stop_tax = round(stop_amount * tax)
    loss = (buy_amount - stop_amount) + buy_fee + stop_fee + stop_tax

    return {
        "qty": qty,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
        "sell_tax": sell_tax,
        "gross_profit": gross,
        "net_profit": net,
        "net_profit_pct": round(net / buy_amount * 100, 2) if buy_amount else 0,
        "max_loss": loss,
        "max_loss_pct": round(loss / buy_amount * 100, 2) if buy_amount else 0,
        "cost_total": buy_fee + sell_fee + sell_tax,
        "fee_rate": fee_rate,
        "tax_rate": tax,
    }


# ------------------------------------------------------------------ 신호등
def _rsi_score(v: float) -> int:
    if 50 <= v < 65:
        return 25
    if 45 <= v < 50 or 65 <= v < 70:
        return 21
    if 38 <= v < 45 or 70 <= v < 75:
        return 15
    if 30 <= v < 38:
        return 10
    if v >= 75:
        return 6
    return 8  # 30 미만: 과매도지만 하락 추세일 수 있음


def _volume_score(v: float) -> int:
    if 1.0 <= v <= 2.5:
        return 20
    if 0.8 <= v < 1.0:
        return 16
    if 2.5 < v <= 4.0:
        return 13
    if 0.6 <= v < 0.8:
        return 11
    if v > 4.0:
        return 8
    return 6


def _pullback_score(dd: float) -> int:
    """dd = 최근 20일 고점 대비 현재가 (%). 음수."""
    if -8 <= dd <= -2:
        return 15  # 눌림목
    if -2 < dd <= 0.5:
        return 10  # 고점 근접(추격 주의)
    if -12 <= dd < -8:
        return 10
    if -20 <= dd < -12:
        return 5
    return 2


def evaluate(rows: list[dict], target_pct: float | None = None,
             stop_pct: float | None = None) -> dict | None:
    """일봉 리스트(오래된 순)를 받아 지표·점수·신호·주문가를 계산.

    target_pct/stop_pct 를 비워두면 14일 ATR 변동성 기준으로 손절·목표를 잡는다.
    숫자를 주면 그 고정 비율을 쓴다(기존 호출 호환).
    """
    if len(rows) < 65:
        return None
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    vols = [r["volume"] for r in rows]

    close = closes[-1]
    prev = closes[-2]
    # 5거래일 수익률(%): 6봉 미만이면 계산하지 않는다.
    # 표시값(소수 2자리)과 조건 판정이 어긋나지 않도록 같은 자리에서 반올림한 값을 쓴다.
    ret5 = None
    if len(rows) >= 6 and rows[-6]["close"]:
        ret5 = round(((close / rows[-6]["close"]) - 1) * 100, 2)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    ma20_prev = sma(closes[:-5], 20)  # 5거래일 전 20일선
    rsi14 = rsi(closes, 14)
    vol_avg20 = sma(vols[:-1], 20)
    vol_ratio = (vols[-1] / vol_avg20) if vol_avg20 else 0
    high20 = max(highs[-20:])
    drawdown = (close / high20 - 1) * 100

    # 20일선 기울기(5거래일, %)
    ma20_slope = ((ma20 / ma20_prev) - 1) * 100 if ma20_prev else 0

    score = 0
    score += 12 if close > ma20 else 0
    score += 13 if ma20 > ma60 else 0
    if ma20_slope > 0.3:
        score += 8
    elif ma20_slope > 0:
        score += 4
    score += 7 if close > ma60 else 0
    score += _rsi_score(rsi14)
    score += _volume_score(vol_ratio)
    score += _pullback_score(drawdown)

    notes = []
    # 하드 리스크 캡
    if close < ma60 and ma20 < ma60:
        score = min(score, 42)
        notes.append("역배열")
    if rsi14 >= 78:
        score = min(score, 68)
        notes.append("과열")
    if drawdown < -25:
        score = min(score, 50)
        notes.append("추세훼손")
    score = int(max(0, min(100, round(score))))

    if score >= 70:
        signal, label = "BUY", "🟢 매수"
    elif score >= 45:
        signal, label = "WATCH", "🟡 관망"
    else:
        signal, label = "SELL", "🔴 주의"

    # 과매도 반등 관찰 — 매수 신호가 아니라 '지켜볼 목록' 플래그다.
    # signal/picks/order_prices/score 는 이 값과 무관하게 그대로 계산된다.
    rsi14_disp = round(rsi14, 1) if rsi14 is not None else None
    rebound_watch = bool(
        ret5 is not None and rsi14_disp is not None
        and ret5 <= REBOUND_RET5_MAX and rsi14_disp < REBOUND_RSI_MAX
    )

    atr14 = atr(rows, 14)
    atr_pct = (atr14 / close * 100) if (atr14 and close) else None
    prices = order_prices(close, target_pct, stop_pct, atr14=atr14)
    reason = _reason(signal, close, ma20, ma60, ma20_slope, rsi14, vol_ratio, drawdown,
                     prices, atr_pct)

    return {
        "close": int(round(close)),
        "prev_close": int(round(prev)),
        "change_pct": round((close / prev - 1) * 100, 2) if prev else 0,
        "ma20": int(round(ma20)),
        "ma60": int(round(ma60)),
        "ma20_slope5": round(ma20_slope, 2),
        "rsi14": round(rsi14, 1),
        "ret5": ret5,
        "rebound_watch": rebound_watch,
        "vol_ratio": round(vol_ratio, 2),
        "drawdown_from_high20": round(drawdown, 2),
        "high20": int(high20),
        "score": score,
        "signal": signal,
        "signal_label": label,
        "reason": reason,
        "flags": notes,
        "atr14": round(atr14, 2) if atr14 else None,
        "atr_pct": round(atr_pct, 2) if atr_pct else None,
        "breakeven_trigger_pct": round(BREAKEVEN_TRIGGER_PCT * 100, 1),
        **prices,
        "history_days": len(rows),
    }


def _reason(signal, close, ma20, ma60, ma20_slope, rsi14, vol_ratio, drawdown,
            prices, atr_pct=None) -> str:
    """초보자용 한두 문장 근거."""
    trend_up = close > ma20 and ma20 > ma60
    trend_down = close < ma20 and ma20 < ma60
    parts = []

    if trend_up:
        parts.append("20일선 위에서 20일선이 60일선보다 높은 정배열이에요")
    elif trend_down:
        parts.append("20일선 아래로 내려왔고 20일선이 60일선보다 낮은 역배열이에요")
    elif close > ma20:
        parts.append(f"20일선({ma20:,.0f}원)은 지키고 있지만 60일선과의 배열은 아직 엇갈려요")
    else:
        parts.append(f"20일선({ma20:,.0f}원)을 밑돌고 있어요")

    if vol_ratio >= 2.5:
        parts.append(f"거래량이 20일 평균의 {vol_ratio:.1f}배로 크게 터져 변동성에 주의하세요")
    elif vol_ratio >= 1.0:
        parts.append(f"거래량이 20일 평균의 {vol_ratio:.1f}배로 관심이 붙었어요")
    else:
        parts.append(f"거래량은 20일 평균의 {vol_ratio:.1f}배로 아직 조용해요")

    if -8 <= drawdown <= -2:
        parts.append(f"최근 고점 대비 {abs(drawdown):.2f}% 눌린 자리라 분할 매수에 유리해요")
    elif drawdown > -2:
        parts.append("고점 부근이라 추격 매수는 피하고 눌림을 기다리세요")
    elif drawdown <= -12:
        parts.append(f"고점 대비 {abs(drawdown):.0f}% 빠진 상태라 반등 확인이 먼저예요")

    if rsi14 >= 75:
        parts.append(f"RSI {rsi14:.0f}로 단기 과열권이에요")
    elif rsi14 <= 30:
        parts.append(f"RSI {rsi14:.0f}로 과매도권이에요")

    body = ". ".join(parts[:3])
    stop_pct = prices["stop_pct"]
    target_pct = prices["target_pct"]
    vol_basis = f"14일 변동성(ATR {atr_pct:.1f}%)" if atr_pct else "변동성 기준"
    trigger = BREAKEVEN_TRIGGER_PCT * 100
    if signal == "BUY":
        return (body + f". 진입 {prices['entry']:,}원. {vol_basis} 기준 목표 +{target_pct:.1f}%"
                f"({prices['target']:,}원) · 손절 {stop_pct:.1f}%({prices['stop']:,}원)이고, "
                f"+{trigger:.0f}%에 닿으면 손절선을 본전(진입가)으로 옮기세요.")
    if signal == "WATCH":
        return (body + f". 지금은 추격보다 {prices['stop']:,}원 손절선({stop_pct:.1f}%)을 염두에 둔 "
                f"소액 분할이 안전해요. 손절·목표 폭은 {vol_basis}으로 정했어요.")
    return (". ".join(parts[:2])
            + f". 신규 매수는 피하고, 보유 중이라면 손절가({prices['stop']:,}원 · {vol_basis})를 지키세요.")
