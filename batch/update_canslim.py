#!/usr/bin/env python3
"""
CANSLIM 일일 스크리닝 배치

[스크리닝 대상]
  S2/S3 통합 유니버스: KOSPI200 + KOSDAQ150 + ETF (채권·금리 ETF 제외)
  → get_s2_watchlist() 사용 (S2, S3 공통)

[조건 계산]
  N   — 52주 신고가 대비 10% 이내              (OHLCV)
  S   — 당일 거래량 ≥ 50일 평균의 150%        (OHLCV)
  L   — 3개월 수익률 > KOSPI 3개월 수익률      (OHLCV)
  I   — 외국인+기관 30거래일 누적 순매수 > 0   (KIS 투자자동향 API)
  M   — KODEX200 MA20 > MA60                 (ohlcv_cache.json)
  ※ C·A(DART 재무조건) 제외
  score = N+S+L+I+M (최대 5), all_pass = N and S and I and score≥4

[OHLCV 캐시 전략]
  S2 MA배치(update_ma.py)의 ohlcv_cache.json 우선 재사용.
  S3 전용 종목은 canslim_ohlcv_cache.json 에 따로 저장.

장 마감 후(16:40 KST) GitHub Actions에서 실행.
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
import data.canslim_store as canslim_store
import data.ma_store as ma_store
from data.watchlist import get_s2_watchlist

KST = pytz.timezone("Asia/Seoul")

OHLCV_CACHE_PATH         = Path("data/ohlcv_cache.json")
CANSLIM_OHLCV_CACHE_PATH = Path("data/canslim_ohlcv_cache.json")
CANSLIM_OHLCV_DAYS       = 300   # 약 210 거래일 (52주 신고가 계산에 충분)
INCREMENTAL_DAYS         = 30

KODEX200_CODE = "069500"   # KOSPI 프록시


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
    """KODEX200 MA20 > MA60 (중기 시장 상승 추세 — MA5>MA20보다 완화된 기준)"""
    if len(kospi_closes) < 60:
        logger.warning(f"[CANSLIM M] KODEX200 closes 부족 ({len(kospi_closes)}개) — M=False")
        return False
    ma20 = sum(kospi_closes[-20:]) / 20
    ma60 = sum(kospi_closes[-60:]) / 60
    logger.info(f"[CANSLIM M] MA20={ma20:.0f}  MA60={ma60:.0f}  MA20>MA60={ma20 > ma60}  (closes:{len(kospi_closes)}개, 최근:{kospi_closes[-1]:,})")
    return ma20 > ma60


def _check_I(market, code: str, throttler: RateThrottler) -> bool:
    """외국인+기관 최근 20거래일 누적 순매수금액 > 0 (보유수량 증가 추세)"""
    try:
        throttler.acquire()
        history = market.get_investor_trend_history(code, days=20)
        total = sum(h["frgn_net"] + h["orgn_net"] for h in history)
        return total > 0
    except Exception as e:
        logger.debug(f"[CANSLIM] [{code}] I 조건 조회 실패: {e}")
        return False



# ── S3 매수 후보 결정 (저녁 배치) ──────────────────────────────────────────

def _check_s3_entries(
    stocks_out: dict, market_uptrend: bool, today_str: str,
    notifier: Notifier = None,
) -> None:
    """S3 매수 후보를 저녁에 결정 → entry_pending 기록.
    아침 전략은 잔고/슬롯만 확인 후 실행.
    """
    if not market_uptrend:
        canslim_store.set_entry_pending([])
        logger.info("[S3 매수결정] 시장 하락장 → 매수 후보 없음")
        return

    positions   = canslim_store.load_positions()
    ca_data     = canslim_store.load_ca_screened()
    ca_screened = ca_data.get("screened", [])
    ca_codes    = {s["code"] for s in ca_screened if s.get("A")}
    use_ca      = bool(ca_codes)

    if use_ca:
        logger.info(f"[S3 매수결정] A 필터 적용 — {len(ca_codes)}종목")
    else:
        logger.warning("[S3 매수결정] A 스크리닝 없음 — 전체 후보 사용")

    candidates = []
    for code, info in sorted(
        [(c, i) for c, i in stocks_out.items() if i.get("all_pass")],
        key=lambda x: x[1].get("score", 0),
        reverse=True,
    ):
        if code in positions:
            continue
        if use_ca and code not in ca_codes:
            continue
        ca_info = next((s for s in ca_screened if s["code"] == code), None)
        ca_tag  = ""
        if ca_info:
            ca_tag = (" C+A" if (ca_info.get("C") and ca_info.get("A"))
                      else (" C" if ca_info.get("C") else " A"))
        candidates.append({
            "code":   code,
            "name":   info["name"],
            "score":  info.get("score", 0),
            "ca_tag": ca_tag,
            "date":   today_str,
        })

    canslim_store.set_entry_pending(candidates)

    if candidates:
        logger.info(f"[S3 매수결정] {len(candidates)}종목 → entry_pending 설정")
        for c in candidates:
            logger.info(f"  [{c['code']}] {c['name']}{c['ca_tag']}")
        if notifier:
            lines = [f"[S3 매수대기] 내일 09:00 매수 예정 {len(candidates)}종목:"]
            for c in candidates:
                lines.append(f"  [{c['code']}] {c['name']}{c['ca_tag']}")
            notifier.notify("\n".join(lines))
    else:
        logger.info("[S3 매수결정] 후보 없음")


# ── 메인 배치 ─────────────────────────────────────────────────────────

def run_batch(market, notifier: Notifier = None, force: bool = False) -> None:
    from data.holidays import is_market_holiday

    today      = datetime.now(KST).strftime("%Y-%m-%d")
    today_date = datetime.now(KST).date()

    # 중복 실행 방지 (--force 시 건너뜀)
    if not force:
        existing_out = canslim_store.load_data()
        if existing_out.get("updated_at", "").startswith(today):
            logger.info(f"[CANSLIM배치] {today} 이미 완료 — 중복 실행 건너뜀")
            return

        if is_market_holiday():
            logger.info(f"[CANSLIM배치] {today} 휴장일 — 미실행")
            return
    else:
        logger.info(f"[CANSLIM배치] --force 모드: 휴장일·중복 체크 건너뜀")

    # ── 스크리닝 대상: S2/S3 통합 유니버스 ────────────────────────────
    universe = get_s2_watchlist()
    logger.info(f"[CANSLIM배치] 스크리닝 대상: {len(universe)}종목 (KOSPI200+KOSDAQ150+ETF)")

    # S2 OHLCV 캐시 (update_ma.py가 이미 최신화)
    s2_cache      = _load_cache(OHLCV_CACHE_PATH)
    canslim_cache = _load_cache(CANSLIM_OHLCV_CACHE_PATH)

    # 캐시 신선도 기준: 최근 7일 이내면 재사용 (비거래일·주말 실행 대응)
    from datetime import timedelta
    cache_cutoff = (today_date - timedelta(days=7)).strftime("%Y-%m-%d")

    # KODEX200 (M·L 조건용)
    kospi_closes: list = s2_cache.get(KODEX200_CODE, {}).get("closes", [])
    if not kospi_closes:
        logger.warning("[CANSLIM배치] KODEX200 OHLCV 없음 — M·L 조건 비활성화")

    M_global = _check_M(kospi_closes)

    throttler  = RateThrottler(max_per_second=9)
    stocks_out: dict = {}
    ok, fail, skip = 0, 0, 0
    today_date = datetime.now(KST).date()
    n_total    = len(universe)

    logger.info("══════════════════════════════════════════")
    logger.info(f" CANSLIM 배치 시작 [{today}]  시장추세(M)={M_global}")
    logger.info(f" 대상: {n_total}종목")
    logger.info("══════════════════════════════════════════")

    for i, stock in enumerate(universe, 1):
        code   = stock["code"]
        name   = stock.get("name", code)
        sector = stock.get("sector", "")

        try:
            # ── OHLCV 확보 ────────────────────────────────────────
            s2_entry      = s2_cache.get(code, {})
            canslim_entry = canslim_cache.get(code, {})

            s2_date = s2_entry.get("last_date", "")
            cl_date = canslim_entry.get("last_date", "")
            if s2_date >= cache_cutoff and len(s2_entry.get("closes", [])) >= 60:
                closes     = s2_entry["closes"]
                volumes    = s2_entry.get("volumes") or canslim_entry.get("volumes", [])
                from_cache = "S2"
            elif cl_date >= cache_cutoff and len(canslim_entry.get("closes", [])) >= 60:
                closes    = canslim_entry["closes"]
                volumes   = canslim_entry.get("volumes", [])
                from_cache = "canslim"
            else:
                last_cached   = canslim_entry.get("last_date", "") or s2_entry.get("last_date", "")
                cached_closes = canslim_entry.get("closes") or s2_entry.get("closes", [])

                if last_cached and len(cached_closes) >= 60:
                    stale_days = (
                        today_date - datetime.strptime(last_cached, "%Y-%m-%d").date()
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

            # ── 가격 필터: 1만원 미만 제외 ────────────────────────
            current_price = closes[-1]
            if current_price < 10_000:
                logger.debug(
                    f"[{i:03d}/{n_total}] [{code}] {name}  "
                    f"가격={current_price:,}원 < 10,000원 → 제외"
                )
                skip += 1
                continue

            # ── N·S·L·I·M 조건 ────────────────────────────────────
            N, hi_52w    = _check_N(closes)
            S, vol_ratio = _check_S(volumes) if volumes else (False, 0.0)
            L, rs_3m     = _check_L(closes, kospi_closes)
            I            = _check_I(market, code, throttler)
            M            = M_global

            score    = sum([N, S, L, I, M])
            all_pass = (N and S and I and score >= 4)

            stocks_out[code] = {
                "name":      name,
                "sector":    sector,
                "N": N, "S": S, "L": L, "I": I, "M": M,
                "score":     score,
                "all_pass":  all_pass,
                "close":     int(closes[-1]),
                "hi_52w":    hi_52w,
                "vol_ratio": vol_ratio,
                "rs_3m":     rs_3m,
            }

            signal = " ★ALL_PASS" if all_pass else ""
            logger.info(
                f"[{i:03d}/{n_total}] [{code}] {name:14s}  "
                f"N:{int(N)} S:{int(S)} L:{int(L)} I:{int(I)} M:{int(M)}  "
                f"score={score}/5  ({from_cache}){signal}"
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:03d}/{n_total}] [{code}] {name} 실패: {e}")
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
        f"data: CANSLIM 스크리닝 {today} ({ok}/{n_total}종목)",
    )

    # 매수 후보 로그
    all_pass_list = sorted(
        [(c, s) for c, s in stocks_out.items() if s["all_pass"]],
        key=lambda x: x[1]["score"], reverse=True,
    )

    logger.info("══════════════════════════════════════════")
    logger.info(f" CANSLIM(N·S·L·I·M) 완료: 성공:{ok} / 실패:{fail} / 건너뜀:{skip}")
    if all_pass_list:
        logger.info(f" 내일 매수 후보 (ALL_PASS 5/5) — {len(all_pass_list)}종목:")
        for c, s in all_pass_list[:20]:
            logger.info(
                f"  [{c}] {s['name']}  close={s['close']:,}  "
                f"vol_ratio={s['vol_ratio']:.2f}x  rs_3m={s['rs_3m']:+.1%}"
            )
    else:
        logger.info(" 내일 매수 후보 없음 (ALL_PASS 없음)")
    logger.info("══════════════════════════════════════════")

    if notifier:
        _notify(all_pass_list, M_global, ok, fail, notifier)

    _check_s3_entries(stocks_out, M_global, today, notifier)


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
        for c, s in all_pass_list[:20]:
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
