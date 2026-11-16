"""네이버 금융 공개 엔드포인트에서 국장 일봉/종목 목록을 가져오는 모듈.

- 일봉: fchart.stock.naver.com/sise.nhn (XML, EUC-KR)
- 종목 목록: m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ} (JSON)
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
)

CHART_URL = "https://fchart.stock.naver.com/sise.nhn?symbol={symbol}&timeframe=day&count={count}&requestType=0"
LIST_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize={size}"

_ITEM_RE = re.compile(r'<item data="([^"]+)"')
_NAME_RE = re.compile(r'name="([^"]*)"')


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://finance.naver.com/",
            "Accept": "*/*",
        },
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # 재시도
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"네이버 요청 실패: {url} ({last_err})")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_daily(symbol: str, count: int = 160) -> dict:
    """일봉 OHLCV. [{date:'YYYY-MM-DD', open, high, low, close, volume}, ...] 오래된 순."""
    text = _decode(_get(CHART_URL.format(symbol=symbol, count=count)))
    m = _NAME_RE.search(text)
    name = m.group(1) if m else ""
    rows = []
    for payload in _ITEM_RE.findall(text):
        parts = payload.split("|")
        if len(parts) < 6:
            continue
        d, o, h, l, c, v = parts[:6]
        try:
            rows.append(
                {
                    "date": f"{d[0:4]}-{d[4:6]}-{d[6:8]}",
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                }
            )
        except ValueError:
            continue
    rows.sort(key=lambda r: r["date"])
    return {"symbol": symbol, "name": name, "rows": rows}


def _num(value, default: float = 0.0) -> float:
    """'1,234' / '-' / None 같은 네이버 표기 숫자를 float 로 바꾼다."""
    if value is None:
        return default
    text = str(value).replace(",", "").strip()
    if not text or text in ("-", "N/A"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def fetch_market_list(market: str, size: int = 100, pages: int = 1, start_page: int = 1) -> list[dict]:
    """시총 상위 종목 목록 (코드/이름/현재가/등락률/시총/거래대금/종목구분).

    - market_cap_eok, trading_value_eok: 둘 다 억원 단위로 맞춰서 돌려준다.
      네이버 모바일 응답은 marketValue 가 억원, accumulatedTradingValue 가 백만원이라 단위가 다르다.
      (예: 삼성전자 marketValue 11,499,630억 / accumulatedTradingValue 3,119,902백만원 = 3조1,199억)
    - stock_end_type: 'stock' | 'etf' | 'etn' ... (spac 은 'stock' 으로 오므로 이름으로 걸러야 한다)
    - trade_stop: 'TRADING' 이면 정상, 그 외는 거래정지/단일가 등
    """
    out = []
    base = "KOSPI" if market.upper() == "KOSPI" else "KOSDAQ"
    for page in range(start_page, start_page + pages):
        raw = _get(LIST_URL.format(market=market, page=page, size=size))
        data = json.loads(raw.decode("utf-8"))
        for s in data.get("stocks", []):
            close = int(_num(s.get("closePrice")))
            if not s.get("itemCode") or not s.get("stockName"):
                continue
            out.append(
                {
                    "code": s.get("itemCode", ""),
                    "name": s.get("stockName", ""),
                    "market": base,
                    "close": close,
                    "change_pct": float(_num(s.get("fluctuationsRatio"))),
                    "market_cap_eok": int(_num(s.get("marketValue"))),
                    "trading_value_eok": int(_num(s.get("accumulatedTradingValue")) / 100),
                    "volume": int(_num(s.get("accumulatedTradingVolume"))),
                    "stock_end_type": str(s.get("stockEndType") or ""),
                    "trade_stop": str((s.get("tradeStopType") or {}).get("name") or ""),
                    "tradable_status": str(s.get("tradableStatus") or ""),
                }
            )
        time.sleep(0.15)
    return out


def now_kst() -> datetime:
    return datetime.now(KST)
