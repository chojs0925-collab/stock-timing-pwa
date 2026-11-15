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


def fetch_market_list(market: str, size: int = 100, pages: int = 1) -> list[dict]:
    """시가총액 상위 종목 목록 (코드/이름/현재가/등락률/시총)."""
    out = []
    for page in range(1, pages + 1):
        raw = _get(LIST_URL.format(market=market, page=page, size=size))
        data = json.loads(raw.decode("utf-8"))
        for s in data.get("stocks", []):
            try:
                close = int(str(s.get("closePrice", "0")).replace(",", ""))
            except ValueError:
                continue
            out.append(
                {
                    "code": s.get("itemCode", ""),
                    "name": s.get("stockName", ""),
                    "market": "KOSPI" if market.upper() == "KOSPI" else "KOSDAQ",
                    "close": close,
                    "change_pct": float(s.get("fluctuationsRatio") or 0),
                    "market_cap_eok": int(float(str(s.get("marketValue") or "0").replace(",", ""))),
                    "volume": int(str(s.get("accumulatedTradingVolume") or "0").replace(",", "")),
                }
            )
        time.sleep(0.15)
    return out


def now_kst() -> datetime:
    return datetime.now(KST)
