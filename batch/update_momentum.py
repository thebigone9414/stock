#!/usr/bin/env python3
"""
S5 Momentum 일일 스크리닝 배치

[매수 조건]
  ① MA 부분 정배열: MA5 > MA21 > MA62
  ② 52주 신고가 돌파: 당일 종가 ≥ 52주(252거래일) 최고가
  ③ 수급: 외국인 OR 기관 중 하나가 3거래일 이상 연속 순매수
  ④ 거래량: 당일 거래량 ≥ 20일 평균 × 1.2

[청산 조건 — trade_decision.py(20:00)에서 판정]
  ① 손절: -7%
  ② MA5 하회: 종가 < MA5
  ③ 수급 역전: 매수 주체(buyer_type) 3일 연속 순매도
  ④ 시간스탑: 보유 15영업일 초과
"""
import json
import sys
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
import data.ma_store as ma_store
import data.momentum_store as momentum_store
from data.watchlist import get_s2_watchlist

KST = pytz.timezone("Asia/Seoul")

OHLCV_CACHE_PATH      = Path("data/ohlcv_cache.json")
INVESTOR_CACHE_PATH   = Path("data/momentum_investor_cache.json")

CONSEC_DAYS_MIN = 3
VOL_RATIO_MIN   = 1.2
STOP_LOSS       = 0.07


def _load_ohlcv_cache() -> dict:
    if not OHLCV_CACHE_PATH.exists():
        return {}
    with open(OHLCV_CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_investor_cache() -> dict:
    if not INVESTOR_CACHE_PATH.exists():
        return {}
    with open(INVESTOR_CACHE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_investor_cache(cache: dict) -> None:
    INVESTOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INVESTOR_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def _count_consec_buying(trend: list) -> tuple:
    """trend: list of {"date", "frgn_net", "orgn_net"} (최신 first)
    Returns (frgn_consec, orgn_consec) — 가장 최근부터 연속 순매수 일수
    """
    frgn_consec = 0
    orgn_consec = 0
    for rec in trend:
        if rec.get("frgn_net", 0) > 0:
            frgn_consec += 1
        else:
            break
    for rec in trend:
        if rec.get("orgn_net", 0) > 0:
            orgn_consec += 1
        else:
            break
    return frgn_consec, orgn_consec


def _check_s5_entries(candidates: list, today_str: str, notifier: Notifier = None) -> None:
    positions = momentum_store.load_positions()
    entries   = []

    for c in candidates:
        code = c["code"]
        if code in positions:
            continue
        entries.append({
            "code":        code,
            "name":        c["name"],
            "buyer_type":  c["buyer_type"],
            "consec_days": c["consec_days"],
            "vol_ratio":   round(c["vol_ratio"], 3),
            "ma5":         c["ma5"],
            "date":        today_str,
        })

    momentum_store.set_entry_pending(entries)

    if entries:
        logger.info(f"[S5 매수결정] {len(entries)}종목 → entry_pending 설정")
        for e in entries[:3]:
            logger.info(
                f"  [{e['code']}] {e['name']}  "
                f"buyer={e['buyer_type']}  consec={e['consec_days']}일  "
                f"vol_x={e['vol_ratio']:.2f}  MA5={e['ma5']:,}"
            )
        if notifier:
            lines = [f"[S5 Momentum] 내일 09:00 매수 예정 {len(entries)}종목:"]
            for e in entries[:5]:
                lines.append(
                    f"  [{e['code']}] {e['name']}  "
                    f"{e['buyer_type']} {e['consec_days']}일  vol×{e['vol_ratio']:.2f}"
                )
            notifier.notify("\n".join(lines))
    else:
        logger.info("[S5 매수결정] 후보 없음")


def run_batch(market, notifier: Notifier = None, force: bool = False) -> None:
    from data.holidays import is_market_holiday

    today = datetime.now(KST).strftime("%Y-%m-%d")

    if not force:
        pending = momentum_store.get_entry_pending()
        if pending and any(e.get("date") == today for e in pending):
            logger.info(f"[Momentum배치] {today} 이미 완료 — 중복 실행 건너뜀")
            return
        if is_market_holiday():
            logger.info(f"[Momentum배치] {today} 휴장일 — 미실행")
            return
    else:
        logger.info("[Momentum배치] --force 모드: 휴장일·중복 체크 건너뜀")

    universe      = get_s2_watchlist()
    ohlcv_cache   = _load_ohlcv_cache()
    inv_cache     = _load_investor_cache()
    throttler     = RateThrottler(max_per_second=9)
    n_total       = len(universe)

    logger.info("══════════════════════════════════════════")
    logger.info(f" S5 Momentum 배치 시작 [{today}]  대상:{n_total}종목")
    logger.info(f" 조건: MA정배열 + 52주신고가 + 연속순매수{CONSEC_DAYS_MIN}일 + 거래량×{VOL_RATIO_MIN}")
    logger.info("══════════════════════════════════════════")

    candidates = []

    for i, stock in enumerate(universe, 1):
        code = stock["code"]
        name = stock.get("name", code)

        try:
            # ① MA 데이터 확인
            stock_ma = ma_store.get_stock(code)
            if not stock_ma:
                continue

            ma5  = stock_ma.get("ma5",  0)
            ma21 = stock_ma.get("ma21", 0)
            ma62 = stock_ma.get("ma62", 0)

            if not (ma5 and ma21 and ma62):
                continue

            # ① MA 부분 정배열: MA5 > MA21 > MA62
            if not (ma5 > ma21 > ma62):
                continue

            # OHLCV 캐시 확인
            cache_entry = ohlcv_cache.get(code, {})
            closes  = cache_entry.get("closes", [])
            volumes = cache_entry.get("volumes", [])

            if len(closes) < 22:
                continue

            close_today = closes[-1]

            # ② 52주 신고가 돌파: 당일 종가 ≥ 최근 252거래일 최고가
            hi_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
            if close_today < hi_52w:
                continue

            # ④ 거래량: 당일 거래량 ≥ 20일 평균 × 1.2
            if len(volumes) < 21:
                continue
            vol_avg_20 = sum(volumes[-21:-1]) / 20
            if vol_avg_20 <= 0:
                continue
            vol_ratio = volumes[-1] / vol_avg_20
            if vol_ratio < VOL_RATIO_MIN:
                continue

            # ③ 수급: 투자자별 매매동향 조회 (캐시 활용)
            cached_inv = inv_cache.get(code, {})
            if cached_inv.get("date") == today:
                trend = cached_inv.get("trend", [])
            else:
                throttler.acquire()
                trend = market.get_investor_trend_history(code, days=5)
                inv_cache[code] = {"date": today, "trend": trend}

            if not trend:
                continue

            frgn_consec, orgn_consec = _count_consec_buying(trend)

            buyer_type  = None
            consec_days = 0

            if frgn_consec >= CONSEC_DAYS_MIN and orgn_consec >= CONSEC_DAYS_MIN:
                buyer_type  = "both"
                consec_days = max(frgn_consec, orgn_consec)
            elif frgn_consec >= CONSEC_DAYS_MIN:
                buyer_type  = "foreign"
                consec_days = frgn_consec
            elif orgn_consec >= CONSEC_DAYS_MIN:
                buyer_type  = "institution"
                consec_days = orgn_consec

            if buyer_type is None:
                continue

            candidates.append({
                "code":        code,
                "name":        name,
                "buyer_type":  buyer_type,
                "consec_days": consec_days,
                "vol_ratio":   vol_ratio,
                "ma5":         int(ma5),
                "close":       close_today,
                "hi_52w":      hi_52w,
            })

            logger.info(
                f"★ S5 후보 [{code}] {name}  "
                f"buyer={buyer_type}({consec_days}일)  "
                f"vol_x={vol_ratio:.2f}  "
                f"52w고={hi_52w:,}  종가={close_today:,}  MA5={int(ma5):,}"
            )

        except Exception as e:
            logger.debug(f"[Momentum배치] [{code}] {name}: {e}")

    # 투자자 캐시 저장
    _save_investor_cache(inv_cache)

    momentum_store.git_commit_push(
        [str(INVESTOR_CACHE_PATH)],
        f"data: S5 투자자캐시 {today} ({len(candidates)}후보)",
    )

    logger.info("══════════════════════════════════════════")
    logger.info(f" S5 Momentum 배치 완료: 후보 {len(candidates)}종목")
    logger.info("══════════════════════════════════════════")

    if notifier and not candidates:
        notifier.notify(f"[S5 Momentum배치] {today}  후보 없음")

    _check_s5_entries(candidates, today, notifier)


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    notifier = Notifier.from_settings(settings)
    logger.info(f"=== S5 Momentum 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ===")
    try:
        kis = KIS(settings)
        run_batch(kis.market, notifier=notifier)
    except Exception as _e:
        logger.exception(f"[Momentum배치] 예외 발생: {_e}")
        notifier.notify(f"[Momentum배치] 배치 비정상 종료\n오류: {_e}")
        sys.exit(1)
