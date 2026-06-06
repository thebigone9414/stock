#!/usr/bin/env python3
"""
CANSLIM 일일 스크리닝 배치

[동작]
  CANSLIM 유니버스 종목에 대해 7개 조건 계산 → data/canslim_data.json 저장

  C — 최근 분기 EPS YoY +25% 이상     (dart_data.json)
  A — 최근 3년 연간 EPS CAGR +15% 이상 (dart_data.json)
  N — 52주 신고가 대비 10% 이내         (OHLCV)
  S — 당일 거래량 ≥ 50일 평균의 150%   (OHLCV)
  L — 3개월 수익률 > KOSPI 3개월 수익률 (OHLCV)
  I — 외국인+기관 순매수 > 0           (KIS 투자자동향 API)
  M — KODEX200 MA5 > MA20             (ohlcv_cache.json)

[OHLCV 캐시 전략]
  - S2 MA배치(update_ma.py)가 이미 ohlcv_cache.json을 최신화
  - 해당 캐시에 있는 종목은 API 호출 생략
  - KOSDAQ 전용 종목(S2 watchlist에 없음)은 canslim_ohlcv_cache.json에 따로 저장

장 마감 후(16:40 KST) GitHub Actions에서 실행.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from utils.throttler import RateThrottler
from kis.factory import KIS
from data.canslim_universe import CANSLIM_UNIVERSE
import data.dart_store as dart_store
import data.canslim_store as canslim_store

KST = pytz.timezone("Asia/Seoul")

OHLCV_CACHE_PATH         = Path("data/ohlcv_cache.json")
CANSLIM_OHLCV_CACHE_PATH = Path("data/canslim_ohlcv_cache.json")
CANSLIM_OHLCV_DAYS       = 300   # 약 210 거래일 (52주 신고가 계산에 충분)
INCREMENTAL_DAYS         = 30

KODEX200_CODE = "069500"          # KOSPI 프록시


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


# ── 조건별 계산 헬퍼 ─────────────────────────────────────────────────

def _check_C(corp: dict) -> bool:
    """최근 분기 EPS YoY +25% 이상"""
    if not corp:
        return False
    qeps = corp.get("quarterly_eps", [])
    if len(qeps) < 2:
        return False
    # 최신 분기와 동일 분기 전년도 비교
    latest = next((q for q in qeps if q.get("eps") is not None), None)
    if not latest or not latest["eps"]:
        return False
    qnum, qyear = latest["quarter"], latest["year"]
    prev = next(
        (q for q in qeps if q["year"] == qyear - 1 and q["quarter"] == qnum and q.get("eps") is not None),
        None,
    )
    if not prev or not prev["eps"] or prev["eps"] <= 0:
        return False
    growth = (latest["eps"] - prev["eps"]) / abs(prev["eps"])
    return growth >= 0.25


def _check_A(corp: dict) -> bool:
    """최근 3년 연간 EPS CAGR +15% 이상"""
    if not corp:
        return False
    aeps = [a for a in corp.get("annual_eps", []) if a.get("eps") is not None and a["eps"] > 0]
    if len(aeps) < 2:
        return False
    aeps_sorted = sorted(aeps, key=lambda x: x["year"], reverse=True)
    latest = aeps_sorted[0]["eps"]
    # 최대 3년 전 데이터 사용
    oldest = aeps_sorted[min(2, len(aeps_sorted) - 1)]
    years  = aeps_sorted[0]["year"] - oldest["year"]
    if years <= 0:
        return False
    cagr = (latest / oldest["eps"]) ** (1 / years) - 1
    return cagr >= 0.15


def _check_N(closes: list) -> tuple:
    """52주 신고가 대비 10% 이내 여부 + 신고가"""
    n = len(closes)
    if n < 50:
        return False, 0
    window   = min(252, n)
    hi_52w   = max(closes[-window:])
    close    = closes[-1]
    in_range = close / hi_52w >= 0.90 if hi_52w > 0 else False
    return in_range, int(hi_52w)


def _check_S(volumes: list) -> tuple:
    """당일 거래량 ≥ 50일 평균의 150%"""
    if len(volumes) < 52:
        return False, 0.0
    vol_avg = sum(volumes[-51:-1]) / 50    # 전일 기준 50일 평균
    vol_today = volumes[-1]
    ratio   = vol_today / vol_avg if vol_avg > 0 else 0.0
    return ratio >= 1.50, round(ratio, 3)


def _check_L(stock_closes: list, kospi_closes: list) -> tuple:
    """3개월(63거래일) 수익률 > KOSPI 3개월 수익률"""
    if len(stock_closes) < 64 or len(kospi_closes) < 64:
        return False, 0.0
    stock_rs  = stock_closes[-1] / stock_closes[-64] - 1
    kospi_rs  = kospi_closes[-1] / kospi_closes[-64] - 1
    return bool(stock_rs > kospi_rs), round(stock_rs, 4)


def _check_M(kospi_closes: list) -> bool:
    """KODEX200 MA5 > MA20 (단기 시장 상승 추세)"""
    if len(kospi_closes) < 20:
        return False
    ma5  = sum(kospi_closes[-5:])  / 5
    ma20 = sum(kospi_closes[-20:]) / 20
    return ma5 > ma20


def _check_I(market, code: str, throttler: RateThrottler) -> bool:
    """외국인 + 기관 순매수금액 합산 > 0"""
    try:
        throttler.acquire()
        iv   = market.get_investor_trend(code)
        frgn = int(str(iv.get("frgn_ntby_tr_pbmn", "0")).replace(",", "") or "0")
        orgn = int(str(iv.get("orgn_ntby_tr_pbmn", "0")).replace(",", "") or "0")
        return (frgn + orgn) > 0
    except Exception as e:
        logger.debug(f"[CANSLIM] [{code}] I 조건 조회 실패: {e}")
        return False


# ── 메인 배치 ─────────────────────────────────────────────────────────

def run_batch(market, notifier: Notifier = None) -> None:
    from data.holidays import is_market_holiday

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 중복 실행 방지
    existing_out = canslim_store.load_data()
    if existing_out.get("updated_at", "").startswith(today):
        logger.info(f"[CANSLIM배치] {today} 이미 완료 — 중복 실행 건너뜀")
        return

    if is_market_holiday():
        logger.info(f"[CANSLIM배치] {today} 휴장일 — 미실행")
        return

    dart_data = dart_store.load()

    # S2 OHLCV 캐시 (update_ma.py가 이미 최신화)
    s2_cache      = _load_cache(OHLCV_CACHE_PATH)
    canslim_cache = _load_cache(CANSLIM_OHLCV_CACHE_PATH)

    # KODEX200 (M·L 조건용)
    kospi_closes: list = s2_cache.get(KODEX200_CODE, {}).get("closes", [])
    if not kospi_closes:
        logger.warning(f"[CANSLIM배치] KODEX200 OHLCV 없음 — M·L 조건 비활성화")

    M_global = _check_M(kospi_closes)

    throttler = RateThrottler(max_per_second=9)
    stocks_out: dict = {}
    ok, fail, skip = 0, 0, 0
    today_date = datetime.now(KST).date()

    logger.info("══════════════════════════════════════════")
    logger.info(f" CANSLIM 배치 시작 [{today}]  시장추세(M)={M_global}")
    logger.info(f" 대상: {len(CANSLIM_UNIVERSE)}종목")
    logger.info("══════════════════════════════════════════")

    for i, stock in enumerate(CANSLIM_UNIVERSE, 1):
        code   = stock["code"]
        name   = stock["name"]
        sector = stock["sector"]

        try:
            # ── OHLCV 확보 ────────────────────────────────────────
            # S2 캐시 우선, 없으면 canslim 전용 캐시, 없으면 API 호출
            s2_entry      = s2_cache.get(code, {})
            canslim_entry = canslim_cache.get(code, {})

            if s2_entry.get("last_date") == today and len(s2_entry.get("closes", [])) >= 60:
                closes  = s2_entry["closes"]
                volumes = s2_entry.get("volumes", [])
                last_date = today
                from_cache = "S2"
            elif canslim_entry.get("last_date") == today and len(canslim_entry.get("closes", [])) >= 60:
                closes  = canslim_entry["closes"]
                volumes = canslim_entry.get("volumes", [])
                last_date = today
                from_cache = "canslim"
            else:
                # API 호출
                last_cached = canslim_entry.get("last_date", "") or s2_entry.get("last_date", "")
                cached_closes = canslim_entry.get("closes") or s2_entry.get("closes", [])

                if last_cached and len(cached_closes) >= 60:
                    stale_days = (
                        today_date
                        - datetime.strptime(last_cached, "%Y-%m-%d").date()
                    ).days
                    fetch_days = max(INCREMENTAL_DAYS, stale_days * 2 + 5)
                else:
                    fetch_days = CANSLIM_OHLCV_DAYS

                df = market.get_ohlcv_long(code, days=fetch_days, throttler=throttler)
                if df.empty:
                    skip += 1
                    logger.debug(f"[{i:03d}] [{code}] {name} OHLCV 빈 응답")
                    continue

                new_dates  = [d.strftime("%Y-%m-%d") for d in df["date"]]
                new_closes = [int(c) for c in df["close"].tolist()]
                new_vols   = (
                    [int(v) for v in df["volume"].tolist()]
                    if "volume" in df.columns else []
                )
                last_date  = new_dates[-1]

                if fetch_days < CANSLIM_OHLCV_DAYS and cached_closes:
                    new_mask = [d > last_cached for d in new_dates]
                    add_c    = [c for c, k in zip(new_closes, new_mask) if k]
                    add_v    = [v for v, k in zip(new_vols,   new_mask) if k]
                    closes   = (cached_closes + add_c)[-CANSLIM_OHLCV_DAYS:]
                    volumes  = (canslim_entry.get("volumes", []) + add_v)[-CANSLIM_OHLCV_DAYS:]
                else:
                    closes  = new_closes[-CANSLIM_OHLCV_DAYS:]
                    volumes = new_vols[-CANSLIM_OHLCV_DAYS:]

                canslim_cache[code] = {
                    "last_date": last_date,
                    "closes":    closes,
                    "volumes":   volumes,
                }
                from_cache = "API"

            if len(closes) < 60:
                skip += 1
                continue

            # ── CANSLIM 조건 계산 ─────────────────────────────────
            corp  = dart_data.get("corps", {}).get(code, {})
            C     = _check_C(corp)
            A     = _check_A(corp)
            N, hi_52w = _check_N(closes)
            S, vol_ratio = _check_S(volumes) if volumes else (False, 0.0)
            L, rs_3m  = _check_L(closes, kospi_closes)
            I         = _check_I(market, code, throttler)
            M         = M_global

            score    = sum([C, A, N, S, L, I, M])
            all_pass = (score == 7)

            stocks_out[code] = {
                "name":     name,
                "sector":   sector,
                "C": C, "A": A, "N": N, "S": S, "L": L, "I": I, "M": M,
                "score":    score,
                "all_pass": all_pass,
                "close":    int(closes[-1]),
                "hi_52w":   hi_52w,
                "vol_ratio": vol_ratio,
                "rs_3m":    rs_3m,
            }

            signal = " ★ALL_PASS" if all_pass else ""
            logger.info(
                f"[{i:03d}/{len(CANSLIM_UNIVERSE)}] [{code}] {name:14s}  "
                f"C:{int(C)} A:{int(A)} N:{int(N)} S:{int(S)} L:{int(L)} I:{int(I)} M:{int(M)}  "
                f"score={score}/7  ({from_cache}){signal}"
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:03d}/{len(CANSLIM_UNIVERSE)}] [{code}] {name} 실패: {e}")
            fail += 1

    # 저장
    _save_cache(CANSLIM_OHLCV_CACHE_PATH, canslim_cache)

    out = {
        "updated_at":     today,
        "market_uptrend": M_global,
        "stocks":         stocks_out,
    }
    canslim_store.save_data(out)

    canslim_store.git_commit_push(
        [str(canslim_store.CANSLIM_DATA_PATH), str(CANSLIM_OHLCV_CACHE_PATH)],
        f"data: CANSLIM 스크리닝 {today} ({ok}/{len(CANSLIM_UNIVERSE)}종목)",
    )

    # 매수 후보 로그
    all_pass_list = sorted(
        [(c, s) for c, s in stocks_out.items() if s["all_pass"]],
        key=lambda x: x[1]["score"], reverse=True,
    )

    logger.info("══════════════════════════════════════════")
    logger.info(f" CANSLIM 완료: 성공:{ok} / 실패:{fail} / 건너뜀:{skip}")
    if all_pass_list:
        logger.info(f" 내일 매수 후보 (ALL_PASS) — {len(all_pass_list)}종목:")
        for c, s in all_pass_list[:5]:
            logger.info(
                f"  [{c}] {s['name']}  close={s['close']:,}  "
                f"vol_ratio={s['vol_ratio']:.2f}x  rs_3m={s['rs_3m']:+.1%}"
            )
    else:
        logger.info(" 내일 매수 후보 없음 (ALL_PASS 없음)")
    logger.info("══════════════════════════════════════════")

    if notifier:
        _notify(all_pass_list, M_global, ok, fail, notifier)


def _notify(
    all_pass_list: list,
    market_uptrend: bool,
    ok: int, fail: int,
    notifier: Notifier,
) -> None:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    mstr    = "상승" if market_uptrend else "하락/중립"
    lines   = [f"[CANSLIM배치] {now_str}  시장:{mstr}  처리:{ok}종목"]
    if all_pass_list:
        lines.append(f"ALL_PASS 후보 {len(all_pass_list)}종목:")
        for c, s in all_pass_list[:5]:
            lines.append(
                f"  [{c}] {s['name']}  {s['close']:,}원  "
                f"거래량:{s['vol_ratio']:.1f}x  RS:{s['rs_3m']:+.1%}"
            )
    else:
        lines.append("ALL_PASS 후보 없음")
    notifier.notify("\n".join(lines))


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    notifier = Notifier.from_settings(settings)
    logger.info(f"=== CANSLIM 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ===")
    try:
        kis = KIS(settings)
        run_batch(kis.market, notifier=notifier)
    except Exception as _e:
        logger.exception(f"[CANSLIM배치] 예외 발생: {_e}")
        notifier.notify(f"[CANSLIM배치] 배치 비정상 종료\n오류: {_e}")
        sys.exit(1)
