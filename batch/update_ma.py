#!/usr/bin/env python3
"""
MA 이평선 배치 업데이트 (증분 로딩 버전)

[동작 방식]
  최초 실행: 전 종목 820일치 OHLCV 풀 로딩 → data/ohlcv_cache.json 저장
  이후 실행: 캐시에서 신규분(30일)만 추가 로딩 → 합산 후 MA 재계산
  → 1차: ~90분 / 이후: ~10분

[부분 정배열]
  데이터 250일 이상 750일 미만 종목(일부 ETF 등):
  MA5 > MA21 > MA62 > MA248 정배열로 매수/매도 판단 (MA744 조건 면제)

장 마감 후(16:30 KST) GitHub Actions에서 실행.
손절 체크(S2 포지션 -3%) + 잔고 현황 텔레그램 알림 포함.
"""
import json
import sys
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
from data.watchlist import get_s2_watchlist
import data.ma_store as ma_store

KST                = pytz.timezone("Asia/Seoul")
MA_PERIODS         = [5, 21, 62, 248, 744]
OHLCV_DAYS         = 820   # 캐시 없을 때 풀 로딩 일수
INCREMENTAL_DAYS   = 30    # 캐시 있을 때 증분 로딩 일수
MIN_DAYS_FULL      = 750   # MA744 계산 최소 일수
MIN_DAYS_PARTIAL   = 250   # MA248 계산 최소 일수 (부분 정배열)
S2_STOP_LOSS       = 0.07  # 손절 -7%
S2_TAKE_PROFIT     = 0.20  # 익절 기본 +20%
S2_TAKE_PROFIT_EXT = 0.25  # 익절 확장 +25% (21일 이내 +15% 달성 시)
S2_TIME_STOP_DAYS  = 56    # 타임스탑 8주

S2_WATCHLIST = get_s2_watchlist()   # KOSPI200 + KOSDAQ150

OHLCV_CACHE_PATH  = Path("data/ohlcv_cache.json")


# ── OHLCV 캐시 ────────────────────────────────────────────────────────

def load_ohlcv_cache() -> dict:
    if not OHLCV_CACHE_PATH.exists():
        return {}
    with open(OHLCV_CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ohlcv_cache(cache: dict) -> None:
    OHLCV_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OHLCV_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


# ── MA 계산 ────────────────────────────────────────────────────────────

def _is_uptrend(values: list, window: int = 20) -> bool:
    """최근 window개 값에 선형 회귀 → 기울기 > 0 이면 상승 추세"""
    if len(values) < window:
        return False
    y = np.array(values[-window:], dtype=float)
    x = np.arange(window, dtype=float)
    return bool(np.polyfit(x, y, 1)[0] > 0)


def compute_stock_entry(
    name: str, sector: str, last_date: str,
    closes: list, opens: list,
) -> dict:
    """close/open 배열(정수, 과거→최근 순) → MA 지표 dict"""
    n  = len(closes)
    cs = pd.Series(closes, dtype=float)

    has_ma744 = n >= MIN_DAYS_FULL   # True → 완전 정배열 / False → 부분 정배열

    # 사용 가능한 기간만 MA 계산
    ma: dict[int, list] = {}
    for p in MA_PERIODS:
        if n >= p:
            ma[p] = cs.rolling(p).mean().tolist()

    def curr(p):
        return ma[p][-1] if p in ma else None

    def prev(p):
        return ma[p][-2] if p in ma else None

    # 완전 정배열 (MA744 포함)
    if has_ma744:
        fully_aligned      = curr(5) > curr(21) > curr(62) > curr(248) > curr(744)
        prev_fully_aligned = prev(5) > prev(21) > prev(62) > prev(248) > prev(744)
    else:
        fully_aligned = prev_fully_aligned = False

    # 부분 정배열 (MA248까지)
    if all(p in ma for p in [5, 21, 62, 248]):
        partial_aligned      = curr(5) > curr(21) > curr(62) > curr(248)
        prev_partial_aligned = prev(5) > prev(21) > prev(62) > prev(248)
    else:
        partial_aligned = prev_partial_aligned = False

    # 5일선 눌림 정배열: 중장기 배열 유지 + MA5<MA21 (단기 눌림)
    if has_ma744 and all(p in ma for p in [5, 21, 62, 248, 744]):
        near_full_aligned = (
            curr(21) > curr(62) > curr(248) > curr(744)
            and curr(5) < curr(21)
        )
    else:
        near_full_aligned = False

    if not has_ma744 and all(p in ma for p in [5, 21, 62, 248]):
        near_partial_aligned = (
            curr(21) > curr(62) > curr(248)
            and curr(5) < curr(21)
        )
    else:
        near_partial_aligned = False

    # 당일 캔들 (배치 실행일 = 내일 매수 기준 "전일")
    prev_o     = opens[-1]  if len(opens)  >= 1 else 0
    prev_c     = closes[-1] if n >= 1 else 0
    prev_bull  = bool(prev_c > prev_o > 0)
    body_ratio = abs(prev_c - prev_o) / prev_c if prev_c > 0 else 0.0

    return {
        "name":                name,
        "sector":              sector,
        "last_date":           last_date,
        "close":               int(closes[-1]),
        "has_ma744":           has_ma744,
        # 현재 이평선
        "ma5":   curr(5),  "ma21":  curr(21),  "ma62":  curr(62),
        "ma248": curr(248), "ma744": curr(744),
        # 전일 이평선
        "prev_ma5":   prev(5),  "prev_ma21":   prev(21),  "prev_ma62":   prev(62),
        "prev_ma248": prev(248), "prev_ma744":  prev(744),
        # 정배열
        "fully_aligned":       fully_aligned,
        "prev_fully_aligned":  prev_fully_aligned,
        "partial_aligned":     partial_aligned,
        "prev_partial_aligned": prev_partial_aligned,
        # 매도 신호
        "ma21_below_ma62":   (curr(21) < curr(62)) if 21 in ma and 62 in ma else False,
        "ma62_declining_5d": (not _is_uptrend(ma[62], window=5)) if 62 in ma else False,
        # 추세 방향
        "ma62_uptrend":  _is_uptrend(ma[62])  if 62  in ma else False,
        "ma248_uptrend": _is_uptrend(ma[248]) if 248 in ma else False,
        "ma744_uptrend": _is_uptrend(ma[744]) if has_ma744 else False,
        # 정배열 확장
        "near_full_aligned":    near_full_aligned,
        "near_partial_aligned": near_partial_aligned,
        # 전일 캔들
        "prev_bullish_candle": prev_bull,
        "candle_body_ratio":   round(body_ratio, 6),
    }


# ── 배치 메인 ──────────────────────────────────────────────────────────

def run_batch(market, account=None, notifier: Notifier = None, force: bool = False) -> None:
    from data.holidays import is_market_holiday

    today      = datetime.now(KST).strftime("%Y-%m-%d")
    today_date = datetime.now(KST).date()

    # 중복 실행 방지 (18:05 재시도 대비)
    existing = ma_store.load()
    last_run = existing.get("updated_at_kst", "")
    if not force and last_run.startswith(today):
        try:
            last_run_hour = int(last_run.split(" ")[1].split(":")[0])
        except (IndexError, ValueError):
            last_run_hour = 0
        if last_run_hour >= 16:
            msg = f"[MA배치] {today} 장 마감 후 이미 완료됨 — 중복 실행 건너뜀"
            logger.info(msg)
            if notifier:
                notifier.notify(msg)
            return
        logger.info(f"[MA배치] {today} 장중 실행이었음({last_run}) → 재실행")

    # 휴장일 체크
    if is_market_holiday():
        msg = f"[MA배치] {today} 증시 휴장일 — 배치 미실행"
        logger.info(msg)
        if notifier:
            notifier.notify(msg)
        return

    # OHLCV 캐시 로드
    ohlcv_cache = load_ohlcv_cache()
    has_cache   = bool(ohlcv_cache)

    logger.info("══════════════════════════════════════════")
    logger.info(f" MA 배치 업데이트 시작 [{today}]")
    logger.info(
        f" 대상: {len(S2_WATCHLIST)}종목 (KOSPI200 + KOSDAQ150)  "
        f"캐시: {'있음(증분로딩)' if has_cache else '없음(풀로딩)'}"
    )
    logger.info("══════════════════════════════════════════")

    # base_capital 초기화
    if account and not existing.get("base_capital"):
        try:
            _bal = account.get_balance()
            existing["base_capital"] = _bal.total_eval
            ma_store.save(existing)
            logger.info(f"[MA배치] 기준 자산 초기화: {_bal.total_eval:,}원")
        except Exception as _e:
            logger.warning(f"[MA배치] 기준 자산 조회 실패: {_e}")

    throttler  = RateThrottler(max_per_second=9)
    existing   = ma_store.load()
    stocks_out = {}
    ok, fail, skip, cache_hits = 0, 0, 0, 0

    for i, stock in enumerate(S2_WATCHLIST, 1):
        code   = stock["code"]
        name   = stock["name"]
        sector = stock["sector"]

        cached           = ohlcv_cache.get(code, {})
        last_cached_date = cached.get("last_date", "")
        cached_closes    = cached.get("closes", [])
        cached_opens     = cached.get("opens",  [])

        try:
            # ── 캐시 히트: 오늘 이미 저장돼 있으면 API 생략 ──────────────
            if last_cached_date == today and len(cached_closes) >= MIN_DAYS_PARTIAL:
                closes    = cached_closes
                opens     = cached_opens
                last_date = today
                cache_hits += 1
                logger.info(
                    f"[{i:03d}/{len(S2_WATCHLIST)}] [{code}] {name} — 캐시 히트 (API 생략)"
                )

            # ── 캐시 미스: 증분 or 풀 로딩 ──────────────────────────────
            else:
                # 캐시가 있으면 증분(신규분만), 없으면 풀 로딩
                if last_cached_date and len(cached_closes) >= MIN_DAYS_PARTIAL:
                    stale_days = (today_date - datetime.strptime(
                        last_cached_date, "%Y-%m-%d").date()).days
                    fetch_days = max(INCREMENTAL_DAYS, stale_days * 2 + 5)
                else:
                    fetch_days = OHLCV_DAYS

                df = market.get_ohlcv_long(code, days=fetch_days, throttler=throttler)

                if df.empty:
                    logger.warning(
                        f"[{i:03d}/{len(S2_WATCHLIST)}] [{code}] {name} 빈 응답 — 건너뜀"
                    )
                    skip += 1
                    continue

                new_dates  = [d.strftime("%Y-%m-%d") for d in df["date"]]
                new_closes = [int(c) for c in df["close"].tolist()]
                new_opens  = (
                    [int(o) for o in df["open"].tolist()]
                    if "open" in df.columns
                    else [0] * len(df)
                )
                last_date  = new_dates[-1]

                if fetch_days < OHLCV_DAYS and cached_closes:
                    # 캐시와 합산 (중복 날짜 제거)
                    new_mask   = [d > last_cached_date for d in new_dates]
                    add_closes = [c for c, k in zip(new_closes, new_mask) if k]
                    add_opens  = [o for o, k in zip(new_opens,  new_mask) if k]
                    closes     = (cached_closes + add_closes)[-OHLCV_DAYS:]
                    opens      = (cached_opens  + add_opens )[-OHLCV_DAYS:]
                else:
                    closes = new_closes[-OHLCV_DAYS:]
                    opens  = new_opens [-OHLCV_DAYS:]

                ohlcv_cache[code] = {
                    "last_date": last_date,
                    "closes":    closes,
                    "opens":     opens,
                }

            # ── 데이터 충분 여부 확인 ──────────────────────────────────
            if len(closes) < MIN_DAYS_PARTIAL:
                logger.warning(
                    f"[{i:03d}/{len(S2_WATCHLIST)}] [{code}] {name} "
                    f"데이터 부족({len(closes)}일 < {MIN_DAYS_PARTIAL}) — 건너뜀"
                )
                skip += 1
                continue

            # ── MA 계산 ───────────────────────────────────────────────
            entry = compute_stock_entry(name, sector, last_date, closes, opens)
            stocks_out[code] = entry

            # 로그
            has744  = entry["has_ma744"]
            aligned = entry["fully_aligned"] if has744 else entry["partial_aligned"]
            signal  = ""
            if has744 and entry["fully_aligned"] and not entry["prev_fully_aligned"]:
                signal = " ★정배열첫날"
            elif not has744 and entry["partial_aligned"] and not entry["prev_partial_aligned"]:
                signal = " ★부분정배열첫날"
            elif entry["ma21_below_ma62"]:
                signal = " ⚠ma21<ma62"

            logger.info(
                f"[{i:03d}/{len(S2_WATCHLIST)}] [{code}] {name:12s} "
                f"{'전체' if has744 else 'MA248'}정배열:{str(aligned):5s} "
                f"62↑:{entry['ma62_uptrend']} "
                f"248↑:{entry['ma248_uptrend']}"
                + (f" 744↑:{entry['ma744_uptrend']}" if has744 else "")
                + signal
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:03d}/{len(S2_WATCHLIST)}] [{code}] {name} 실패: {e}")
            fail += 1

    logger.info(
        f" 완료: 성공:{ok} / 실패:{fail} / 데이터부족:{skip} / 캐시히트:{cache_hits}"
    )

    # OHLCV 캐시 저장 (다음 배치에서 증분 로딩에 사용)
    save_ohlcv_cache(ohlcv_cache)

    # MA 데이터 저장
    existing["updated_at"]     = today
    existing["updated_at_kst"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    existing["stocks"]         = stocks_out
    ma_store.save(existing)

    ma_store.git_commit_push(
        [str(OHLCV_CACHE_PATH), str(ma_store.MA_DATA_PATH)],
        f"data: MA 이평선 업데이트 {today} ({ok}/{len(S2_WATCHLIST)}종목, 캐시히트:{cache_hits})",
    )

    # 매수/매도 후보 요약
    positions_now = ma_store.get_positions()
    buy_signals = sorted(
        [
            (c, s) for c, s in stocks_out.items()
            if c not in positions_now and _is_buy_signal(s)
        ],
        key=lambda x: x[1].get("candle_body_ratio", 0),
        reverse=True,
    )
    sell_signals = [
        (c, s["name"])
        for c, s in stocks_out.items()
        if c in positions_now and s["ma21_below_ma62"] and s.get("ma62_declining_5d")
    ]

    logger.info("══════════════════════════════════════════")
    logger.info(f" MA 배치 완료: 성공:{ok} / 실패:{fail} / 데이터부족:{skip}")
    if buy_signals:
        logger.info(f" 내일 매수 후보 ({len(buy_signals)}종목, 상위 표시):")
        for c, s in buy_signals[:5]:
            tag = "(전체정배열)" if s["has_ma744"] else "(MA248정배열)"
            logger.info(
                f"  [{c}] {s['name']}  {tag}  몸통:{s.get('candle_body_ratio', 0):.2%}  "
                f"62↑:{s['ma62_uptrend']} 248↑:{s['ma248_uptrend']}"
                + (f" 744↑:{s['ma744_uptrend']}" if s["has_ma744"] else "")
            )
    else:
        logger.info(" 내일 매수 후보 없음")
    if sell_signals:
        logger.info(f" 내일 매도 대상 ({len(sell_signals)}종목):")
        for c, sname in sell_signals:
            logger.info(f"  [{c}] {sname}  ma21<ma62 & ma62하락추세")
    else:
        logger.info(" 내일 매도 예정 없음")
    logger.info("══════════════════════════════════════════")

    _check_s2_exits(stocks_out, notifier)
    _notify_daily_summary(stocks_out, buy_signals, sell_signals, account, notifier)


def _is_buy_signal(s: dict) -> bool:
    """배치 기준 매수 신호 여부 (완전 정배열 or 부분 정배열)"""
    if not s.get("prev_bullish_candle"):
        return False
    if not s.get("ma62_uptrend") or not s.get("ma248_uptrend"):
        return False
    if s.get("has_ma744"):
        return (
            s.get("fully_aligned")
            and not s.get("prev_fully_aligned")
            and s.get("ma744_uptrend")
        )
    else:
        return s.get("partial_aligned") and not s.get("prev_partial_aligned")


# ── S2 매도 플래그 체크 (손절·익절·타임스탑) ──────────────────────────────

def _check_s2_exits(stocks_out: dict, notifier: Notifier = None) -> None:
    positions = ma_store.get_positions()
    if not positions:
        return

    today_str           = datetime.now(KST).strftime("%Y-%m-%d")
    stop_loss_flagged   = []
    take_profit_flagged = []
    time_stop_flagged   = []

    for code, pos in positions.items():
        entry_price = pos.get("entry_price", 0)
        entry_date  = pos.get("entry_date", today_str)
        name        = pos.get("name", code)
        if not entry_price:
            continue
        stock       = stocks_out.get(code)
        close_price = stock.get("close", 0) if stock else 0
        if not close_price:
            logger.warning(f"[S2 익절체크] [{code}] {name} — MA배치 데이터 없음, 건너뜀")
            continue

        pnl_rate  = (close_price - entry_price) / entry_price
        try:
            days_held = (
                datetime.strptime(today_str, "%Y-%m-%d")
                - datetime.strptime(entry_date, "%Y-%m-%d")
            ).days
        except ValueError:
            days_held = 0

        # 고점 갱신 + 조기익절 트리거 체크
        ma_store.update_position_peak(code, close_price, today_str)
        pos         = ma_store.get_positions().get(code, pos)
        early_trig  = pos.get("early_gain_triggered", False)
        target      = S2_TAKE_PROFIT_EXT if early_trig else S2_TAKE_PROFIT

        # 손절 -7%
        if pnl_rate <= -S2_STOP_LOSS:
            if not pos.get("stop_loss_pending"):
                ma_store.set_stop_loss_pending(code, True)
                stop_loss_flagged.append((code, name, entry_price, close_price, pnl_rate))
                logger.warning(
                    f"[S2 손절플래그] [{code}] {name}  "
                    f"매수가:{entry_price:,} → 마감가:{close_price:,}  "
                    f"{pnl_rate*100:+.2f}% ≤ -7% → 내일 09:00 시초가 매도"
                )
            else:
                logger.info(f"[S2 손절대기중] [{code}] {name}  {pnl_rate*100:+.2f}% (이미 플래그)")

        # 익절 +20% / +25%
        elif pnl_rate >= target:
            if not pos.get("take_profit_pending"):
                ma_store.set_take_profit_pending(code, True)
                take_profit_flagged.append((code, name, entry_price, close_price, pnl_rate, target))
                logger.info(
                    f"[S2 익절플래그] [{code}] {name}  "
                    f"매수가:{entry_price:,} → 마감가:{close_price:,}  "
                    f"{pnl_rate*100:+.2f}% ≥ {target:.0%} → 내일 09:00 시초가 매도"
                )
            else:
                logger.info(f"[S2 익절대기중] [{code}] {name}  {pnl_rate*100:+.2f}% (이미 플래그)")

        # 타임스탑 56일
        elif days_held >= S2_TIME_STOP_DAYS:
            if not pos.get("stop_loss_pending") and not pos.get("take_profit_pending") and not pos.get("time_stop_pending"):
                ma_store.set_time_stop_pending(code, True)
                time_stop_flagged.append((code, name, entry_price, close_price, pnl_rate, days_held))
                logger.info(
                    f"[S2 타임스탑플래그] [{code}] {name}  "
                    f"{days_held}일 경과 ({pnl_rate*100:+.2f}%) → 내일 09:00 시초가 매도"
                )
            else:
                logger.info(f"[S2 타임스탑대기중] [{code}] {name}  {days_held}일 경과 (이미 플래그)")
        else:
            ext_mark = " (확장목표)" if early_trig else ""
            logger.info(
                f"[S2 보유중] [{code}] {name}  "
                f"매수가:{entry_price:,} → 마감가:{close_price:,}  "
                f"{pnl_rate*100:+.2f}%  목표:{target:.0%}{ext_mark}  {days_held}일"
            )

    if stop_loss_flagged and notifier:
        lines = [f"[MA전략] 손절 대상 {len(stop_loss_flagged)}종목 — 내일 09:00 시초가 매도 예약"]
        for code, name, ep, cp, rate in stop_loss_flagged:
            lines.append(f"  [{code}] {name}  매수:{ep:,} → 마감:{cp:,}  {rate*100:+.2f}%")
        notifier.notify("\n".join(lines))

    if take_profit_flagged and notifier:
        lines = [f"[MA전략] 익절 대상 {len(take_profit_flagged)}종목 — 내일 09:00 시초가 매도 예약"]
        for code, name, ep, cp, rate, tgt in take_profit_flagged:
            lines.append(f"  [{code}] {name}  매수:{ep:,} → 마감:{cp:,}  {rate*100:+.2f}% ≥ {tgt:.0%}")
        notifier.notify("\n".join(lines))

    if time_stop_flagged and notifier:
        lines = [f"[MA전략] 타임스탑 대상 {len(time_stop_flagged)}종목 — 내일 09:00 시초가 매도 예약"]
        for code, name, ep, cp, rate, days in time_stop_flagged:
            lines.append(f"  [{code}] {name}  매수:{ep:,} → 마감:{cp:,}  {rate*100:+.2f}%  {days}일 경과")
        notifier.notify("\n".join(lines))


# ── 일일 요약 알림 ─────────────────────────────────────────────────────

def _notify_daily_summary(
    stocks_out: dict,
    buy_signals: list,
    sell_signals: list,
    account,
    notifier: Notifier = None,
) -> None:
    if not notifier:
        return

    now_str   = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    positions = ma_store.get_positions()
    lines     = [f"[일일 잔고 현황] {now_str}"]

    balance     = None
    kis_pos_map = {}
    if account:
        try:
            balance     = account.get_balance()
            kis_pos_map = {p.code: p for p in balance.positions}
            lines += [
                f"총자산: {balance.total_eval:,}원  현금: {balance.cash:,}원",
                f"평가손익: {balance.total_profit_loss:+,}원  ({balance.total_profit_loss_rate:+.2f}%)",
            ]
        except Exception as e:
            logger.error(f"[잔고현황] 잔고 조회 실패: {e}")
            lines.append(f"잔고 조회 실패: {e}")

    if positions:
        lines.append(f"\nMA전략 보유 ({len(positions)}종목):")
        for code, pos in positions.items():
            ep      = pos.get("entry_price", 0)
            sl_flag = (
                "  ※내일손절매도" if pos.get("stop_loss_pending")
                else "  ※내일익절매도" if pos.get("take_profit_pending")
                else "  ※내일타임스탑매도" if pos.get("time_stop_pending")
                else ""
            )
            kis_p   = kis_pos_map.get(code)
            if kis_p:
                name = kis_p.name
                cp   = kis_p.current_price or ep
                qty  = kis_p.quantity
                if pos.get("name") != name:
                    logger.info(f"[잔고현황] [{code}] 종목명 수정: {pos.get('name')} → {name}")
                    _data = ma_store.load()
                    if code in _data.get("positions", {}):
                        _data["positions"][code]["name"] = name
                        ma_store.save(_data)
            else:
                name = stocks_out.get(code, {}).get("name") or pos.get("name", code)
                cp   = stocks_out.get(code, {}).get("close", ep) or ep
                qty  = pos.get("quantity", 0)
            if ep > 0:
                rate = (cp - ep) / ep * 100
                amt  = (cp - ep) * qty
                lines.append(
                    f"  [{code}] {name}  "
                    f"매수:{ep:,} → 마감:{cp:,}  {rate:+.2f}% ({amt:+,}원){sl_flag}"
                )
    else:
        lines.append("\n보유 종목 없음")

    if balance:
        from data.shared_slots import count_shared as _count_shared
        base_cap     = ma_store.get_base_capital()
        extra        = ma_store.extra_slots(base_cap, balance.total_eval) if base_cap else 0
        shared_max   = 4 + extra
        s2_n, s3_n, s4_n = _count_shared()
        total_shared = s2_n + s3_n + s4_n
        slot_line    = (
            f"\n슬롯: S1=1개(고정)  S2+S3+S4={total_shared}/{shared_max}개 "
            f"(S2:{s2_n} S3:{s3_n} S4:{s4_n})"
        )
        if base_cap:
            growth_r  = (balance.total_eval - base_cap) / base_cap * 100
            slot_line += f"  (자산증가 {growth_r:+.1f}% / 기준 {base_cap:,}원)"
        lines.append(slot_line)

    if buy_signals:
        top_code, top_s = buy_signals[0]
        tag  = "(전체정배열)" if top_s["has_ma744"] else "(MA248정배열)"
        note = f"  (후보 {len(buy_signals)}종목 중 1위)" if len(buy_signals) > 1 else ""
        lines.append(f"\n내일 매수 예정 (1종목):")
        lines.append(
            f"  [{top_code}] {top_s['name']}  {tag}  "
            f"양봉몸통:{top_s.get('candle_body_ratio', 0):.2%}"
        )
        if note:
            lines.append(note)
    else:
        lines.append("\n내일 매수 후보 없음")

    if sell_signals:
        lines.append(f"\n내일 매도 예정 ({len(sell_signals)}종목):")
        for code, sname in sell_signals:
            lines.append(f"  [{code}] {sname}  ma21<ma62 & ma62하락추세")
    else:
        lines.append("내일 매도 예정 없음")

    notifier.notify("\n".join(lines))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="중복 실행 방지 건너뜀 (장중 데이터 덮어쓰기)")
    args = parser.parse_args()

    settings = get_settings()
    setup_logger(settings.log_level)
    if args.force:
        logger.info("=== MA 배치 [--force: 중복체크 무시] ===")
    logger.info(f"=== MA 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ===")
    notifier = Notifier.from_settings(settings)
    try:
        kis = KIS(settings)
        run_batch(kis.market, account=kis.account, notifier=notifier, force=args.force)
    except Exception as _e:
        logger.exception(f"[MA배치] 예외 발생: {_e}")
        notifier.notify(f"[MA배치] 배치 비정상 종료\n오류: {_e}")
        sys.exit(1)
