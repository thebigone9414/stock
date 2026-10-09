#!/usr/bin/env python3
"""
Connors RSI(2) 일일 스크리닝 배치 (전략6)

[전략 — Larry Connors]
  시장 필터  : KODEX200 종가 > MA200 (장기 상승장)
  매수 조건  : 개별 종목 종가 > MA200 AND RSI(2) < 10
  청산 신호  : RSI(2) >= 65 (평균회귀 완성) → 다음날 09:00 시장가 매도
  손절       : -5%
  타임스탑   : 10 거래일

[RSI(2) 계산]
  Wilder's EMA 방식 (alpha=1/period)

[OHLCV 캐시]
  canslim_ohlcv_cache.json 우선 재사용.
  S6 전용 종목은 connors_ohlcv_cache.json 에 별도 저장.

장 마감 후(17:20 KST) GitHub Actions에서 실행.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from utils.throttler import RateThrottler
from kis.factory import KIS
import data.connors_store as connors_store
from data.watchlist import get_s2_watchlist

KST = pytz.timezone("Asia/Seoul")

CANSLIM_OHLCV_CACHE_PATH  = Path("data/canslim_ohlcv_cache.json")
CONNORS_OHLCV_CACHE_PATH  = Path("data/connors_ohlcv_cache.json")
OHLCV_DAYS                = 300
INCREMENTAL_DAYS          = 30

KODEX200_CODE = "069500"

# Connors RSI(2) 파라미터
RSI_PERIOD    = 2
MA_LONG       = 200     # 종목 필터: close > MA200
MARKET_MA     = 200     # 시장 필터: KODEX200 > MA200
RSI_ENTRY     = 10.0    # 매수: RSI(2) < 10
RSI_EXIT      = 65.0    # 청산: RSI(2) >= 65


# ── RSI 계산 ──────────────────────────────────────────────────────────

def _rsi(closes: list, period: int = 2) -> float:
    """Wilder's RSI. 데이터 부족 시 -1.0 반환."""
    n = len(closes)
    warmup = period * 50   # 안정적 수렴을 위한 충분한 워밍업
    if n < warmup + 1:
        return -1.0
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / period, adjust=False).mean()
    last_g = avg_g.iloc[-1]
    last_l = avg_l.iloc[-1]
    if last_l == 0:
        return 100.0
    rs = last_g / last_l
    return round(float(100 - 100 / (1 + rs)), 2)


# ── OHLCV 캐시 I/O ────────────────────────────────────────────────────

def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def _fetch_ohlcv(market, code: str, cache_entry: dict | None,
                 throttler: RateThrottler, today: str) -> dict | None:
    try:
        if (cache_entry
                and cache_entry.get("last_date") == today
                and len(cache_entry.get("closes", [])) >= 200):
            return cache_entry

        throttler.acquire()
        if (cache_entry
                and cache_entry.get("last_date")
                and len(cache_entry.get("closes", [])) >= 200):
            raw = market.get_ohlcv(code, days=INCREMENTAL_DAYS)
        else:
            raw = market.get_ohlcv(code, days=OHLCV_DAYS)

        if not raw or not raw.get("closes"):
            return cache_entry

        if cache_entry and cache_entry.get("closes"):
            old_closes  = cache_entry["closes"]
            last_date   = cache_entry.get("last_date", "")
            new_dates   = raw.get("dates", [])
            new_closes  = raw["closes"]
            if new_dates and last_date:
                idx = next(
                    (i for i, d in enumerate(new_dates) if d > last_date), len(new_dates)
                )
                new_closes = new_closes[idx:]
            merged = (old_closes + new_closes)[-OHLCV_DAYS:]
        else:
            merged = raw["closes"][-OHLCV_DAYS:]

        return {"closes": merged, "last_date": today}
    except Exception as e:
        logger.debug(f"[Connors배치] [{code}] OHLCV 취득 실패: {e}")
        return cache_entry


# ── 메인 배치 ─────────────────────────────────────────────────────────

def run_batch(market, notifier=None, force: bool = False) -> None:
    now_kst = datetime.now(KST)
    today   = now_kst.strftime("%Y-%m-%d")

    setup_logger("INFO")
    logger.info(f"[Connors배치] 시작 {today}")

    if not force:
        existing = connors_store.load_data()
        if existing.get("updated_at") == today and now_kst.hour >= 16:
            logger.info("[Connors배치] 오늘 이미 완료 — 건너뜀")
            return

    from data.holidays import is_market_holiday
    if not force and is_market_holiday(now_kst.date()):
        logger.info("[Connors배치] 휴장일 — 건너뜀")
        return

    watchlist = get_s2_watchlist()
    throttler = RateThrottler(max_per_second=9)

    canslim_cache  = _load_cache(CANSLIM_OHLCV_CACHE_PATH)
    connors_cache  = _load_cache(CONNORS_OHLCV_CACHE_PATH)

    # KODEX200 시장 필터
    kodex_entry  = canslim_cache.get(KODEX200_CODE) or connors_cache.get(KODEX200_CODE, {})
    kodex_entry  = _fetch_ohlcv(market, KODEX200_CODE, kodex_entry, throttler, today)
    kodex_closes = kodex_entry.get("closes", []) if kodex_entry else []

    market_up = False
    if len(kodex_closes) >= MARKET_MA:
        ma200 = sum(kodex_closes[-MARKET_MA:]) / MARKET_MA
        market_up = kodex_closes[-1] > ma200

    logger.info(f"[Connors배치] 시장 필터: {'상승(>MA200)' if market_up else '하락(<=MA200)'}")

    result_stocks: dict = {}
    buy_signals:   list = []
    exit_signals:  list = []
    new_cache = {KODEX200_CODE: kodex_entry} if kodex_entry else {}

    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)

        cache_entry = canslim_cache.get(code) or connors_cache.get(code)
        entry = _fetch_ohlcv(market, code, cache_entry, throttler, today)
        if entry:
            new_cache[code] = entry

        closes = entry.get("closes", []) if entry else []
        n = len(closes)
        if n < MA_LONG + 1:
            continue

        rsi2 = _rsi(closes, RSI_PERIOD)
        if rsi2 < 0:
            continue

        ma200       = sum(closes[-MA_LONG:]) / MA_LONG
        above_ma200 = closes[-1] > ma200
        rsi2_exit   = rsi2 >= RSI_EXIT
        all_pass    = market_up and above_ma200 and (rsi2 < RSI_ENTRY)

        result_stocks[code] = {
            "name":        name,
            "close":       int(closes[-1]),
            "ma200":       round(ma200, 1),
            "rsi2":        rsi2,
            "rsi2_exit":   rsi2_exit,
            "all_pass":    all_pass,
            "last_date":   today,
        }

        if all_pass:
            buy_signals.append(f"[{code}] {name}  RSI(2)={rsi2:.1f}  종가:{int(closes[-1]):,}")
        if rsi2_exit:
            exit_signals.append(f"[{code}] {name}  RSI(2)={rsi2:.1f}")

    # 저장
    connors_store.save_data({
        "updated_at":     today,
        "market_uptrend": market_up,
        "stocks":         result_stocks,
    })
    _save_cache(CONNORS_OHLCV_CACHE_PATH, new_cache)

    connors_store.git_commit_push(
        [str(connors_store.CONNORS_DATA_PATH), str(CONNORS_OHLCV_CACHE_PATH)],
        f"chore: Connors RSI(2) 스크리닝 {today} (매수{len(buy_signals)} 청산{len(exit_signals)}종목)",
    )

    msg_lines = [f"[Connors배치] {today}  시장:{'상승' if market_up else '하락'}"]
    msg_lines.append(f"전체 {len(result_stocks)}종목  매수신호 {len(buy_signals)}  청산신호 {len(exit_signals)}")
    if buy_signals:
        msg_lines.append("── RSI(2)<10 매수신호 ──")
        msg_lines.extend(buy_signals[:10])
    if exit_signals:
        msg_lines.append("── RSI(2)>=65 청산신호 ──")
        msg_lines.extend(exit_signals[:10])

    msg = "\n".join(msg_lines)
    logger.info(msg)
    if notifier:
        notifier.notify(msg)

    logger.info(f"[Connors배치] 완료 — 매수신호 {len(buy_signals)} 청산신호 {len(exit_signals)}종목")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    setup_logger(settings.log_level)
    kis      = KIS(settings)
    notifier = Notifier.from_settings(settings)
    run_batch(kis.market, notifier=notifier, force=args.force)
