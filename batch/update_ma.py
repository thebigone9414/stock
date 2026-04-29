#!/usr/bin/env python3
"""
MA 이평선 배치 업데이트
장 마감 후(16:30 KST) GitHub Actions에서 실행.
84종목 일봉 800일치 수집 → MA 계산 → data/ma_data.json 업데이트 → git push

Usage:
    python batch/update_ma.py
"""
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.throttler import RateThrottler
from kis.factory import KIS
from data.watchlist import WATCHLIST
import data.ma_store as ma_store

KST          = pytz.timezone("Asia/Seoul")
MA_PERIODS   = [5, 21, 62, 248, 744]
OHLCV_DAYS   = 820   # 800일 + 여유 20일
UPTREND_COLS = [62, 248, 744]


def _uptrend_days(ma_series: pd.Series) -> int:
    """최근 연속 상승일 수 (MA값이 전날보다 높은 날 역순 카운트)"""
    vals = ma_series.dropna().values
    if len(vals) < 2:
        return 0
    count = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1]:
            count += 1
        else:
            break
    return count


def compute_stock_entry(code: str, name: str, sector: str, df: pd.DataFrame) -> dict:
    """일봉 DataFrame → MA 지표 dict 반환"""
    close = df["close"]
    ma    = {p: close.rolling(p).mean() for p in MA_PERIODS}

    # 데이터 충분 여부 확인
    for p in MA_PERIODS:
        if pd.isna(ma[p].iloc[-1]):
            raise ValueError(f"MA{p} 계산 불가 (데이터 {len(df)}행)")

    curr = {p: float(ma[p].iloc[-1]) for p in MA_PERIODS}
    prev = {p: float(ma[p].iloc[-2]) for p in MA_PERIODS}

    fully_aligned      = curr[5] > curr[21] > curr[62] > curr[248] > curr[744]
    prev_fully_aligned = prev[5] > prev[21] > prev[62] > prev[248] > prev[744]

    return {
        "name":               name,
        "sector":             sector,
        "last_date":          df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "close":              int(close.iloc[-1]),
        # 현재 이평선
        "ma5":   curr[5],   "ma21":  curr[21],
        "ma62":  curr[62],  "ma248": curr[248],  "ma744": curr[744],
        # 전일 이평선
        "prev_ma5":  prev[5],  "prev_ma21":  prev[21],
        "prev_ma62": prev[62], "prev_ma248": prev[248], "prev_ma744": prev[744],
        # 정배열 여부
        "fully_aligned":      fully_aligned,
        "prev_fully_aligned": prev_fully_aligned,
        # 매도 신호: ma21이 ma62 아래
        "ma21_below_ma62":    curr[21] < curr[62],
        # 연속 상승일
        "ma62_trend_days":    _uptrend_days(ma[62]),
        "ma248_trend_days":   _uptrend_days(ma[248]),
        "ma744_trend_days":   _uptrend_days(ma[744]),
    }


def run_batch(market) -> None:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logger.info(f"══════════════════════════════════════════")
    logger.info(f" MA 배치 업데이트 시작 [{today}]")
    logger.info(f" 대상: {len(WATCHLIST)}종목 / 조회기간: 최근 {OHLCV_DAYS}영업일")
    logger.info(f"══════════════════════════════════════════")

    throttler  = RateThrottler(max_per_second=9)
    existing   = ma_store.load()
    stocks_out = {}
    ok, fail, skip = 0, 0, 0

    for i, stock in enumerate(WATCHLIST, 1):
        code   = stock["code"]
        name   = stock["name"]
        sector = stock["sector"]
        try:
            df = market.get_ohlcv_long(code, days=OHLCV_DAYS, throttler=throttler)

            if df.empty or len(df) < 750:
                logger.warning(
                    f"[{i:02d}/{len(WATCHLIST)}] [{code}] {name} "
                    f"데이터 부족({len(df)}행) — MA744 계산 불가, 건너뜀"
                )
                skip += 1
                continue

            entry = compute_stock_entry(code, name, sector, df)
            stocks_out[code] = entry

            # 주목할 신호 표시
            signal = ""
            if entry["fully_aligned"] and not entry["prev_fully_aligned"]:
                signal = " ★정배열첫날"
            elif entry["ma21_below_ma62"]:
                signal = " ⚠ma21<ma62"

            logger.info(
                f"[{i:02d}/{len(WATCHLIST)}] [{code}] {name:10s} "
                f"정배열:{str(entry['fully_aligned']):5s} "
                f"62추세:{entry['ma62_trend_days']:3d}일 "
                f"248추세:{entry['ma248_trend_days']:3d}일{signal}"
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:02d}/{len(WATCHLIST)}] [{code}] {name} 실패: {e}")
            fail += 1

    # 기존 포지션은 유지하고 MA 테이블만 교체
    existing["updated_at"] = today
    existing["stocks"]     = stocks_out
    ma_store.save(existing)

    ma_store.git_commit_push(
        [str(ma_store.MA_DATA_PATH)],
        f"data: MA 이평선 업데이트 {today} ({ok}/{len(WATCHLIST)}종목)",
    )

    # 매수 후보 요약 출력
    buy_signals = [
        f"  [{c}] {s['name']} 62추세:{s['ma62_trend_days']}일 248추세:{s['ma248_trend_days']}일"
        for c, s in stocks_out.items()
        if s["fully_aligned"] and not s["prev_fully_aligned"]
        and s["ma62_trend_days"] >= 20 and s["ma248_trend_days"] >= 20 and s["ma744_trend_days"] >= 20
    ]
    sell_signals = [
        f"  [{c}] {s['name']}"
        for c, s in stocks_out.items()
        if s["ma21_below_ma62"]
    ]

    logger.info(f"══════════════════════════════════════════")
    logger.info(f" MA 배치 완료: 성공:{ok} / 실패:{fail} / 부족:{skip}")
    if buy_signals:
        logger.info(f" 내일 매수 후보 ({len(buy_signals)}종목):")
        for s in buy_signals:
            logger.info(s)
    if sell_signals:
        logger.info(f" 내일 매도 대상 ({len(sell_signals)}종목):")
        for s in sell_signals:
            logger.info(s)
    logger.info(f"══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info(f"=== MA 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ===")
    kis = KIS(settings)
    run_batch(kis.market)
