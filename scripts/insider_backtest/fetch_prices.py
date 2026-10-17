"""insider_backtest/fetch_prices.py — yfinance 가격 수집

지시서 §1-2 / §2-5:
  - insider_P 의 unique 티커 + SPY, IWM
  - period="max", auto_adjust=False
  - 200개씩 묶음 다운, group_by="ticker", threads=True
  - 빈 응답은 price_available=0 (상장폐지 가능성)
  - 티커별 parquet 저장 → prices/{ticker}.parquet
  - prices_bench.csv.gz (SPY, IWM) 별도
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from common import OUT_DIR, PRICES_DIR, get_logger

log = get_logger("fetch_prices")

BENCH = ["SPY", "IWM"]


def load_tickers_from_P() -> list[str]:
    p = OUT_DIR / "insider_P.csv.gz"
    if not p.exists():
        log.error(f"insider_P.csv.gz 없음 — parse_form4 먼저 실행 필요")
        return []
    df = pd.read_csv(p, usecols=["ticker"], dtype=str)
    tickers = sorted({t for t in df["ticker"].dropna().unique() if t})
    return tickers


def _already_cached(ticker: str, min_rows: int = 20) -> bool:
    p = PRICES_DIR / f"{ticker}.parquet"
    if not p.exists():
        return False
    try:
        df = pd.read_parquet(p)
        return len(df) >= min_rows
    except Exception:
        return False


def _save_batch_result(result, requested: list[str]) -> tuple[int, int]:
    """yf.download(group_by='ticker', ...) 반환을 티커별로 분리 저장. (저장, 빈) 반환."""
    saved = empty = 0
    if result is None or len(result) == 0:
        return 0, len(requested)
    # multiindex columns: (ticker, field)
    if isinstance(result.columns, pd.MultiIndex):
        tickers_in = list(result.columns.get_level_values(0).unique())
    else:
        tickers_in = requested  # single-ticker case
    for t in requested:
        try:
            if isinstance(result.columns, pd.MultiIndex):
                sub = result[t].copy() if t in tickers_in else pd.DataFrame()
            else:
                sub = result.copy()
        except Exception:
            sub = pd.DataFrame()
        sub = sub.dropna(how="all")
        if sub.empty:
            # 빈 응답도 플래그 파일로 기록 (price_available=0)
            empty += 1
            (PRICES_DIR / f"{t}.empty").write_text("")
            continue
        sub = sub.reset_index()
        sub["Date"] = pd.to_datetime(sub["Date"]).dt.strftime("%Y-%m-%d")
        # standardize columns
        for c in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
            if c not in sub.columns:
                sub[c] = pd.NA
        sub = sub[["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]]
        try:
            sub.to_parquet(PRICES_DIR / f"{t}.parquet", index=False)
            # empty flag 있으면 삭제
            (PRICES_DIR / f"{t}.empty").unlink(missing_ok=True)
            saved += 1
        except Exception as e:
            log.warning(f"{t} parquet 저장 실패: {e}")
    return saved, empty


def download_batch(tickers: list[str], retries: int = 2) -> tuple[int, int]:
    delay = 5.0
    for attempt in range(retries + 1):
        try:
            result = yf.download(
                tickers,
                period="max",
                interval="1d",
                auto_adjust=False,
                actions=False,
                threads=True,
                group_by="ticker",
                progress=False,
            )
            return _save_batch_result(result, tickers)
        except Exception as e:
            if attempt < retries:
                log.warning(f"배치 재시도 {attempt+1}/{retries} after {delay}s: {e}")
                time.sleep(delay); delay *= 2
            else:
                log.error(f"배치 최종 실패: {e}")
                return 0, len(tickers)
    return 0, len(tickers)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=200)
    p.add_argument("--max-tickers", type=int, default=0, help="디버그용 상한 (0=전체)")
    p.add_argument("--skip-bench", action="store_true")
    args = p.parse_args()

    tickers = load_tickers_from_P()
    log.info(f"insider_P 유니크 티커 {len(tickers)}개")
    if args.max_tickers > 0:
        tickers = tickers[: args.max_tickers]

    # 캐시 있는 것 제외
    todo = [t for t in tickers if not _already_cached(t)]
    skipped = len(tickers) - len(todo)
    log.info(f"캐시 skip {skipped}, 다운로드 대상 {len(todo)}")

    total_saved = total_empty = 0
    for i in range(0, len(todo), args.batch_size):
        batch = todo[i : i + args.batch_size]
        log.info(f"배치 {i//args.batch_size + 1} / {(len(todo)-1)//args.batch_size + 1} — {len(batch)}개")
        s, e = download_batch(batch)
        total_saved += s; total_empty += e
        time.sleep(1.0)  # Yahoo 완화

    # 벤치마크
    if not args.skip_bench:
        log.info("벤치마크 (SPY, IWM) 다운로드")
        s, e = download_batch(BENCH)
        total_saved += s; total_empty += e
        # bench 전용 csv.gz
        rows = []
        for t in BENCH:
            fp = PRICES_DIR / f"{t}.parquet"
            if fp.exists():
                df = pd.read_parquet(fp)
                df["ticker"] = t
                rows.append(df)
        if rows:
            bench_df = pd.concat(rows, ignore_index=True)
            bench_df = bench_df[["ticker", "Date", "Open", "Close", "Adj Close"]]
            out = OUT_DIR / "prices_bench.csv.gz"
            bench_df.to_csv(out, index=False, compression="gzip")
            log.info(f"저장: {out}  ({len(bench_df):,}건)")

    log.info(f"완료 — 저장 {total_saved}  빈/상폐 {total_empty}  skip {skipped}  합계 {len(tickers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
