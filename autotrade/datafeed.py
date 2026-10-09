"""autotrade/datafeed.py — 20:10 KIS 당일 OHLC 수집

흐름:
  1. KIS에서 코스피200 지수 당일 시고저종 조회 → engine_v3/k200.csv 1행 추가
  2. KIS에서 유니버스(캐시 349 + ETF 2) get_quote 호출 → 당일 시고저종·거래량 수집
  3. engine_v3/stockdata/ohlcv.csv.gz 에 각 종목 1행씩 머지 (중복이면 교체)
  4. 수집 검증 (지시서 §5):
     - 결측률 >= 5% → 중단
     - 전일 대비 ±30% 초과 → 경고 (중단 안 함)

실행:
  python -m autotrade.datafeed            # 당일 데이터 수집·추가
  python -m autotrade.datafeed --check    # 수집 안 하고 현재 데이터 상태만 보고
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg
from kis.factory import KIS
from utils.throttler import RateThrottler
from config.settings import get_settings


def load_universe() -> list[tuple[str, str]]:
    """캐시 유니버스 + ETF 2개. (code, name) 리스트, 중복 제거."""
    out: dict[str, str] = {}
    for p in cfg.get("data.universe_sources", []):
        with open(cfg.abspath(p), "r", encoding="utf-8") as f:
            for s in json.load(f).get("stocks", []):
                code = s.get("code")
                name = s.get("name", code)
                if code and code not in out:
                    out[str(code).zfill(6)] = name
    etf_long = cfg.get("etf.long_code")
    etf_short = cfg.get("etf.short_code")
    out.setdefault(str(etf_long).zfill(6), cfg.get("etf.long_name"))
    out.setdefault(str(etf_short).zfill(6), cfg.get("etf.short_name"))
    return list(out.items())


def fetch_index_bar(kis: KIS, idx_code: str) -> Optional[dict]:
    """코스피200 지수 당일 OHLC 조회."""
    q = kis.market.get_index_quote(idx_code)
    if not q or q.get("close", 0) <= 0:
        return None
    return {
        "date":  common.today_str(),
        "open":  q["open"],
        "high":  q["high"],
        "low":   q["low"],
        "close": q["close"],
    }


def fetch_stock_bars(kis: KIS, universe: list[tuple[str, str]]) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """유니버스 각 종목의 당일 OHLC+거래량 수집.
    get_quote().price 는 당일 현재가 (장후 20:10이면 종가).
    Returns: (수집된 행 리스트, 실패 리스트)
    """
    throttler = RateThrottler(max_per_second=18)
    today = common.today_str()
    rows: list[dict] = []
    failed: list[tuple[str, str, str]] = []

    for idx, (code, name) in enumerate(universe, 1):
        try:
            with throttler:
                q = kis.market.get_quote(code)
            if q.price <= 0:
                failed.append((code, name, "price=0"))
                continue
            rows.append({
                "code":   str(code).zfill(6),
                "name":   name,
                "date":   today,
                "open":   int(q.open),
                "high":   int(q.high),
                "low":    int(q.low),
                "close":  int(q.price),  # 20:10 호출이면 price = 종가
                "volume": int(q.volume),
            })
            if idx % 50 == 0:
                logger.info(f"[datafeed] {idx}/{len(universe)} 수집 중...")
        except Exception as e:
            failed.append((code, name, str(e)[:100]))
    return rows, failed


def validate_rows(rows: list[dict], existing_df: pd.DataFrame, max_change_pct: float) -> list[str]:
    """결측 아닌 수집 결과에 대한 이상치 체크. 반환: 경고 메시지 목록."""
    warnings = []
    today = common.today_str()
    # 각 종목의 어제 종가 (가장 최근 날짜)
    last_closes: dict[str, int] = {}
    if not existing_df.empty:
        last = existing_df.sort_values("date").groupby("code").tail(1)
        for _, r in last.iterrows():
            last_closes[str(r["code"]).zfill(6)] = int(r["close"])

    for r in rows:
        code = r["code"]
        prev = last_closes.get(code)
        if prev and prev > 0:
            change = (r["close"] - prev) / prev * 100
            if abs(change) > max_change_pct:
                warnings.append(
                    f"[{code}] {r['name']} 전일대비 {change:+.1f}% "
                    f"({prev:,} → {r['close']:,}) — 수정주가·거래정지 확인 필요"
                )
    return warnings


def append_k200_csv(bar: dict) -> bool:
    """k200.csv 에 1행 추가. 같은 날짜 있으면 덮어씀."""
    p = cfg.abspath(cfg.get("data.k200_csv"))
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[df["date"] != bar["date"]]
    df = pd.concat([df, pd.DataFrame([bar])], ignore_index=True)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(p, index=False)
    return True


def append_ohlcv_gz(rows: list[dict]) -> int:
    """ohlcv.csv.gz 에 당일 행 머지. 같은 (code,date) 있으면 교체."""
    p = cfg.abspath(cfg.get("data.ohlcv_gz"))
    existing = pd.read_csv(p, dtype={"code": str})
    existing["code"] = existing["code"].astype(str).str.zfill(6)
    new_df = pd.DataFrame(rows)
    new_df["code"] = new_df["code"].astype(str).str.zfill(6)

    merged = pd.concat([existing, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged.sort_values(["code", "date"]).reset_index(drop=True)
    added = len(merged) - len(existing)
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        merged.to_csv(f, index=False)
    return added


def report_status() -> dict:
    """현재 데이터 상태 보고 (수집 없이)."""
    k200 = pd.read_csv(cfg.abspath(cfg.get("data.k200_csv")))
    ohlcv = pd.read_csv(cfg.abspath(cfg.get("data.ohlcv_gz")), dtype={"code": str})
    return {
        "k200_last_date":  str(k200["date"].max()),
        "ohlcv_last_date": str(ohlcv["date"].max()),
        "ohlcv_codes":     int(ohlcv["code"].nunique()),
        "ohlcv_rows":      int(len(ohlcv)),
    }


def run(check_only: bool = False) -> int:
    common.setup()
    status = report_status()
    logger.info(f"[datafeed] 현재 상태 {status}")
    if check_only:
        return 0

    if not common.is_trading_today():
        logger.info(f"[datafeed] {common.today_str()} 휴장일 — 수집 건너뜀")
        return 0

    settings = get_settings()
    kis = KIS(settings)

    # 1) 지수
    idx_code = cfg.get("index.k200_code")
    bar = fetch_index_bar(kis, idx_code)
    if not bar:
        logger.error("[datafeed] 코스피200 지수 수집 실패 — 중단")
        return 1
    logger.info(f"[datafeed] K200 {bar['date']} O:{bar['open']:.2f} H:{bar['high']:.2f} L:{bar['low']:.2f} C:{bar['close']:.2f}")

    # 2) 종목
    universe = load_universe()
    logger.info(f"[datafeed] 유니버스 {len(universe)}종목 수집 시작")
    rows, failed = fetch_stock_bars(kis, universe)
    logger.info(f"[datafeed] 수집 완료: {len(rows)} / {len(universe)}  실패 {len(failed)}")

    # 3) 검증 — 결측률
    cov = len(rows) / len(universe) * 100
    min_cov = cfg.get("data.min_data_coverage_pct", 95)
    if cov < min_cov:
        msg = f"[datafeed] 수집률 {cov:.1f}% < {min_cov}% — 중단"
        logger.error(msg)
        for c, n, r in failed[:10]:
            logger.error(f"  실패: [{c}] {n}: {r}")
        return 1

    # 4) 이상치 경고
    existing = pd.read_csv(cfg.abspath(cfg.get("data.ohlcv_gz")), dtype={"code": str})
    warnings = validate_rows(rows, existing, cfg.get("data.max_daily_change_pct", 30))
    for w in warnings[:20]:
        logger.warning(w)

    # 5) 지수 추가
    append_k200_csv(bar)
    logger.info("[datafeed] k200.csv 갱신 완료")

    # 6) 종목 추가
    added = append_ohlcv_gz(rows)
    logger.info(f"[datafeed] ohlcv.csv.gz 갱신 완료 ({added:+}행)")

    # 수집 완료 상태 재보고
    status = report_status()
    logger.info(f"[datafeed] 수집 후 상태 {status}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="autotrade/datafeed")
    p.add_argument("--check", action="store_true", help="수집 안 하고 상태만 보고")
    args = p.parse_args()
    return run(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
