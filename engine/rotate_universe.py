#!/usr/bin/env python3
"""국장 매매 타이밍 PWA — 주 1회(금요일 장후) 분석 유니버스 자동 교체 엔진.

무엇을 하는가
  1) 네이버 금융 시가총액 목록(코스피·코스닥)을 받아 스팩·ETF·ETN·우선주·리츠/펀드·
     거래정지(거래대금 없음) 종목을 걸러낸다.
  2) 남은 종목을 시총 4개 버킷(코스피 대형/중소형, 코스닥 대형/중소형)으로 나누고
     최근 20거래일 평균 거래대금(유동성) 순으로 줄을 세운다. 65거래일 미만(신규상장)과
     일봉이 끊긴 종목은 여기서 다시 걸러진다.
  3) universe.json 의 "pinned": true 종목(보유·수동 지정)은 무조건 유지하고, 버킷별 정원에서
     고정 종목을 뺀 나머지 슬롯만 유동성 상위 종목으로 채운다.
  4) --dry-run 이면 파일을 건드리지 않고 편입/편출 예상만 출력한다.

실행
    python3 engine/rotate_universe.py --dry-run        # 이번 주에 교체하면 어떻게 되는지 확인
    python3 engine/rotate_universe.py                  # 실제 교체 → engine/universe.json 갱신
    python3 engine/rotate_universe.py --init-pins --pin-code 005930 --pin-code 000660
                                                       # 구성은 그대로 두고 pinned 플래그만 정리

선정 기준
    - 버킷: 대형주 = 시총 4조원 이상, 중소형주 = 시총 3,000억~4조원
    - 순위: 최근 20거래일 평균 거래대금(종가×거래량) — 하루짜리 급등 거래량에 휘둘리지 않게
    - 정원(기본 35): 코스피 대형 12 · 코스피 중소형 9 · 코스닥 대형 5 · 코스닥 중소형 9
      (직전 35종목 구성의 시장·규모 배분과 중소형 비중 약 50%를 유지하면서 코스닥 대형
       주도주 슬롯을 2→5로 늘린 값. BUCKET_QUOTA 상수로 조정한다)
    - 유지 우선: 지금 들어 있는 종목은 정원의 1.5배 순위 안이면 그대로 두고, 새 종목은
      정원 안(상위 N위)에 들 때만 넣는다. 매주 35종목이 통째로 갈리는 것을 막는 장치다.

한계
    - 네이버 공개 데이터에는 '관리종목' 지정 여부가 없다. 거래정지·거래대금 0 필터와
      시총 3,000억 하한·유동성 상위 선정으로 사실상 걸러지지만, 관리종목을 확실히 빼려면
      아래 EXCLUDE_FILE(engine/exclude.local.json)에 코드를 적어 두면 된다.
    - universe.json 은 공개 저장소에 올라가므로 이 파일에는 보유 수량·평단가를 절대 쓰지 않는다.
      pinned 플래그만 남기고, 정렬은 버킷·유동성 순이라 고정 종목이 한 덩어리로 드러나지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import naver  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
UNIVERSE_PATH = os.path.join(ENGINE, "universe.json")
ROTATION_PATH = os.path.join(DATA, "universe_rotation.json")
EXCLUDE_FILE = os.path.join(ENGINE, "exclude.local.json")

TOTAL_TARGET = 35
BUCKETS = ("KOSPI_L", "KOSPI_M", "KOSDAQ_L", "KOSDAQ_M")
BUCKET_LABEL = {
    "KOSPI_L": "코스피 대형(4조원↑)",
    "KOSPI_M": "코스피 중소형(3,000억~4조원)",
    "KOSDAQ_L": "코스닥 대형(4조원↑)",
    "KOSDAQ_M": "코스닥 중소형(3,000억~4조원)",
}
BUCKET_QUOTA = {"KOSPI_L": 12, "KOSPI_M": 9, "KOSDAQ_L": 5, "KOSDAQ_M": 9}

LARGE_MIN_EOK = 40_000      # 4조원
MID_MIN_EOK = 3_000         # 3,000억원
MIN_HISTORY_DAYS = 65       # 신규상장(65거래일 미만) 제외
LIQ_WINDOW = 20             # 유동성 평균 구간(거래일)
KEEP_BAND = 1.5             # 기존 종목은 정원의 1.5배 순위 안이면 유지(주간 교체가 통째로 갈리지 않게)
ENTRY_BAND = 1.0            # 신규 편입은 정원 안(상위 need)에 드는 종목만
VERIFY_ROWS = 90
SHORTLIST_FACTOR = 2        # 버킷 정원의 2배까지 후보를 검증
CACHE_MAX_GAP_DAYS = 6      # data/raw 캐시가 이보다 오래되면 네이버에서 새로 받는다

SPAC_KEYWORDS = ("스팩", "SPAC")
FUND_KEYWORDS = ("리츠", "REITs", "인프라", "선박투자")
FUND_SUFFIX = ("인프라",)
ETF_KEYWORDS = (
    "KODEX", "TIGER", "ARIRANG", "KBSTAR", "HANARO", "KOSEF", "TIMEFOLIO", "TREX",
    "ETN", "레버리지", "인버스", "ETF",
)
PREFERRED_NAME_RE = re.compile(r"(우|우B|우C|[1-3]우B?)$")


# ----------------------------------------------------------------------------- helpers
def eok(value: float) -> str:
    return f"{value:,.0f}억"


def load_universe() -> dict:
    with open(UNIVERSE_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_exclude_codes() -> set[str]:
    """관리종목·투자주의 등 손으로 빼고 싶은 종목 코드(선택)."""
    if not os.path.exists(EXCLUDE_FILE):
        return set()
    try:
        with open(EXCLUDE_FILE, encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError):
        return set()
    if isinstance(spec, list):
        return {str(x) for x in spec}
    return {str(x) for x in spec.get("codes", [])}


def market_snapshot(min_mv_eok: int = MID_MIN_EOK) -> dict[str, dict]:
    """코스피·코스닥 시총 목록을 시총 하한까지 내려가며 모은다(코드 → 종목 정보)."""
    rows: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        last_mv = None
        for page in range(1, 15):
            batch = naver.fetch_market_list(market, size=100, pages=1, start_page=page)
            if not batch:
                break
            for s in batch:
                rows.setdefault(s["code"], s)
            last_mv = batch[-1]["market_cap_eok"]
            if last_mv and last_mv < min_mv_eok:
                break
            time.sleep(0.1)
        print(f"[목록] {market} {page}페이지 · 시총 하한 {eok(last_mv or 0)}까지 수집", file=sys.stderr)
    return rows


def is_preferred(row: dict) -> bool:
    name, code = row["name"], row["code"]
    if PREFERRED_NAME_RE.search(name):
        return True
    # 구형 우선주는 6자리 코드 끝이 5/7/9
    return len(code) == 6 and code[:-1].isdigit() and code[-1] in "579"


def screen(row: dict) -> tuple[bool, str]:
    """기본 자격 심사: 스팩·ETF/ETN·우선주·리츠·거래정지 제외."""
    name = row["name"]
    upper = name.upper()
    if row.get("stock_end_type", "stock") != "stock":
        return False, "ETF/ETN"
    if any(k in name for k in SPAC_KEYWORDS):
        return False, "스팩"
    if any(k in upper for k in ETF_KEYWORDS):
        return False, "ETF/ETN"
    if is_preferred(row):
        return False, "우선주"
    if any(k in name for k in FUND_KEYWORDS) or name.endswith(FUND_SUFFIX):
        return False, "리츠/펀드"
    if row.get("trade_stop") and row["trade_stop"] != "TRADING":
        return False, f"거래정지({row['trade_stop']})"
    if row.get("tradable_status") and row["tradable_status"] != "tradable":
        return False, "거래불가"
    if row.get("trading_value_eok", 0) <= 0:
        return False, "거래대금 없음"
    if row.get("close", 0) <= 0:
        return False, "시세 없음"
    return True, ""


def classify(market: str, market_cap_eok: float) -> str | None:
    if market_cap_eok >= LARGE_MIN_EOK:
        return f"{market}_L"
    if market_cap_eok >= MID_MIN_EOK:
        return f"{market}_M"
    return None


def bucket_of(code: str, universe_stock: dict | None, snapshot: dict[str, dict]) -> str:
    """종목의 버킷. 시총 미달·정보 없음이면 해당 시장의 중소형 버킷으로 본다."""
    row = snapshot.get(code)
    if row:
        got = classify(row["market"], row["market_cap_eok"])
        if got:
            return got
        return f"{row['market']}_M"
    if universe_stock and universe_stock.get("bucket") in BUCKETS:
        return universe_stock["bucket"]
    market = (universe_stock or {}).get("market", "KOSPI")
    return f"{market if market in ('KOSPI', 'KOSDAQ') else 'KOSPI'}_M"


def market_date_ref() -> str:
    """기준 거래일(코스피 지수 마지막 일봉). 실패하면 오늘 날짜."""
    try:
        rows = naver.fetch_daily("KOSPI", 10)["rows"]
        if rows:
            return rows[-1]["date"]
    except Exception as exc:  # 참고용 값일 뿐이라 실패해도 진행
        print(f"[warn] 기준일 조회 실패: {exc}", file=sys.stderr)
    return naver.now_kst().strftime("%Y-%m-%d")


def raw_rows(code: str) -> list[dict] | None:
    path = os.path.join(RAW, f"{code}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("rows")
    except (OSError, ValueError):
        return None


def activity(code: str, ref_date: str) -> dict | None:
    """최근 20거래일 평균 거래대금(억원)과 이력 길이. 데이터가 모자라면 None."""
    rows = raw_rows(code)
    stale = True
    if rows:
        try:
            gap = (datetime.strptime(ref_date, "%Y-%m-%d")
                   - datetime.strptime(rows[-1]["date"], "%Y-%m-%d")).days
            stale = gap > CACHE_MAX_GAP_DAYS
        except ValueError:
            stale = True
    source = "cache"
    if not rows or stale:
        try:
            rows = naver.fetch_daily(code, VERIFY_ROWS)["rows"]
            source = "naver"
            time.sleep(0.12)
        except Exception as exc:
            print(f"[warn] {code} 일봉 조회 실패: {exc}", file=sys.stderr)
            rows = rows or None
    if not rows or len(rows) < MIN_HISTORY_DAYS:
        return None
    window = rows[-LIQ_WINDOW:]
    liq = sum(r["close"] * r["volume"] for r in window) / len(window) / 1e8
    return {
        "liq20_eok": round(liq, 1),
        "history_days": len(rows),
        "last_date": rows[-1]["date"],
        "source": source,
    }


# ----------------------------------------------------------------------------- plan
def select_bucket(ranked: list[dict], need: int, current_codes: set[str], used: set[str]) -> list[dict]:
    """한 버킷의 교체 슬롯(need)을 채운다.

    - 지금 유니버스에 있는 종목은 정원의 KEEP_BAND(1.5)배 순위 안이면 유지한다.
    - 새 종목은 정원 안(상위 need)에 드는 경우에만 편입한다.
    - 그래도 자리가 남으면 순위대로 채운다.
    """
    if need <= 0:
        return []
    ranked = [r for r in ranked if r["code"] not in used]
    keep_limit = max(1, math.ceil(need * KEEP_BAND))
    entry_limit = max(1, math.ceil(need * ENTRY_BAND))
    keepers = [r for r in ranked[:keep_limit] if r["code"] in current_codes]
    entrants = [r for r in ranked[:entry_limit] if r["code"] not in current_codes]
    picked: list[dict] = []
    taken: set[str] = set()
    for row in keepers + entrants:
        if len(picked) >= need:
            break
        if row["code"] in taken:
            continue
        picked.append(row)
        taken.add(row["code"])
    for row in ranked:
        if len(picked) >= need:
            break
        if row["code"] in taken:
            continue
        picked.append(row)
        taken.add(row["code"])
    return picked


def build_plan(snapshot: dict[str, dict], universe: dict, total: int, args) -> dict:
    stocks = universe.get("stocks", [])
    pinned = {s["code"] for s in stocks if s.get("pinned")}
    pinned |= {str(c) for c in (args.pin_code or [])}
    excluded = load_exclude_codes()
    by_code = {s["code"]: s for s in stocks}
    ref_date = args.ref_date

    pool: dict[str, list[dict]] = {b: [] for b in BUCKETS}
    skipped: list[dict] = []
    for code, row in snapshot.items():
        if code in excluded:
            skipped.append({"code": code, "name": row["name"], "reason": "수동 제외 목록"})
            continue
        ok, why = screen(row)
        if not ok:
            if row["market_cap_eok"] >= MID_MIN_EOK:  # 하한 미달 종목은 목록만 방대해져 기록 생략
                skipped.append({"code": code, "name": row["name"], "reason": why})
            continue
        bucket = classify(row["market"], row["market_cap_eok"])
        if not bucket:
            continue
        pool[bucket].append(dict(row, bucket=bucket))

    quota = {b: max(0, int(args.quota.get(b, BUCKET_QUOTA[b]))) for b in BUCKETS}
    ratio = total / sum(quota.values()) if sum(quota.values()) else 1
    if abs(ratio - 1) > 1e-9:  # --total 로 정원을 바꾸면 비율대로 다시 나눈다
        quota = {b: max(0, round(q * ratio)) for b, q in quota.items()}
        diff = total - sum(quota.values())
        if diff:
            quota["KOSPI_L"] += diff

    pins_by_bucket = {b: set() for b in BUCKETS}
    for s in stocks:
        if s["code"] in pinned:
            pins_by_bucket[bucket_of(s["code"], s, snapshot)].add(s["code"])
    for code in pinned - set(by_code):
        pins_by_bucket[bucket_of(code, None, snapshot)].add(code)

    # 후보 검증: 일봉 이력(신규상장/거래중단) + 20일 평균 거래대금
    print(f"\n[검증] 후보 유동성 계산 (기준일 {ref_date})", file=sys.stderr)
    verified: dict[str, list[dict]] = {}
    for b in BUCKETS:
        cands = sorted(pool[b], key=lambda r: -r["trading_value_eok"])
        need_n = max(quota[b] * SHORTLIST_FACTOR, quota[b] + 8)
        short = cands[:need_n]
        seen = {r["code"] for r in short}
        # 지금 유니버스에 있는 종목은 순위 밖이어도 같은 잣대로 비교해 편출 여부를 판단한다
        for s in stocks:
            if s["code"] in pinned or s["code"] in seen:
                continue
            if bucket_of(s["code"], s, snapshot) != b:
                continue
            row = snapshot.get(s["code"])
            if row:
                short.append(dict(row, bucket=b))
                seen.add(s["code"])
        checked = []
        for i, row in enumerate(short, 1):
            act = activity(row["code"], ref_date)
            if not act:
                skipped.append({"code": row["code"], "name": row["name"], "reason": "일봉 65거래일 미만/데이터 없음"})
                continue
            if act["last_date"] < (datetime.strptime(ref_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d"):
                skipped.append({"code": row["code"], "name": row["name"], "reason": f"일봉 최근일 {act['last_date']}"})
                continue
            checked.append(dict(row, **act))
            if i % 10 == 0:
                print(f"  {BUCKET_LABEL[b]} {i}/{len(short)}", file=sys.stderr)
        verified[b] = sorted(checked, key=lambda r: -r["liq20_eok"])

    # 버킷별 선발: 고정 종목은 유지, 나머지 정원은 유동성 순(기존 종목 유지 우선)
    current_codes = {s["code"] for s in stocks}
    chosen: dict[str, list[dict]] = {}
    used: set[str] = set(pinned)
    for b in BUCKETS:
        need = max(0, quota[b] - len(pins_by_bucket[b]))
        picks = select_bucket(verified[b], need, current_codes, used)
        chosen[b] = picks
        used |= {r["code"] for r in picks}

    # 정원을 채우지 못한 버킷이 있으면 남는 후보로 순환하며 채운다
    deficit = total - (len(pinned) + sum(len(v) for v in chosen.values()))
    if deficit > 0:
        cursor = {b: 0 for b in BUCKETS}
        progress = True
        while deficit > 0 and progress:
            progress = False
            for b in BUCKETS:
                while cursor[b] < len(verified[b]):
                    row = verified[b][cursor[b]]
                    cursor[b] += 1
                    if row["code"] in used:
                        continue
                    chosen[b].append(row)
                    used.add(row["code"])
                    deficit -= 1
                    progress = True
                    break

    # 최종 목록: 버킷 순 → 버킷 안은 유동성 순(고정 종목도 같은 줄에 섞는다)
    final: list[dict] = []
    for b in BUCKETS:
        entries = []
        for code in pins_by_bucket[b]:
            src = by_code.get(code, {})
            act = activity(code, ref_date) or {}
            row = snapshot.get(code) or {}
            entries.append({
                "code": code,
                "name": src.get("name") or row.get("name", code),
                "market": row.get("market") or src.get("market", b.split("_")[0]),
                "sector": src.get("sector", ""),
                "bucket": b,
                "pinned": True,
                "liq20_eok": act.get("liq20_eok", src.get("liq20_eok", 0)),
            })
        for r in chosen[b]:
            src = by_code.get(r["code"], {})
            entries.append({
                "code": r["code"],
                "name": src.get("name") or r["name"],
                "market": r["market"],
                "sector": src.get("sector", ""),
                "bucket": b,
                "pinned": False,
                "liq20_eok": r["liq20_eok"],
            })
        entries.sort(key=lambda s: (-s["liq20_eok"], s["code"]))
        final.extend(entries)

    before = {s["code"]: s for s in stocks}
    after = {s["code"]: s for s in final}
    added = [after[c] for c in after if c not in before]
    removed = []
    for c, s in before.items():
        if c in after:
            continue
        item = dict(s)
        item["bucket"] = s.get("bucket") or bucket_of(c, s, snapshot)
        removed.append(item)
    kept_pins = [after[c] for c in after if c in before and after[c]["pinned"]]
    return {
        "ref_date": ref_date,
        "quota": quota,
        "total": len(final),
        "stocks": final,
        "added": added,
        "removed": removed,
        "kept_pinned": kept_pins,
        "skipped": skipped,
        "pool_sizes": {b: len(pool[b]) for b in BUCKETS},
        "verified_sizes": {b: len(verified[b]) for b in BUCKETS},
    }


# ----------------------------------------------------------------------------- output
def universe_payload(plan: dict, dry_run: bool) -> dict:
    now = naver.now_kst()
    return {
        "note": (
            "분석 대상 종목 = engine/rotate_universe.py 가 금요일 장후 주 1회 자동 교체하는 목록"
            " (코스피·코스닥 시총 3,000억원 이상, 스팩·ETF·ETN·우선주·리츠·거래정지 제외,"
            " 최근 20거래일 평균 거래대금 순). pinned=true 는 자동 교체에서 제외하는 고정 종목이다."
            " 실제 보유 수량·평균단가는 이 파일에 없다."
        ),
        "updated_at": now.strftime("%Y-%m-%d"),
        "rotation": {
            "run_at": now.isoformat(timespec="seconds"),
            "ref_date": plan["ref_date"],
            "total": plan["total"],
            "bucket_quota": plan["quota"],
            "added": [s["code"] for s in plan["added"]],
            "removed": [s["code"] for s in plan["removed"]],
            "dry_run": dry_run,
        },
        "stocks": plan["stocks"],
    }


def write_universe(payload: dict) -> None:
    """한 종목 = 한 줄. 손으로 읽고 diff 보기 좋게 기존 형식을 유지한다."""
    head = {k: v for k, v in payload.items() if k != "stocks"}
    body = json.dumps(head, ensure_ascii=False, indent=2).rstrip()
    assert body.endswith("}")
    lines = [body[:-1].rstrip() + ",", '  "stocks": [']
    stocks = payload["stocks"]
    for i, s in enumerate(stocks):
        lines.append("    " + json.dumps(s, ensure_ascii=False) + ("," if i < len(stocks) - 1 else ""))
    lines += ["  ]", "}"]
    with open(UNIVERSE_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def print_report(plan: dict, dry_run: bool, init: bool = False) -> None:
    tag = "[구성 유지·고정 정리]" if init else ("[드라이런]" if dry_run else "[교체 완료]")
    print(f"\n{tag} 기준일 {plan['ref_date']} · 최종 {plan['total']}종목 "
          f"(고정 {len(plan['kept_pinned'])} 유지)")
    print("정원: " + " · ".join(f"{BUCKET_LABEL[b].split('(')[0]} {plan['quota'][b]}" for b in BUCKETS))
    if not init:
        print("후보: " + " · ".join(f"{b} {plan['pool_sizes'][b]}→검증 {plan['verified_sizes'][b]}" for b in BUCKETS))
    print(f"\n편입 {len(plan['added'])} · 편출 {len(plan['removed'])}")
    for s in plan["added"]:
        print(f"  + {s['code']} {s['name']:<16} {s['bucket']:<9} 20일 평균 거래대금 {eok(s['liq20_eok'])}")
    for s in plan["removed"]:
        print(f"  - {s['code']} {s['name']:<16} {s.get('bucket', '')}")
    if not plan["added"] and not plan["removed"]:
        print("  (변동 없음)")
    print("\n최종 구성")
    for b in BUCKETS:
        rows = [s for s in plan["stocks"] if s["bucket"] == b]
        print(f"  {BUCKET_LABEL[b]} {len(rows)}종목")
        for s in rows:
            mark = "🔒" if s["pinned"] else "  "
            print(f"    {mark} {s['code']} {s['name']:<16} {eok(s['liq20_eok']):>10}")


def rotation_record(plan: dict, dry_run: bool, mode: str = "rotate") -> dict:
    return {
        "run_at": naver.now_kst().isoformat(timespec="seconds"),
        "mode": mode,
        "ref_date": plan["ref_date"],
        "dry_run": dry_run,
        "total": plan["total"],
        "bucket_quota": plan["quota"],
        "added": [{k: s[k] for k in ("code", "name", "market", "bucket", "liq20_eok")} for s in plan["added"]],
        "removed": [{k: s.get(k, "") for k in ("code", "name", "market", "bucket")} for s in plan["removed"]],
        "pinned": [s["code"] for s in plan["kept_pinned"]],
        "skipped": plan["skipped"][:80],
    }


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="분석 유니버스 주간 자동 교체")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 편입/편출 예상만 출력")
    ap.add_argument("--init-pins", action="store_true",
                    help="교체 없이 구성은 그대로 두고 pinned 플래그·버킷·유동성만 정리")
    ap.add_argument("--pin-code", action="append", default=None, help="고정할 종목 코드(반복 가능)")
    ap.add_argument("--total", type=int, default=TOTAL_TARGET, help=f"목표 종목 수(기본 {TOTAL_TARGET})")
    ap.add_argument("--quota", action="append", default=None, metavar="BUCKET=N",
                    help="버킷 정원 직접 지정(예: KOSDAQ_L=7). 반복 가능")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로도 출력")
    args = ap.parse_args()

    if args.quota:
        args.quota = {**BUCKET_QUOTA, **{k: int(v) for k, v in (x.split("=") for x in args.quota)}}
    else:
        args.quota = dict(BUCKET_QUOTA)
    args.ref_date = market_date_ref()

    universe = load_universe()
    snapshot = market_snapshot()

    if args.init_pins:
        plan = init_pins_plan(universe, snapshot, args)
    else:
        plan = build_plan(snapshot, universe, args.total, args)

    print_report(plan, args.dry_run, init=args.init_pins)
    mode = "init-pins" if args.init_pins else "rotate"
    if args.json:
        print(json.dumps(rotation_record(plan, args.dry_run, mode), ensure_ascii=False, indent=1))

    if args.dry_run:
        print("\n(--dry-run: engine/universe.json 은 그대로 두었습니다)")
        return 0

    payload = universe_payload(plan, dry_run=False)
    write_universe(payload)
    os.makedirs(DATA, exist_ok=True)
    with open(ROTATION_PATH, "w", encoding="utf-8") as f:
        json.dump(rotation_record(plan, False, mode), f, ensure_ascii=False, indent=1)
    print(f"\n저장: engine/universe.json ({plan['total']}종목) · data/universe_rotation.json")
    return 0


def init_pins_plan(universe: dict, snapshot: dict[str, dict], args) -> dict:
    """구성(종목)은 그대로 두고 pinned·버킷·유동성만 다시 계산해 정리한다."""
    stocks = universe.get("stocks", [])
    pins = {s["code"] for s in stocks if s.get("pinned")} | {str(c) for c in (args.pin_code or [])}
    final = []
    for s in stocks:
        act = activity(s["code"], args.ref_date) or {}
        row = snapshot.get(s["code"]) or {}
        final.append({
            "code": s["code"],
            "name": s.get("name") or row.get("name", s["code"]),
            "market": row.get("market") or s.get("market", "KOSPI"),
            "sector": s.get("sector", ""),
            "bucket": bucket_of(s["code"], s, snapshot),
            "pinned": s["code"] in pins,
            "liq20_eok": act.get("liq20_eok", s.get("liq20_eok", 0)),
        })
    order = {b: i for i, b in enumerate(BUCKETS)}
    final.sort(key=lambda s: (order.get(s["bucket"], 99), -s["liq20_eok"], s["code"]))
    before = {s["code"]: s for s in stocks}
    after = {s["code"]: s for s in final}
    return {
        "ref_date": args.ref_date,
        "quota": args.quota,
        "total": len(final),
        "stocks": final,
        "added": [after[c] for c in after if c not in before],
        "removed": [before[c] for c in before if c not in after],
        "kept_pinned": [s for s in final if s["pinned"]],
        "skipped": [],
        "pool_sizes": {b: 0 for b in BUCKETS},
        "verified_sizes": {b: len([s for s in final if s["bucket"] == b]) for b in BUCKETS},
    }


if __name__ == "__main__":
    sys.exit(main())
