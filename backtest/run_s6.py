#!/usr/bin/env python3
"""
S6 Connors RSI(2) 전략 백테스트

조건:
  시장 필터 : KODEX200 종가 > MA200
  매수      : 개별 종목 종가 > MA200 AND RSI(2) < 10
  청산      : RSI(2) >= 65 OR 손절 -5% OR 타임스탑 10거래일

신호: i일 종가/RSI 기준  →  체결: i+1일 시가
데이터: data/ohlcv_cache.json (820일, closes+opens)
※ 볼륨 불필요 — RSI는 순수 가격 기반

Usage:
    python backtest/run_s6.py
    python backtest/run_s6.py --capital 50000000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 전략 파라미터 (strategies/connors.py 와 동일) ────────────────────
MAX_SLOTS   = 4
SLOT_RATIO  = 0.20
STOP_LOSS   = 0.05     # -5% (단기 전략)
TIME_STOP   = 10       # 거래일

RSI_PERIOD  = 2
MA_LONG     = 200      # 종목 필터: close > MA200
MARKET_MA   = 200      # 시장 필터: KODEX200 > MA200
RSI_ENTRY   = 10.0     # 매수
RSI_EXIT    = 65.0     # 청산

# ── 거래비용 ─────────────────────────────────────────────────────────
BUY_FEE  = 0.00015
SELL_FEE = 0.00015 + 0.002

KODEX200_CODE = "069500"
MIN_DATA_DAYS = 820


# ── 데이터 로드 ──────────────────────────────────────────────────────
def _load_data() -> dict:
    path = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"
    if not path.exists():
        print(f"[오류] {path} 없음 — ma-batch 먼저 실행 필요")
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


# ── RSI(2) 사전 계산 ─────────────────────────────────────────────────
def _compute_rsi2(closes: np.ndarray) -> np.ndarray:
    """전체 기간 RSI(2) 배열 반환 (Wilder's EMA, alpha=0.5)"""
    s     = pd.Series(closes)
    delta = s.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        rs  = np.where(avg_l.values == 0, np.inf, avg_g.values / avg_l.values)
        rsi = np.where(avg_l.values == 0, 100.0, 100 - 100 / (1 + rs))
    # 초기 워밍업 구간(100일) NaN 처리
    rsi[:100] = np.nan
    return rsi


def _precompute(stocks: dict) -> None:
    for data in stocks.values():
        data["rsi2"] = _compute_rsi2(data["closes"])
        ma = pd.Series(data["closes"]).rolling(MA_LONG).mean().values
        data["ma200"] = ma


# ── 시뮬레이션 ───────────────────────────────────────────────────────
def run_backtest(initial_capital: int = 10_000_000) -> tuple[list, list]:
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")

    print("RSI(2) · MA200 사전 계산 중...")
    _precompute(stocks)

    if KODEX200_CODE not in stocks:
        print(f"[오류] KODEX200 데이터 없음")
        sys.exit(1)

    kodex_closes = stocks[KODEX200_CODE]["closes"]
    kodex_ma200  = stocks[KODEX200_CODE]["ma200"]
    total_days   = MIN_DATA_DAYS

    capital   = float(initial_capital)
    positions = {}   # code → {entry_i, entry_price, qty, days_held}
    trades    = []
    port_hist = []

    # MA200(200일) + RSI 워밍업(100일) 이후부터
    start = MA_LONG + 100
    end   = total_days - 1

    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end - start) / 252:.1f}년)\n")

    for i in range(start, end):
        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items() if c in stocks
        )
        total_eval = capital + pos_val

        # 시장 필터: KODEX200 > MA200
        market_up = (not np.isnan(kodex_ma200[i])
                     and kodex_closes[i] > kodex_ma200[i])

        # ── 매도 신호 (i 종가/RSI 기준, i+1 시가 체결) ───────────
        for code in list(positions):
            if code not in stocks:
                del positions[code]
                continue

            pos   = positions[code]
            cur   = stocks[code]["closes"][i]
            entry = pos["entry_price"]
            gain  = (cur - entry) / entry
            days  = pos["days_held"]
            rsi2  = stocks[code]["rsi2"][i]

            reason = None
            if gain <= -STOP_LOSS:
                reason = "손절"
            elif days >= TIME_STOP:
                reason = "타임스탑"
            elif not np.isnan(rsi2) and rsi2 >= RSI_EXIT:
                reason = f"RSI청산({rsi2:.0f})"

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

        # ── 매수 신호 ─────────────────────────────────────────────
        if len(positions) < MAX_SLOTS and market_up:
            slot_budget = total_eval * SLOT_RATIO

            # RSI(2) 낮은 순(더 과매도) 정렬
            candidates = []
            for code, data in stocks.items():
                if code in positions or code == KODEX200_CODE:
                    continue
                if i >= len(data["closes"]) - 1:
                    continue
                rsi2   = data["rsi2"][i]
                ma200  = data["ma200"][i]
                close  = data["closes"][i]
                if np.isnan(rsi2) or np.isnan(ma200):
                    continue
                if rsi2 < RSI_ENTRY and close > ma200:
                    candidates.append((code, rsi2))

            candidates.sort(key=lambda x: x[1])   # 과매도 심한 순

            for code, _rsi in candidates:
                if len(positions) >= MAX_SLOTS:
                    break
                data   = stocks[code]
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
                    "entry_i":     i + 1,
                    "entry_price": buy_px,
                    "qty":         qty,
                    "days_held":   0,
                }

        for pos in positions.values():
            pos["days_held"] += 1

        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items() if c in stocks
        )
        port_hist.append(capital + pos_val)

    return trades, port_hist


# ── 결과 리포트 ──────────────────────────────────────────────────────
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

    wins      = [t for t in trades if t["pnl_pct"] > 0]
    by_reason: dict = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["pnl_pct"])

    W = 57
    print("\n" + "=" * W)
    print(f"  S6 Connors RSI(2) 전략 백테스트 결과")
    print(f"  시뮬레이션 기간: {n_days}영업일 ({years:.1f}년)")
    print("=" * W)
    print(f"  초기자본     : {initial_capital:>20,} 원")
    print(f"  최종자산     : {final:>20,.0f} 원")
    print(f"  총 수익률    : {total_r:>+19.2%}")
    print(f"  CAGR         : {cagr:>+19.2%}")
    print(f"  MDD          : {mdd:>+19.2%}")
    print("-" * W)
    print(f"  총 매매 횟수 : {len(trades):>15} 회")
    if trades:
        wr       = len(wins) / len(trades) * 100
        avg_win  = np.mean([t["pnl_pct"] for t in wins]) if wins else 0.0
        loss_t   = [t for t in trades if t["pnl_pct"] <= 0]
        avg_loss = np.mean([t["pnl_pct"] for t in loss_t]) if loss_t else 0.0
        avg_days = np.mean([t["days_held"] for t in trades])
        print(f"  승률         : {wr:>18.1f} %")
        print(f"  평균수익(승) : {avg_win:>+19.2%}")
        print(f"  평균손실(패) : {avg_loss:>+19.2%}")
        print(f"  평균 보유일  : {avg_days:>16.1f} 일")
    print("-" * W)
    print("  매도 사유별:")
    for reason, pnls in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        avg = np.mean(pnls)
        print(f"    {reason:14s}: {len(pnls):3d}회  평균 {avg:+.2%}")
    print("=" * W)
    print(f"  ※ 매수: RSI(2)<{RSI_ENTRY:.0f} AND close>MA{MA_LONG} AND KODEX200>MA{MARKET_MA}")
    print(f"  ※ 청산: RSI(2)>={RSI_EXIT:.0f} OR 손절-{STOP_LOSS:.0%} OR 타임스탑{TIME_STOP}일")
    print("  ※ 실제 전략은 S2~S6 슬롯 공유 → 실매매와 결과 차이 있음")
    print("=" * W + "\n")


def main():
    parser = argparse.ArgumentParser(description="S6 Connors RSI(2) 전략 백테스트")
    parser.add_argument("--capital", type=int, default=10_000_000)
    args = parser.parse_args()

    trades, port_hist = run_backtest(args.capital)
    _report(trades, port_hist, args.capital)


if __name__ == "__main__":
    main()
