#!/usr/bin/env python3
"""
KOSPI200 + KOSDAQ150 종목 10년치 OHLCV 추출 (수동 실행 전용)

pykrx로 10년 기간 일봉을 받아 하나의 xlsx로 저장.
- 종목이 10년 미만 상장이면 상장 후부터만 수집
- 하나의 시트에 모든 행 (code, name, date, open, high, low, close, volume, amount)
- 결과: scratchpad/ohlcv_10y_<YYYYMMDD>.xlsx → GitHub Actions 아티팩트 업로드
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger
from pykrx import stock


KOSPI_CACHE = Path(__file__).parent.parent / "data" / "kospi200_cache.json"
KOSDAQ_CACHE = Path(__file__).parent.parent / "data" / "kosdaq150_cache.json"
OUT_DIR = Path(__file__).parent.parent / "scratchpad"


def load_universe() -> list[tuple[str, str]]:
    """KOSPI200 + KOSDAQ150 (코드, 이름) 반환. 중복 제거."""
    out: dict[str, str] = {}
    for path in (KOSPI_CACHE, KOSDAQ_CACHE):
        with open(path, "r", encoding="utf-8") as f:
            for s in json.load(f).get("stocks", []):
                code = s.get("code")
                name = s.get("name", code)
                if code and code not in out:
                    out[code] = name
    return list(out.items())


def fetch_one(code: str, name: str, fromdate: str, todate: str) -> pd.DataFrame:
    """단일 종목 OHLCV. 상장일이 fromdate 이후면 자동으로 짧게 반환."""
    df = stock.get_market_ohlcv_by_date(fromdate, todate, code)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index().rename(columns={
        "날짜":       "date",
        "시가":       "open",
        "고가":       "high",
        "저가":       "low",
        "종가":       "close",
        "거래량":     "volume",
        "거래대금":   "amount",
        "등락률":     "change_pct",
    })
    # 필요한 컬럼만
    cols = [c for c in ["date", "open", "high", "low", "close", "volume", "amount"] if c in df.columns]
    df = df[cols].copy()
    df.insert(0, "name", name)
    df.insert(0, "code", code)
    return df


def main() -> int:
    today = datetime.now()
    todate = today.strftime("%Y%m%d")
    fromdate = (today - timedelta(days=365 * 10)).strftime("%Y%m%d")

    universe = load_universe()
    logger.info(f"총 {len(universe)}종목 10년치 수집 시작 {fromdate}~{todate}")

    all_dfs: list[pd.DataFrame] = []
    failed: list[tuple[str, str, str]] = []

    for idx, (code, name) in enumerate(universe, 1):
        try:
            df = fetch_one(code, name, fromdate, todate)
            if df.empty:
                failed.append((code, name, "empty"))
                logger.warning(f"[{idx}/{len(universe)}] [{code}] {name} — empty")
            else:
                all_dfs.append(df)
                logger.info(f"[{idx}/{len(universe)}] [{code}] {name}  {len(df)}행")
        except Exception as e:
            failed.append((code, name, str(e)[:100]))
            logger.error(f"[{idx}/{len(universe)}] [{code}] {name} 실패: {e}")

        # pykrx가 네이버/KRX를 호출 — rate limit 완화용
        time.sleep(0.15)

    if not all_dfs:
        logger.error("수집된 데이터 없음 — 종료")
        return 1

    big = pd.concat(all_dfs, ignore_index=True)
    big["date"] = pd.to_datetime(big["date"]).dt.strftime("%Y-%m-%d")
    logger.info(f"총 {len(big):,}행 수집 완료")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"ohlcv_10y_{todate}.xlsx"
    logger.info(f"엑셀 저장 중: {out_path}")

    with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
        big.to_excel(writer, sheet_name="OHLCV", index=False)
        if failed:
            pd.DataFrame(failed, columns=["code", "name", "reason"]).to_excel(
                writer, sheet_name="FAILED", index=False
            )

    size_mb = out_path.stat().st_size / 1024 / 1024
    logger.info(f"완료 — {out_path} ({size_mb:.1f}MB), 실패:{len(failed)}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
