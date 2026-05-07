#!/usr/bin/env python3
"""
MA 이평선 배치 업데이트
장 마감 후(16:30 KST) GitHub Actions에서 실행.
전 종목 일봉 800일치 수집 → MA 계산 → data/ma_data.json 업데이트 → git push
손절 체크(S2 포지션 -3%) + 잔고 현황 텔레그램 알림 포함.

Usage:
    python batch/update_ma.py
"""
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
from data.watchlist import WATCHLIST
import data.ma_store as ma_store

KST            = pytz.timezone("Asia/Seoul")
MA_PERIODS     = [5, 21, 62, 248, 744]
OHLCV_DAYS     = 820   # 800일 + 여유 20일
UPTREND_COLS   = [62, 248, 744]
S2_STOP_LOSS   = 0.03  # 전략2 손절 기준: 매수가 대비 -3%


def _is_uptrend(ma_series: pd.Series, window: int = 20) -> bool:
    """최근 window일 MA에 선형 회귀선을 그어 기울기 > 0 이면 상승 추세"""
    vals = ma_series.dropna().values
    if len(vals) < window:
        return False
    y = vals[-window:]
    x = np.arange(window, dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    return bool(slope > 0)


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

    # 전일 양봉 여부 및 몸통 비율
    prev_open_val  = float(df["open"].iloc[-2]) if "open" in df.columns else 0.0
    prev_close_val = float(close.iloc[-2])
    prev_bullish   = bool(prev_close_val > prev_open_val and prev_open_val > 0)
    candle_body    = (
        abs(prev_close_val - prev_open_val) / prev_close_val
        if prev_close_val > 0 else 0.0
    )

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
        # 20일 선형 회귀 기울기 > 0 이면 상승 추세
        "ma62_uptrend":       _is_uptrend(ma[62]),
        "ma248_uptrend":      _is_uptrend(ma[248]),
        "ma744_uptrend":      _is_uptrend(ma[744]),
        # 전일 캔들 (매수 우선순위용)
        "prev_bullish_candle": prev_bullish,
        "candle_body_ratio":   round(candle_body, 6),
    }


def run_batch(market, account=None, notifier: Notifier = None) -> None:
    from data.holidays import is_market_holiday

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 중복 실행 방지: 오늘 16:00 이후에 이미 실행됐으면 건너뜀 (18:05 재시도 대비)
    # 장중 수동 실행(11시 등)은 차단하지 않음 → 16:30 정규 배치가 정확한 마감가로 재실행
    existing = ma_store.load()
    last_run = existing.get("updated_at_kst", "")   # "2026-05-07 16:35" 형식
    if last_run.startswith(today):
        try:
            last_run_hour = int(last_run.split(" ")[1].split(":")[0])
        except (IndexError, ValueError):
            last_run_hour = 0
        if last_run_hour >= 16:   # 마지막 실행이 16시 이후 = 장 마감 후 정규 배치 완료
            msg = f"[MA배치] {today} 장 마감 후 이미 완료됨 — 중복 실행 건너뜀"
            logger.info(msg)
            if notifier:
                notifier.notify(msg)
            return
        logger.info(f"[MA배치] {today} 장중 실행이었음({last_run}) → 재실행")

    # 휴장일 체크: 증시 휴장일엔 이평선 갱신 불필요
    if is_market_holiday():
        msg = f"[MA배치] {today} 증시 휴장일 — 배치 미실행"
        logger.info(msg)
        if notifier:
            notifier.notify(msg)
        return

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
                f"62↑:{entry['ma62_uptrend']} "
                f"248↑:{entry['ma248_uptrend']} "
                f"744↑:{entry['ma744_uptrend']}{signal}"
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:02d}/{len(WATCHLIST)}] [{code}] {name} 실패: {e}")
            fail += 1

    # 기존 포지션은 유지하고 MA 테이블만 교체
    existing["updated_at"]     = today
    existing["updated_at_kst"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    existing["stocks"]         = stocks_out
    ma_store.save(existing)

    ma_store.git_commit_push(
        [str(ma_store.MA_DATA_PATH)],
        f"data: MA 이평선 업데이트 {today} ({ok}/{len(WATCHLIST)}종목)",
    )

    # 매수 후보 요약 출력 (실제 매수 조건과 동일: prev_bullish_candle 포함)
    positions_now = ma_store.get_positions()
    buy_signals = sorted(
        [
            (c, s) for c, s in stocks_out.items()
            if c not in positions_now
            and s["fully_aligned"] and not s["prev_fully_aligned"]
            and s["ma62_uptrend"] and s["ma248_uptrend"] and s["ma744_uptrend"]
            and s["prev_bullish_candle"]
        ],
        key=lambda x: x[1].get("candle_body_ratio", 0),
        reverse=True,
    )
    sell_signals = [
        (c, s["name"])
        for c, s in stocks_out.items()
        if c in positions_now and s["ma21_below_ma62"]
    ]

    logger.info(f"══════════════════════════════════════════")
    logger.info(f" MA 배치 완료: 성공:{ok} / 실패:{fail} / 부족:{skip}")
    if buy_signals:
        logger.info(f" 내일 매수 후보 ({len(buy_signals)}종목, 상위 표시):")
        for c, s in buy_signals[:5]:
            logger.info(
                f"  [{c}] {s['name']}  몸통:{s['candle_body_ratio']:.2%}  "
                f"62↑:{s['ma62_uptrend']} 248↑:{s['ma248_uptrend']} 744↑:{s['ma744_uptrend']}"
            )
    else:
        logger.info(" 내일 매수 후보 없음")
    if sell_signals:
        logger.info(f" 내일 매도 대상 ({len(sell_signals)}종목):")
        for c, name in sell_signals:
            logger.info(f"  [{c}] {name}")
    else:
        logger.info(" 내일 매도 예정 없음")
    logger.info(f"══════════════════════════════════════════")

    # 전략2 포지션 손절 체크 (-3%) → 신규 손절만 긴급 알림
    _check_s2_stop_loss(stocks_out, notifier)
    # 잔고 현황 + 내일 선정 결과 → 통합 텔레그램 알림
    _notify_daily_summary(stocks_out, buy_signals, sell_signals, account, notifier)


def _check_s2_stop_loss(stocks_out: dict, notifier: Notifier = None) -> None:
    """마감가 기준 전략2 포지션 -3% 손절 체크 → 플래그 설정 + 텔레그램 알림"""
    positions = ma_store.get_positions()
    if not positions:
        return

    newly_flagged = []
    for code, pos in positions.items():
        entry_price = pos.get("entry_price", 0)
        name        = pos.get("name", code)
        if not entry_price:
            continue

        stock       = stocks_out.get(code)
        close_price = stock.get("close", 0) if stock else 0
        if not close_price:
            logger.warning(f"[S2 손절체크] [{code}] {name} — MA배치 데이터 없음, 건너뜀")
            continue

        pnl_rate = (close_price - entry_price) / entry_price

        if pnl_rate <= -S2_STOP_LOSS:
            if not pos.get("stop_loss_pending"):   # 신규 플래그만 알림
                ma_store.set_stop_loss_pending(code, True)
                newly_flagged.append((code, name, entry_price, close_price, pnl_rate))
                logger.warning(
                    f"[S2 손절플래그] [{code}] {name}  "
                    f"매수가:{entry_price:,} → 마감가:{close_price:,}  "
                    f"{pnl_rate*100:+.2f}% → 내일 09:00 시초가 매도"
                )
            else:
                logger.info(
                    f"[S2 손절대기중] [{code}] {name}  "
                    f"마감가:{close_price:,}  {pnl_rate*100:+.2f}% (이미 플래그)"
                )
        else:
            logger.info(
                f"[S2 보유중] [{code}] {name}  "
                f"매수가:{entry_price:,} → 마감가:{close_price:,}  {pnl_rate*100:+.2f}%"
            )

    if newly_flagged and notifier:
        lines = [f"[MA전략] 손절 대상 {len(newly_flagged)}종목 — 내일 09:00 시초가 매도 예약"]
        for code, name, ep, cp, rate in newly_flagged:
            lines.append(
                f"  [{code}] {name}  매수:{ep:,} → 마감:{cp:,}  {rate*100:+.2f}%"
            )
        notifier.notify("\n".join(lines))


def _notify_daily_summary(
    stocks_out: dict,
    buy_signals: list,
    sell_signals: list,
    account,
    notifier: Notifier = None,
) -> None:
    """잔고 현황 + 내일 매수/매도 선정 결과 통합 텔레그램 알림"""
    if not notifier:
        return

    now_str   = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    positions = ma_store.get_positions()

    lines = [f"[MA전략 일일 현황] {now_str}"]

    # ── 잔고 현황 ────────────────────────────────────────
    if account:
        try:
            balance = account.get_balance()
            lines += [
                f"총자산: {balance.total_eval:,}원  현금: {balance.cash:,}원",
                f"평가손익: {balance.total_profit_loss:+,}원  ({balance.total_profit_loss_rate:+.2f}%)",
            ]
        except Exception as e:
            logger.error(f"[잔고현황] 잔고 조회 실패: {e}")
            lines.append(f"잔고 조회 실패: {e}")
    else:
        logger.info("[잔고현황] account 없음 — 잔고 생략")

    # ── 보유 종목 손익 ────────────────────────────────────
    if positions:
        lines.append(f"\n보유 종목 ({len(positions)}종목):")
        for code, pos in positions.items():
            ep      = pos.get("entry_price", 0)
            qty     = pos.get("quantity", 0)
            name    = pos.get("name", code)
            stock   = stocks_out.get(code, {})
            cp      = stock.get("close", ep) or ep
            if ep > 0:
                rate    = (cp - ep) / ep * 100
                amt     = (cp - ep) * qty
                sl_flag = "  ※내일매도" if pos.get("stop_loss_pending") else ""
                lines.append(
                    f"  [{code}] {name}  "
                    f"매수:{ep:,}→마감:{cp:,}  {rate:+.2f}% ({amt:+,}원){sl_flag}"
                )
    else:
        lines.append("\n보유 종목 없음")

    # ── 내일 매수 선정 ────────────────────────────────────
    if buy_signals:
        top_code, top_s = buy_signals[0]
        note = f"  (후보 {len(buy_signals)}종목 중 1위)" if len(buy_signals) > 1 else ""
        lines.append(f"\n내일 매수 예정 (1종목):")
        lines.append(
            f"  [{top_code}] {top_s['name']}  "
            f"양봉몸통:{top_s.get('candle_body_ratio', 0):.2%}"
        )
        if note:
            lines.append(note)
    else:
        lines.append("\n내일 매수 후보 없음")

    # ── 내일 매도 선정 ────────────────────────────────────
    if sell_signals:
        lines.append(f"\n내일 매도 예정 ({len(sell_signals)}종목):")
        for code, name in sell_signals:
            lines.append(f"  [{code}] {name}  ma21<ma62 데드크로스")
    else:
        lines.append("내일 매도 예정 없음")

    notifier.notify("\n".join(lines))


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info(f"=== MA 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ===")
    kis      = KIS(settings)
    notifier = Notifier.from_settings(settings)
    run_batch(kis.market, account=kis.account, notifier=notifier)
