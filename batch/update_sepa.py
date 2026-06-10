#!/usr/bin/env python3
"""
SEPA (Specific Entry Point Analysis) 일일 스크리닝 배치
Mark Minervini 방법론

[스크리닝 로직]
  Step 1. 트렌드 템플릿 (T1~T7) — 7개 기술 조건 모두 충족
    T1: 현재가 > MA150 AND MA200
    T2: MA150 > MA200
    T3: MA200 상승 중 (오늘 MA200 > 20거래일 전 MA200)
    T4: MA50 > MA150 AND MA200
    T5: 현재가 > MA50
    T6: 현재가 ≥ 52주 저점 × 1.25 (+25% 이상)
    T7: 현재가 ≥ 52주 고점 × 0.75 (고점 대비 -25% 이내)

  Step 2. 상대강도 (RS) ≥ 70 — 3개월 수익률 유니버스 내 상위 30%

  Step 3. VCP (Volatility Contraction Pattern)
    - 최근 60거래일을 3구간(20일씩)으로 분할
    - 각 구간 가격 변동폭(%) 수축 확인
    - 거래량 수축 확인
    - 피벗 = 마지막 구간(최근 20일) 최고가

  Step 4. 브레이크아웃 확인
    - 당일 종가 > 피벗
    - 당일 거래량 ≥ 50일 평균의 150%
    → breakout_confirmed = True → 다음날 09:00 시장가 매수

[OHLCV 캐시 전략]
  CANSLIM 배치(update_canslim.py)의 canslim_ohlcv_cache.json 우선 재사용.
  SEPA 배치는 CANSLIM 배치(16:40) 이후(17:00)에 실행.

장 마감 후(17:00 KST) GitHub Actions에서 실행.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from utils.throttler import RateThrottler
from kis.factory import KIS
import data.sepa_store as sepa_store
import data.ma_store as ma_store
from data.watchlist import get_s2_watchlist

KST = pytz.timezone("Asia/Seoul")

# CANSLIM 배치가 이미 확보한 캐시 재사용
CANSLIM_OHLCV_CACHE_PATH = Path("data/canslim_ohlcv_cache.json")
SEPA_OHLCV_CACHE_PATH    = Path("data/sepa_ohlcv_cache.json")
OHLCV_DAYS               = 300    # MA200 계산에 충분
INCREMENTAL_DAYS         = 30

KODEX200_CODE    = "069500"   # 시장 추세·RS 기준
RUNNER_THRESHOLD = 0.20       # 러너 기준 고점 수익률

# VCP 파라미터
VCP_BASE_DAYS    = 60    # 전체 base 구간 (3구간 × 20일)
VCP_STAGE_DAYS   = 20    # 각 수축 구간
VCP_MAX_TIGHT    = 8.0   # 타이트 구간 최대 변동폭 %
VCP_MIN_BASE     = 5.0   # 최소 base 변동폭 % (VCP 의미 있으려면)
VCP_VOL_SHRINK   = 0.80  # 거래량 수축 기준 (타이트 < 베이스 × 0.80)
BREAKOUT_VOL_X   = 1.50  # 브레이크아웃 거래량 배수 (50일 평균 × 1.5)


# ── OHLCV 캐시 I/O ───────────────────────────────────────────────────

def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


# ── 트렌드 템플릿 ────────────────────────────────────────────────────

def _check_trend_template(closes: list) -> dict:
    """Minervini 트렌드 템플릿 T1~T7 체크 (MA50·150·200 기반)"""
    n  = len(closes)
    cs = pd.Series(closes, dtype=float)

    if n < 200:
        return {"trend_template": False, "t1": False, "t2": False,
                "t3": False, "t4": False, "t5": False, "t6": False, "t7": False,
                "ma50": 0, "ma150": 0, "ma200": 0}

    ma50  = cs.rolling(50).mean().iloc[-1]
    ma150 = cs.rolling(150).mean().iloc[-1]
    ma200 = cs.rolling(200).mean().iloc[-1]

    # T3: MA200이 20거래일 전보다 높으면 상승 중
    ma200_20d = cs.rolling(200).mean().iloc[-21] if n >= 221 else None
    price = closes[-1]

    # 52주 고저가 (최대 252 거래일)
    lookback = min(252, n)
    hi_52w = max(closes[-lookback:])
    lo_52w = min(closes[-lookback:])

    T1 = bool(price > ma150 and price > ma200)
    T2 = bool(ma150 > ma200)
    T3 = bool(ma200_20d is not None and ma200 > ma200_20d)
    T4 = bool(ma50 > ma150 and ma50 > ma200)
    T5 = bool(price > ma50)
    T6 = bool(lo_52w > 0 and price >= lo_52w * 1.25)
    T7 = bool(hi_52w > 0 and price >= hi_52w * 0.75)

    passed = sum([T1, T2, T3, T4, T5, T6, T7])
    return {
        "trend_template": passed == 7,
        "trend_score":    passed,
        "t1": T1, "t2": T2, "t3": T3, "t4": T4, "t5": T5, "t6": T6, "t7": T7,
        "ma50":  round(ma50,  1),
        "ma150": round(ma150, 1),
        "ma200": round(ma200, 1),
        "hi_52w": int(hi_52w),
        "lo_52w": int(lo_52w),
    }


# ── 상대강도 (RS) ────────────────────────────────────────────────────

def _compute_rs(closes: list, period: int = 63) -> float:
    """종목의 직전 period 거래일 수익률 반환 (RS 랭킹에 사용)"""
    if len(closes) < period + 1:
        return -999.0
    return closes[-1] / closes[-(period + 1)] - 1


def _rank_rs(rs_map: dict) -> dict:
    """{code: rs_return} → {code: rs_percentile (0~100)} 반환"""
    if not rs_map:
        return {}
    sorted_codes = sorted(rs_map, key=lambda c: rs_map[c])
    n = len(sorted_codes)
    return {code: round(rank / n * 100, 1) for rank, code in enumerate(sorted_codes)}


# ── VCP 패턴 감지 ────────────────────────────────────────────────────

def _check_vcp(closes: list, volumes: list) -> dict:
    """
    VCP 패턴 감지 (3단계 수축 기반)
    최근 60거래일을 3구간(early/mid/tight, 각 20일)으로 나눠 변동폭·거래량 수축 확인.
    """
    n = len(closes)
    if n < VCP_BASE_DAYS + 2:   # 60일 base + 오늘 제외 1일 여유
        return {"vcp_detected": False, "pivot": 0}

    # tight 구간은 오늘 종가 제외 (i-20 ~ i-1): 오늘 종가가 피벗을 돌파하는지 체크하기 위함
    # 오늘 종가를 tight에 포함하면 pivot >= today → breakout 조건 항상 False
    early  = closes[-(VCP_BASE_DAYS + 1)       : -(VCP_STAGE_DAYS * 2 + 1)]  # days -61 ~ -42
    mid    = closes[-(VCP_STAGE_DAYS * 2 + 1)  : -(VCP_STAGE_DAYS + 1)]      # days -41 ~ -22
    tight  = closes[-(VCP_STAGE_DAYS + 1)      : -1]                          # days -21 ~ -2 (오늘 제외)

    def range_pct(seg):
        h, l = max(seg), min(seg)
        return (h - l) / h * 100 if h > 0 else 0

    early_range = range_pct(early)
    mid_range   = range_pct(mid)
    tight_range = range_pct(tight)

    # 가격 수축 조건: early > mid > tight (단계별 감소)
    price_contracting = (early_range > mid_range > 0 and mid_range > tight_range)

    # 거래량 수축 (오늘 제외한 구간 기준)
    vol_contracting = True
    if len(volumes) >= VCP_BASE_DAYS + 1:
        vol_early = sum(volumes[-(VCP_BASE_DAYS + 1) : -(VCP_STAGE_DAYS * 2 + 1)]) / VCP_STAGE_DAYS
        vol_tight = sum(volumes[-(VCP_STAGE_DAYS + 1) : -1]) / VCP_STAGE_DAYS
        vol_contracting = (vol_tight < vol_early * VCP_VOL_SHRINK)

    pivot = int(max(tight))

    # 브레이크아웃: 당일 종가가 피벗 돌파
    current_price = closes[-1]
    vol_avg_50d   = sum(volumes[-51:-1]) / 50 if len(volumes) >= 51 else 0
    vol_today     = volumes[-1] if volumes else 0
    vol_breakout  = (vol_today >= vol_avg_50d * BREAKOUT_VOL_X) if vol_avg_50d > 0 else False

    vcp_detected = (
        price_contracting
        and tight_range <= VCP_MAX_TIGHT
        and early_range >= VCP_MIN_BASE
        and vol_contracting
    )
    breakout_confirmed = vcp_detected and current_price > pivot and vol_breakout

    return {
        "vcp_detected":       vcp_detected,
        "pivot":              pivot,
        "early_range_pct":    round(early_range, 1),
        "mid_range_pct":      round(mid_range, 1),
        "tight_range_pct":    round(tight_range, 1),
        "vol_contracting":    vol_contracting,
        "breakout_confirmed": breakout_confirmed,
        "breakout_vol_x":     round(vol_today / vol_avg_50d, 2) if vol_avg_50d > 0 else 0,
    }


# ── 시장 추세 ────────────────────────────────────────────────────────

def _check_market(kodex200_closes: list) -> bool:
    """KODEX200 MA50 > MA150 → 시장 상승 추세"""
    if len(kodex200_closes) < 150:
        return False
    ma50  = sum(kodex200_closes[-50:])  / 50
    ma150 = sum(kodex200_closes[-150:]) / 150
    return ma50 > ma150


# ── S4 포지션 MA이탈 조건 점검 (저녁 배치) ────────────────────────────

def _check_s4_exits(notifier: Notifier = None) -> None:
    """S4 러너 포지션(고점 +20% 이상)의 MA이탈 조건 확인 → ma_exit_pending 플래그 설정."""
    positions = sepa_store.load_positions()
    if not positions:
        return

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    flagged: list = []

    for code, pos in list(positions.items()):
        name        = pos.get("name", code)
        entry_price = pos.get("entry_price", 0)
        if not entry_price:
            continue

        stock_ma = ma_store.get_stock(code)
        if not stock_ma:
            continue

        close = int(stock_ma.get("close", 0))
        if close > 0:
            sepa_store.update_position_peak(code, close, today_str)
            pos = sepa_store.load_positions().get(code, pos)

        peak_price = pos.get("peak_price", entry_price)
        peak_gain  = (peak_price - entry_price) / entry_price

        if peak_gain < RUNNER_THRESHOLD:
            continue

        if stock_ma.get("ma21_below_ma62") and stock_ma.get("ma62_declining_5d"):
            if not pos.get("ma_exit_pending"):
                sepa_store.set_ma_exit_pending(code, True)
                flagged.append((code, name, peak_gain))
                logger.info(
                    f"[S4 MA이탈플래그] [{code}] {name}  "
                    f"고점:{peak_gain:+.1%}  MA이탈 → 내일 09:00 청산 예정"
                )
        elif pos.get("ma_exit_pending"):
            sepa_store.set_ma_exit_pending(code, False)
            logger.info(f"[S4 MA이탈플래그 해제] [{code}] {name}  MA 조건 미충족")

    if flagged and notifier:
        lines = ["[S4 MA이탈] 내일 09:00 청산 예정:"]
        for c, n, pg in flagged:
            lines.append(f"  [{c}] {n}  고점:{pg:+.1%}")
        notifier.notify("\n".join(lines))


# ── 메인 배치 ────────────────────────────────────────────────────────

def run_batch(market, notifier: Notifier = None, force: bool = False) -> None:
    from data.holidays import is_market_holiday

    today = datetime.now(KST).strftime("%Y-%m-%d")

    if not force:
        existing = sepa_store.load_data()
        if existing.get("updated_at", "").startswith(today):
            logger.info(f"[SEPA배치] {today} 이미 완료 — 중복 실행 건너뜀")
            return
        if is_market_holiday():
            logger.info(f"[SEPA배치] {today} 휴장일 — 미실행")
            return
    else:
        logger.info("[SEPA배치] --force 모드: 휴장일·중복 체크 건너뜀")

    universe   = get_s2_watchlist()
    canslim_cache = _load_cache(CANSLIM_OHLCV_CACHE_PATH)
    sepa_cache    = _load_cache(SEPA_OHLCV_CACHE_PATH)

    kodex200_entry  = canslim_cache.get(KODEX200_CODE) or sepa_cache.get(KODEX200_CODE, {})
    kodex200_closes = kodex200_entry.get("closes", [])

    market_uptrend = _check_market(kodex200_closes)
    throttler      = RateThrottler(max_per_second=9)
    today_date     = datetime.now(KST).date()
    n_total        = len(universe)

    logger.info("══════════════════════════════════════════")
    logger.info(f" SEPA 배치 시작 [{today}]  시장추세={market_uptrend}")
    logger.info(f" 대상: {n_total}종목  VCP base={VCP_BASE_DAYS}일 tight≤{VCP_MAX_TIGHT}%")
    logger.info("══════════════════════════════════════════")

    # ── 1pass: OHLCV 확보 + RS 수익률 계산 ─────────────────────────
    ohlcv_map: dict = {}   # code → {closes, volumes}
    rs_raw:    dict = {}   # code → 3개월 수익률

    for i, stock in enumerate(universe, 1):
        code   = stock["code"]
        name   = stock.get("name", code)

        try:
            # 캐시 우선 (CANSLIM → SEPA 전용 → API 순)
            for entry_src in [canslim_cache.get(code), sepa_cache.get(code)]:
                if (entry_src
                        and entry_src.get("last_date") == today
                        and len(entry_src.get("closes", [])) >= 200):
                    closes  = entry_src["closes"]
                    volumes = entry_src.get("volumes", [])
                    break
            else:
                # API 로딩
                last_canslim = (canslim_cache.get(code) or {}).get("last_date", "")
                last_sepa    = (sepa_cache.get(code)    or {}).get("last_date", "")
                last_cached  = max(last_canslim, last_sepa)
                base_closes  = (
                    (canslim_cache.get(code) or {}).get("closes")
                    or (sepa_cache.get(code) or {}).get("closes", [])
                )
                base_vols    = (
                    (canslim_cache.get(code) or {}).get("volumes")
                    or (sepa_cache.get(code) or {}).get("volumes", [])
                )

                if last_cached and len(base_closes) >= 200:
                    stale = (today_date - datetime.strptime(last_cached, "%Y-%m-%d").date()).days
                    fetch_days = max(INCREMENTAL_DAYS, stale * 2 + 5)
                else:
                    fetch_days = OHLCV_DAYS

                df = market.get_ohlcv_long(code, days=fetch_days, throttler=throttler)
                if df.empty:
                    continue

                new_dates  = [d.strftime("%Y-%m-%d") for d in df["date"]]
                new_closes = [int(c) for c in df["close"].tolist()]
                new_vols   = (
                    [int(v) for v in df["volume"].tolist()]
                    if "volume" in df.columns else []
                )
                last_date  = new_dates[-1]

                if fetch_days < OHLCV_DAYS and base_closes:
                    mask    = [d > last_cached for d in new_dates]
                    closes  = (base_closes + [c for c, k in zip(new_closes, mask) if k])[-OHLCV_DAYS:]
                    volumes = (base_vols  + [v for v, k in zip(new_vols,   mask) if k])[-OHLCV_DAYS:]
                else:
                    closes  = new_closes[-OHLCV_DAYS:]
                    volumes = new_vols[-OHLCV_DAYS:]

                sepa_cache[code] = {"last_date": last_date, "closes": closes, "volumes": volumes}

            if len(closes) < 200:
                continue

            ohlcv_map[code] = {"closes": closes, "volumes": volumes, "name": name,
                               "sector": stock.get("sector", "")}
            rs_raw[code]    = _compute_rs(closes)

        except Exception as e:
            logger.debug(f"[SEPA 1pass] [{code}] {name}: {e}")

    logger.info(f"[SEPA배치] 1pass 완료: {len(ohlcv_map)}종목 OHLCV 확보")

    # ── RS 퍼센타일 랭킹 ─────────────────────────────────────────────
    rs_rank = _rank_rs(rs_raw)

    # ── 2pass: 트렌드 템플릿 + RS 필터 + VCP 분석 ────────────────────
    stocks_out:   dict = {}
    pass_tt:      int  = 0
    pass_rs:      int  = 0
    pass_vcp:     int  = 0
    pass_breakout: int = 0

    for code, ohlcv in ohlcv_map.items():
        closes  = ohlcv["closes"]
        volumes = ohlcv["volumes"]
        name    = ohlcv["name"]
        sector  = ohlcv["sector"]

        try:
            # 가격 필터: 1만원 미만 제외
            if closes[-1] < 10_000:
                continue

            # 트렌드 템플릿
            tt = _check_trend_template(closes)
            if tt["trend_template"]:
                pass_tt += 1

            # RS
            rs_score = rs_rank.get(code, 0)
            rs_pass  = rs_score >= 70
            if tt["trend_template"] and rs_pass:
                pass_rs += 1

            # VCP (트렌드 템플릿 통과 시에만 계산)
            vcp = {"vcp_detected": False, "pivot": 0, "breakout_confirmed": False,
                   "early_range_pct": 0, "mid_range_pct": 0, "tight_range_pct": 0,
                   "vol_contracting": False, "breakout_vol_x": 0}
            if tt["trend_template"] and rs_pass:
                vcp = _check_vcp(closes, volumes)
                if vcp["vcp_detected"]:
                    pass_vcp += 1
                if vcp["breakout_confirmed"]:
                    pass_breakout += 1

            stocks_out[code] = {
                "name":   name,
                "sector": sector,
                "close":  int(closes[-1]),
                # 트렌드 템플릿
                **tt,
                # RS
                "rs_score":    rs_score,
                "rs_raw":      round(rs_raw.get(code, 0), 4),
                "rs_pass":     rs_pass,
                # VCP
                **{k: v for k, v in vcp.items()},
            }

            if vcp["breakout_confirmed"]:
                logger.info(
                    f"★ BREAKOUT [{code}] {name:14s}  "
                    f"TT={tt['trend_score']}/7  RS={rs_score:.0f}  "
                    f"pivot={vcp['pivot']:,}  close={closes[-1]:,}  "
                    f"vol_x={vcp['breakout_vol_x']:.1f}  "
                    f"tight={vcp['tight_range_pct']:.1f}%"
                )
            elif vcp["vcp_detected"]:
                logger.info(
                    f"  VCP 대기 [{code}] {name:14s}  "
                    f"TT={tt['trend_score']}/7  RS={rs_score:.0f}  "
                    f"pivot={vcp['pivot']:,}  tight={vcp['tight_range_pct']:.1f}%"
                )

        except Exception as e:
            logger.debug(f"[SEPA 2pass] [{code}] {name}: {e}")

    # 저장
    _save_cache(SEPA_OHLCV_CACHE_PATH, sepa_cache)

    out = {
        "updated_at":     today,
        "market_uptrend": market_uptrend,
        "stocks":         stocks_out,
    }
    sepa_store.save_data(out)

    sepa_store.git_commit_push(
        [str(sepa_store.SEPA_DATA_PATH), str(SEPA_OHLCV_CACHE_PATH)],
        f"data: SEPA 스크리닝 {today} (TT:{pass_tt} RS:{pass_rs} VCP:{pass_vcp} BREAK:{pass_breakout})",
    )

    logger.info("══════════════════════════════════════════")
    logger.info(
        f" SEPA 배치 완료: OHLCV:{len(ohlcv_map)}  TT:{pass_tt}  "
        f"RS≥70:{pass_rs}  VCP:{pass_vcp}  브레이크아웃:{pass_breakout}"
    )
    logger.info("══════════════════════════════════════════")

    if notifier:
        _notify(stocks_out, market_uptrend, pass_breakout, notifier)

    _check_s4_exits(notifier)


def _notify(stocks_out: dict, market_uptrend: bool, n_breakout: int, notifier: Notifier) -> None:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    mstr    = "상승" if market_uptrend else "하락/중립"
    lines   = [f"[SEPA배치] {now_str}  시장:{mstr}"]

    breakouts = [
        (c, s) for c, s in stocks_out.items() if s.get("breakout_confirmed")
    ]
    breakouts.sort(key=lambda x: x[1].get("rs_score", 0), reverse=True)

    if breakouts:
        lines.append(f"브레이크아웃 후보 {len(breakouts)}종목 (내일 09:00 매수):")
        for c, s in breakouts[:5]:
            lines.append(
                f"  [{c}] {s['name']}  {s['close']:,}원  "
                f"RS={s['rs_score']:.0f}  "
                f"pivot={s['pivot']:,}  vol_x={s.get('breakout_vol_x',0):.1f}  "
                f"tight={s.get('tight_range_pct',0):.1f}%"
            )
    else:
        lines.append("브레이크아웃 후보 없음")

    notifier.notify("\n".join(lines))


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    notifier = Notifier.from_settings(settings)
    logger.info(f"=== SEPA 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ===")
    try:
        kis = KIS(settings)
        run_batch(kis.market, notifier=notifier)
    except Exception as _e:
        logger.exception(f"[SEPA배치] 예외 발생: {_e}")
        notifier.notify(f"[SEPA배치] 배치 비정상 종료\n오류: {_e}")
        sys.exit(1)
