#!/usr/bin/env python3
"""
S2 MA 이평선 전략 백테스트

신호: i일 종가 기준  →  체결: i+1일 시가 (look-ahead bias 없음)
데이터: data/ohlcv_cache.json (820일)
시뮬레이션 구간: MA248 최초 유효일(~250일) 이후 → 약 2.3년

Usage:
    python backtest/run_s2.py
    python backtest/run_s2.py --capital 50000000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 전략 파라미터 (strategies/ma_cross.py 와 동일) ──────────────────────
MAX_SLOTS          = 4       # S2 단독 슬롯 (백테스트 단순화)
SLOT_RATIO         = 0.20    # 슬롯당 총자산 비율
STOP_LOSS          = 0.07    # 손절 -7%
TAKE_PROFIT        = 0.20    # 익절 기본 +20%
TAKE_PROFIT_EXT    = 0.25    # 익절 확장 +25%
EARLY_GAIN_TRIGGER = 0.15    # 조기익절 트리거 +15%
EARLY_GAIN_DAYS    = 21      # 조기익절 판단 기간 (캘린더일)
TIME_STOP_DAYS     = 56      # 타임스탑 (캘린더일 ≈ 약 40 영업일)
MA_TREND_LOOKBACK  = 5       # 이평선 상승추세 판단 기간 (영업일)

# ── 거래비용 ─────────────────────────────────────────────────────────────
BUY_FEE  = 0.00015           # 매수 수수료 0.015%
SELL_FEE = 0.00015 + 0.002   # 매도 수수료 0.015% + 증권거래세 0.2%

MIN_DATA_DAYS = 820           # 캐시에서 로드할 최소 일수


# ── 데이터 로드 ──────────────────────────────────────────────────────────
def _load_data() -> dict:
    path = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"
    if not path.exists():
        print(f"[오류] {path} 파일 없음 — ma-batch 먼저 실행 필요")
        sys.exit(1)
    raw = json.loads(path.read_text(encoding="utf-8"))

    stocks = {}
    for code, data in raw.items():
        closes = data.get("closes", [])
        opens  = data.get("opens",  [])
        if len(closes) >= MIN_DATA_DAYS and len(opens) >= MIN_DATA_DAYS:
            stocks[code] = {
                "closes": np.array(closes[-MIN_DATA_DAYS:], dtype=float),
                "opens":  np.array(opens[-MIN_DATA_DAYS:],  dtype=float),
            }
    return stocks


# ── MA 계산 ──────────────────────────────────────────────────────────────
def _ma(arr: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(arr).rolling(period).mean().values


def _precompute_mas(stocks: dict) -> None:
    for data in stocks.values():
        c = data["closes"]
        data["mas"] = {
            "ma5":   _ma(c, 5),
            "ma21":  _ma(c, 21),
            "ma62":  _ma(c, 62),
            "ma248": _ma(c, 248),
            "ma744": _ma(c, 744),
        }


# ── 신호 판단 ────────────────────────────────────────────────────────────
def _golden_align(mas: dict, i: int) -> bool:
    """완전/부분 정배열 여부 (MA744 없으면 MA248 기준)"""
    m5, m21, m62 = mas["ma5"][i], mas["ma21"][i], mas["ma62"][i]
    m248, m744   = mas["ma248"][i], mas["ma744"][i]
    if np.isnan(m62) or np.isnan(m248):
        return False
    if not np.isnan(m744):
        return m5 > m21 > m62 > m248 > m744
    return m5 > m21 > m62 > m248


def _is_first_golden_day(mas: dict, i: int) -> bool:
    """정배열 첫날: 오늘은 정배열, 전날은 아님"""
    return _golden_align(mas, i) and not _golden_align(mas, i - 1)


def _is_trending_up(mas: dict, i: int) -> bool:
    """MA62, MA248 이평선 상승추세 (5영업일 전보다 높음)"""
    lb = MA_TREND_LOOKBACK
    m62, m62p   = mas["ma62"][i], mas["ma62"][i - lb]
    m248, m248p = mas["ma248"][i], mas["ma248"][i - lb]
    if np.isnan(m62) or np.isnan(m62p):
        return False
    if np.isnan(m248) or np.isnan(m248p):
        return m62 > m62p
    return m62 > m62p and m248 > m248p


def _is_deadcross(mas: dict, i: int) -> bool:
    """MA21 < MA62  AND  MA62 5일 하락추세"""
    m21, m62      = mas["ma21"][i], mas["ma62"][i]
    m62_prev      = mas["ma62"][i - MA_TREND_LOOKBACK]
    if np.isnan(m21) or np.isnan(m62) or np.isnan(m62_prev):
        return False
    return m21 < m62 and m62 < m62_prev


# ── 포트폴리오 시뮬레이션 ────────────────────────────────────────────────
def run_backtest(initial_capital: int = 10_000_000) -> tuple[list, list]:
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")

    print("MA 계산 중...")
    _precompute_mas(stocks)

    capital   = float(initial_capital)
    positions = {}   # code → {entry_i, entry_price, qty, peak, days_held, early_triggered}
    trades    = []
    port_hist = []   # 날짜별 총 평가액

    # MA248이 처음 유효한 날(248) + 전날 비교 가능 + 추세 lookback 여유
    start = 248 + MA_TREND_LOOKBACK + 1
    end   = MIN_DATA_DAYS - 1   # i+1 시가 접근 가능한 마지막 인덱스

    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end-start)/252:.1f}년)\n")

    for i in range(start, end):
        # 현재 포지션 평가액
        pos_val    = sum(stocks[c]["closes"][i] * p["qty"]
                         for c, p in positions.items() if c in stocks)
        total_eval = capital + pos_val

        # ── 매도 신호 체크 (i 종가 기준, i+1 시가로 체결) ────────────
        for code in list(positions):
            if code not in stocks:
                del positions[code]
                continue

            pos   = positions[code]
            cur   = stocks[code]["closes"][i]
            entry = pos["entry_price"]
            gain  = (cur - entry) / entry
            days  = pos["days_held"]

            # 고점 갱신
            if cur > pos["peak"]:
                pos["peak"] = cur

            # 조기익절 트리거
            if (not pos["early_triggered"]
                    and gain >= EARLY_GAIN_TRIGGER
                    and days <= EARLY_GAIN_DAYS):
                pos["early_triggered"] = True

            target = TAKE_PROFIT_EXT if pos["early_triggered"] else TAKE_PROFIT

            reason = None
            if gain <= -STOP_LOSS:
                reason = "손절"
            elif days >= TIME_STOP_DAYS:
                reason = "타임스탑"
            elif gain >= target:
                reason = "익절"
            elif _is_deadcross(stocks[code]["mas"], i):
                reason = "데드크로스"

            if reason:
                sell_px  = stocks[code]["opens"][i + 1]
                received = sell_px * pos["qty"] * (1 - SELL_FEE)
                capital += received
                trades.append({
                    "code":        code,
                    "entry_i":     pos["entry_i"],
                    "exit_i":      i + 1,
                    "entry_price": entry,
                    "exit_price":  sell_px,
                    "qty":         pos["qty"],
                    "pnl_pct":     (sell_px - entry) / entry,
                    "reason":      reason,
                    "days_held":   days,
                })
                del positions[code]

        # ── 매수 신호 체크 ────────────────────────────────────────────
        if len(positions) < MAX_SLOTS:
            slot_budget = total_eval * SLOT_RATIO

            for code, data in stocks.items():
                if len(positions) >= MAX_SLOTS:
                    break
                if code in positions:
                    continue

                mas = data["mas"]

                if not _is_first_golden_day(mas, i):
                    continue
                if data["closes"][i] <= data["opens"][i]:  # 양봉
                    continue
                if not _is_trending_up(mas, i):
                    continue

                buy_px = data["opens"][i + 1]
                if buy_px <= 0:
                    continue

                qty = int(slot_budget / (buy_px * (1 + BUY_FEE)))
                if qty <= 0:
                    continue
                cost = buy_px * qty * (1 + BUY_FEE)
                if cost > capital:
                    qty  = int(capital / (buy_px * (1 + BUY_FEE)))
                    cost = buy_px * qty * (1 + BUY_FEE)
                if qty <= 0:
                    continue

                capital -= cost
                positions[code] = {
                    "entry_i":        i + 1,
                    "entry_price":    buy_px,
                    "qty":            qty,
                    "peak":           buy_px,
                    "days_held":      0,
                    "early_triggered": False,
                }

        # 보유 중 포지션 days_held 증가
        for pos in positions.values():
            pos["days_held"] += 1

        # 포트폴리오 기록
        pos_val = sum(stocks[c]["closes"][i] * p["qty"]
                      for c, p in positions.items() if c in stocks)
        port_hist.append(capital + pos_val)

    return trades, port_hist


# ── 결과 리포트 ──────────────────────────────────────────────────────────
def _report(trades: list, port_hist: list, initial_capital: int) -> None:
    if not port_hist:
        print("시뮬레이션 결과 없음")
        return

    final   = port_hist[-1]
    total_r = (final - initial_capital) / initial_capital
    n_days  = len(port_hist)
    years   = n_days / 252
    cagr    = (final / initial_capital) ** (1 / max(years, 0.01)) - 1

    arr  = np.array(port_hist)
    peak = np.maximum.accumulate(arr)
    dd   = (arr - peak) / peak
    mdd  = float(dd.min())

    sells = trades
    wins  = [t for t in sells if t["pnl_pct"] > 0]
    by_reason: dict = {}
    for t in sells:
        by_reason.setdefault(t["reason"], []).append(t["pnl_pct"])

    W = 57
    print("\n" + "=" * W)
    print(f"  S2 MA 이평선 전략 백테스트 결과")
    print(f"  시뮬레이션 기간: {n_days}영업일 ({years:.1f}년)")
    print("=" * W)
    print(f"  초기자본     : {initial_capital:>20,} 원")
    print(f"  최종자산     : {final:>20,.0f} 원")
    print(f"  총 수익률    : {total_r:>+19.2%}")
    print(f"  CAGR         : {cagr:>+19.2%}")
    print(f"  MDD          : {mdd:>+19.2%}")
    print("-" * W)
    print(f"  총 매매 횟수 : {len(sells):>15} 회")
    if sells:
        wr = len(wins) / len(sells) * 100
        avg_win  = np.mean([t["pnl_pct"] for t in wins])  if wins  else 0.0
        loss_t   = [t for t in sells if t["pnl_pct"] <= 0]
        avg_loss = np.mean([t["pnl_pct"] for t in loss_t]) if loss_t else 0.0
        avg_days = np.mean([t["days_held"] for t in sells])
        print(f"  승률         : {wr:>18.1f} %")
        print(f"  평균수익(승) : {avg_win:>+19.2%}")
        print(f"  평균손실(패) : {avg_loss:>+19.2%}")
        print(f"  평균 보유일  : {avg_days:>16.1f} 일")
    print("-" * W)
    print("  매도 사유별:")
    for reason, pnls in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        avg = np.mean(pnls)
        print(f"    {reason:10s}: {len(pnls):3d}회  평균 {avg:+.2%}")
    print("=" * W)
    print("  ※ MA248 기준 부분정배열 포함 / MA744는 후반부에만 유효")
    print("  ※ 실제 전략은 S3/S4와 슬롯 공유 → 실매매와 결과 차이 있음")
    print("=" * W + "\n")


def main():
    parser = argparse.ArgumentParser(description="S2 MA 이평선 전략 백테스트")
    parser.add_argument("--capital", type=int, default=10_000_000,
                        help="초기자본 (기본: 1,000만원)")
    args = parser.parse_args()

    trades, port_hist = run_backtest(args.capital)
    _report(trades, port_hist, args.capital)


if __name__ == "__main__":
    main()
