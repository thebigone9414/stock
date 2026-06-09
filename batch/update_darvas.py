#!/usr/bin/env python3
"""
Darvas Box 일일 스크리닝 배치 (전략5)

[전략]
  박스 형성 : 최근 20 거래일(오늘 제외) 종가 최고·최저가로 박스 정의
              박스 범위(high-low)/high ≤ 15% → 컨솔리데이션 확인
  브레이크아웃: 오늘 종가 > 박스 상단 AND 거래량 ≥ 50일 평균의 150%
  시장 필터 : KODEX200 MA20 > MA60

[OHLCV 캐시]
  canslim_ohlcv_cache.json 우선 재사용 (S3 배치 후 실행).
  S5 전용 종목은 darvas_ohlcv_cache.json 에 별도 저장.

장 마감 후(17:10 KST) GitHub Actions에서 실행.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from utils.throttler import RateThrottler
from kis.factory import KIS
import data.darvas_store as darvas_store
from data.watchlist import get_s2_watchlist

KST = pytz.timezone("Asia/Seoul")

CANSLIM_OHLCV_CACHE_PATH = Path("data/canslim_ohlcv_cache.json")
DARVAS_OHLCV_CACHE_PATH  = Path("data/darvas_ohlcv_cache.json")
OHLCV_DAYS               = 300
INCREMENTAL_DAYS         = 30

KODEX200_CODE = "069500"

# Darvas 파라미터
BOX_DAYS          = 20     # 박스 형성 기간 (거래일)
BOX_MAX_RANGE_PCT = 15.0   # 박스 최대 범위 %
BREAKOUT_VOL_X    = 1.50   # 브레이크아웃 거래량 배수
MARKET_MA_FAST    = 20     # 시장 필터 단기 MA
MARKET_MA_SLOW    = 60     # 시장 필터 장기 MA


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


# ── 지표 계산 ─────────────────────────────────────────────────────────

def _check_darvas(closes: list, volumes: list) -> dict | None:
    """
    Darvas Box 브레이크아웃 체크.
    반환: {"box_high": int, "box_low": int, "box_range_pct": float,
           "volume_ratio": float, "all_pass": bool}
    조건 미충족 / 데이터 부족 시 None 반환.
    """
    n = len(closes)
    if n < BOX_DAYS + 2 or len(volumes) < 52:
        return None

    today_close = closes[-1]
    box_window  = closes[-(BOX_DAYS + 1):-1]   # 오늘 제외 BOX_DAYS일

    box_high = max(box_window)
    box_low  = min(box_window)

    if box_high <= 0:
        return None

    box_range_pct = (box_high - box_low) / box_high * 100

    # 거래량 비율: 50일 평균(전일 기준) vs 오늘
    vol_avg   = sum(volumes[-51:-1]) / 50
    vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 0.0

    breakout      = today_close > box_high
    tight_box     = box_range_pct <= BOX_MAX_RANGE_PCT
    vol_surged    = vol_ratio >= BREAKOUT_VOL_X

    return {
        "box_high":      int(box_high),
        "box_low":       int(box_low),
        "box_range_pct": round(box_range_pct, 2),
        "volume_ratio":  round(vol_ratio, 3),
        "all_pass":      breakout and tight_box and vol_surged,
    }


def _check_market(kodex_closes: list) -> bool:
    """KODEX200 MA20 > MA60"""
    if len(kodex_closes) < MARKET_MA_SLOW:
        return True   # 데이터 부족 시 필터 비활성
    ma_fast = sum(kodex_closes[-MARKET_MA_FAST:]) / MARKET_MA_FAST
    ma_slow = sum(kodex_closes[-MARKET_MA_SLOW:]) / MARKET_MA_SLOW
    return ma_fast > ma_slow


# ── OHLCV 증분 로딩 ───────────────────────────────────────────────────

def _fetch_ohlcv(market, code: str, cache_entry: dict | None,
                 throttler: RateThrottler, today: str) -> dict | None:
    """증분 또는 전체 OHLCV 취득. 실패 시 None."""
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
            old_volumes = cache_entry.get("volumes", [])
            new_closes  = raw["closes"]
            new_volumes = raw.get("volumes", [])

            # 날짜 기준 dedup (last_date 이후만 append)
            last_date = cache_entry.get("last_date", "")
            new_dates = raw.get("dates", [])
            if new_dates and last_date:
                idx = next(
                    (i for i, d in enumerate(new_dates) if d > last_date), len(new_dates)
                )
                new_closes  = new_closes[idx:]
                new_volumes = new_volumes[idx:] if new_volumes else []

            merged_closes  = (old_closes + new_closes)[-OHLCV_DAYS:]
            merged_volumes = (old_volumes + new_volumes)[-OHLCV_DAYS:]
        else:
            merged_closes  = raw["closes"][-OHLCV_DAYS:]
            merged_volumes = raw.get("volumes", [])[-OHLCV_DAYS:]

        return {
            "closes":    merged_closes,
            "volumes":   merged_volumes,
            "last_date": today,
        }
    except Exception as e:
        logger.debug(f"[Darvas배치] [{code}] OHLCV 취득 실패: {e}")
        return cache_entry


# ── 메인 배치 ─────────────────────────────────────────────────────────

def run_batch(market, notifier=None, force: bool = False) -> None:
    now_kst = datetime.now(KST)
    today   = now_kst.strftime("%Y-%m-%d")

    setup_logger("INFO")
    logger.info(f"[Darvas배치] 시작 {today}")

    # 중복 실행 방지 (같은 날 16:00 이후 이미 완료된 경우)
    if not force:
        existing = darvas_store.load_data()
        if existing.get("updated_at") == today and now_kst.hour >= 16:
            logger.info("[Darvas배치] 오늘 이미 완료 — 건너뜀 (--force 로 강제 실행 가능)")
            return

    from data.holidays import is_market_holiday
    if not force and is_market_holiday(now_kst.date()):
        logger.info("[Darvas배치] 휴장일 — 건너뜀")
        return

    watchlist  = get_s2_watchlist()
    throttler  = RateThrottler(max_per_second=9)

    # 캐시 로드 (canslim 우선, 없으면 darvas 자체)
    canslim_cache = _load_cache(CANSLIM_OHLCV_CACHE_PATH)
    darvas_cache  = _load_cache(DARVAS_OHLCV_CACHE_PATH)

    # KODEX200 시장 필터
    kodex_entry  = canslim_cache.get(KODEX200_CODE) or darvas_cache.get(KODEX200_CODE, {})
    kodex_entry  = _fetch_ohlcv(market, KODEX200_CODE, kodex_entry, throttler, today)
    kodex_closes = kodex_entry.get("closes", []) if kodex_entry else []
    market_up    = _check_market(kodex_closes)

    logger.info(f"[Darvas배치] 시장 필터: {'상승(MA20>MA60)' if market_up else '하락(MA20<=MA60)'}")

    result_stocks: dict = {}
    all_pass_list: list = []
    new_darvas_cache = {KODEX200_CODE: kodex_entry} if kodex_entry else {}

    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)

        # 캐시에서 OHLCV 확보
        cache_entry = canslim_cache.get(code) or darvas_cache.get(code)
        entry = _fetch_ohlcv(market, code, cache_entry, throttler, today)
        if entry:
            new_darvas_cache[code] = entry

        closes  = entry.get("closes",  []) if entry else []
        volumes = entry.get("volumes", []) if entry else []

        if not closes:
            continue

        box_result = _check_darvas(closes, volumes)
        if box_result is None:
            continue

        all_pass = market_up and box_result["all_pass"]

        result_stocks[code] = {
            "name":          name,
            "close":         int(closes[-1]),
            "box_high":      box_result["box_high"],
            "box_low":       box_result["box_low"],
            "box_range_pct": box_result["box_range_pct"],
            "volume_ratio":  box_result["volume_ratio"],
            "all_pass":      all_pass,
            "last_date":     today,
        }

        if all_pass:
            all_pass_list.append(f"[{code}] {name}  박스범위:{box_result['box_range_pct']:.1f}%  "
                                 f"거래량:{box_result['volume_ratio']:.2f}배")

    # 저장
    darvas_store.save_data({
        "updated_at":     today,
        "market_uptrend": market_up,
        "stocks":         result_stocks,
    })
    _save_cache(DARVAS_OHLCV_CACHE_PATH, new_darvas_cache)

    # git 커밋·푸시
    darvas_store.git_commit_push(
        [str(darvas_store.DARVAS_DATA_PATH), str(DARVAS_OHLCV_CACHE_PATH)],
        f"chore: Darvas Box 스크리닝 {today} ({len(all_pass_list)}종목 신호)",
    )

    # 알림
    msg_lines = [f"[Darvas배치] {today}  시장:{'상승' if market_up else '하락'}"]
    msg_lines.append(f"전체 {len(result_stocks)}종목 스크리닝  브레이크아웃 {len(all_pass_list)}종목")
    if all_pass_list:
        msg_lines.append("── 브레이크아웃 신호 ──")
        msg_lines.extend(all_pass_list[:10])
        if len(all_pass_list) > 10:
            msg_lines.append(f"  ...외 {len(all_pass_list)-10}종목")

    msg = "\n".join(msg_lines)
    logger.info(msg)
    if notifier:
        notifier.notify(msg)

    logger.info(f"[Darvas배치] 완료 — 브레이크아웃 {len(all_pass_list)}종목")


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
