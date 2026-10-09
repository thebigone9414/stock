#!/usr/bin/env python3
"""
engine_v3/stockdata/ohlcv.csv.gz 데이터 보강 배치 (수동 실행 전용)

레포 유니버스(KOSPI200 + KOSDAQ150 + ETF 2개) 중 engine_v3 데이터에
없는 종목만 pykrx로 10년치 OHLCV를 수집해 ohlcv.csv.gz에 병합한다.

출력:
  - engine_v3/stockdata/ohlcv.csv.gz 갱신 (code, name, date, open, high, low, close, volume)
  - scratchpad/missing_ohlcv_<YYYYMMDD>.csv 수집본만 백업
"""
import gzip
import io
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger
from pykrx import stock


ROOT = Path(__file__).parent.parent
KOSPI_CACHE = ROOT / "data" / "kospi200_cache.json"
KOSDAQ_CACHE = ROOT / "data" / "kosdaq150_cache.json"
ENGINE_OHLCV = ROOT / "engine_v3" / "stockdata" / "ohlcv.csv.gz"
EXTRA_CODES = {
    "122630": "KODEX 레버리지",
    "114800": "KODEX 인버스",
}


def load_target_codes() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (KOSPI_CACHE, KOSDAQ_CACHE):
        with open(path, "r", encoding="utf-8") as f:
            for s in json.load(f).get("stocks", []):
                code = s.get("code")
                name = s.get("name", code)
                if code and code not in out:
                    out[code] = name
    for c, n in EXTRA_CODES.items():
        out.setdefault(c, n)
    return out


def load_existing_codes() -> set[str]:
    df = pd.read_csv(ENGINE_OHLCV, dtype={"code": str})
    return set(df["code"].unique())


def fetch_one(code: str, name: str, fromdate: str, todate: str) -> pd.DataFrame:
    df = stock.get_market_ohlcv_by_date(fromdate, todate, code)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index().rename(columns={
        "날짜":     "date",
        "시가":     "open",
        "고가":     "high",
        "저가":     "low",
        "종가":     "close",
        "거래량":   "volume",
    })
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df.insert(0, "name", name)
    df.insert(0, "code", str(code).zfill(6))
    return df


def main() -> int:
    today = datetime.now()
    todate = today.strftime("%Y%m%d")
    fromdate = (today - timedelta(days=365 * 10)).strftime("%Y%m%d")

    target = load_target_codes()
    existing = load_existing_codes()
    missing = {c: n for c, n in target.items() if c not in existing}

    logger.info(f"목표 유니버스 {len(target)}  기존 {len(existing)}  누락 {len(missing)}")
    if not missing:
        logger.info("누락 종목 없음 — 종료")
        return 0

    for code, name in missing.items():
        logger.info(f"  누락: [{code}] {name}")

    collected: list[pd.DataFrame] = []
    failed: list[tuple[str, str, str]] = []

    for idx, (code, name) in enumerate(missing.items(), 1):
        try:
            df = fetch_one(code, name, fromdate, todate)
            if df.empty:
                failed.append((code, name, "empty"))
                logger.warning(f"[{idx}/{len(missing)}] [{code}] {name} — empty")
            else:
                collected.append(df)
                logger.info(f"[{idx}/{len(missing)}] [{code}] {name}  {len(df)}행")
        except Exception as e:
            failed.append((code, name, str(e)[:100]))
            logger.error(f"[{idx}/{len(missing)}] [{code}] {name} 실패: {e}")
        time.sleep(0.15)

    if not collected:
        logger.error("수집된 데이터 없음 — 종료")
        return 1

    new_df = pd.concat(collected, ignore_index=True)
    logger.info(f"신규 수집 {len(new_df):,}행")

    # 백업
    scratch = ROOT / "scratchpad"
    scratch.mkdir(exist_ok=True)
    backup = scratch / f"missing_ohlcv_{todate}.csv"
    new_df.to_csv(backup, index=False)
    logger.info(f"백업 저장: {backup}")

    # engine_v3 ohlcv.csv.gz에 병합
    logger.info(f"기존 로드: {ENGINE_OHLCV}")
    existing_df = pd.read_csv(ENGINE_OHLCV, dtype={"code": str})
    logger.info(f"  기존 {len(existing_df):,}행")

    merged = pd.concat([existing_df, new_df], ignore_index=True)
    merged["code"] = merged["code"].astype(str).str.zfill(6)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged.sort_values(["code", "date"]).reset_index(drop=True)

    logger.info(f"병합 후 {len(merged):,}행 ({len(merged) - len(existing_df):+,})  유니크 code {merged['code'].nunique()}")

    # 압축 저장
    with gzip.open(ENGINE_OHLCV, "wt", encoding="utf-8", newline="") as f:
        merged.to_csv(f, index=False)
    size_mb = ENGINE_OHLCV.stat().st_size / 1024 / 1024
    logger.info(f"저장 완료: {ENGINE_OHLCV} ({size_mb:.1f}MB)")

    if failed:
        logger.warning(f"실패 {len(failed)}종목:")
        for c, n, r in failed:
            logger.warning(f"  [{c}] {n}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
