#!/usr/bin/env python3
"""
S5 Darvas Box 전략 백테스트

조건:
  박스 형성 : 최근 20거래일(오늘 제외) 종가 범위(high-low)/high ≤ 15%
  브레이크아웃: 오늘 종가 > 박스 상단 AND 거래량 ≥ 50일 평균의 150%
  시장 필터 : KODEX200 MA20 > MA60

신호: i일 종가 기준  →  체결: i+1일 시가(S2캐시) 또는 종가 근사
데이터: data/canslim_ohlcv_cache.json (최대 300일, closes+volumes)
        data/ohlcv_cache.json (opens)

Usage:
    python backtest/run_s5.py
    python backtest/run_s5.py --capital 50000000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 전략 파라미터 (strategies/darvas.py 와 동일) ─────────────────────
MAX_SLOTS          = 4
SLOT_RATIO         = 0.20
STOP_LOSS          = 0.07
TAKE_PROFIT        = 0.20
TAKE_PROFIT_EXT    = 0.25
EARLY_GAIN_TRIGGER = 0.15
EARLY_GAIN_DAYS    = 15      # 영업일 ≈ 21 캘린더일
TIME_STOP_DAYS     = 40      # 영업일 ≈ 56 캘린더일

# ── Darvas 파라미터 ──────────────────────────────────────────────────
BOX_DAYS          = 20
BOX_MAX_RANGE_PCT = 15.0
BREAKOUT_VOL_X    = 1.50
MARKET_MA_FAST    = 20
MARKET_MA_SLOW    = 60

# ── 거래비용 ─────────────────────────────────────────────────────────
BUY_FEE  = 0.00015
SELL_FEE = 0.00015 + 0.002

KODEX200_CODE = "069500"
MIN_DATA_DAYS = BOX_DAYS + 52   # 박스(20) + 거래량50일평균 여유


# ── 데이터 로드 ──────────────────────────────────────────────────────
def _load_data() -> dict:
    canslim_path = Path(__file__).parent.parent / "data" / "canslim_ohlcv_cache.json"
    s2_path      = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"

    if not canslim_path.exists():
        print(f"[오류] {canslim_path} 없음 — canslim-batch 먼저 실행 필요")
        sys.exit(1)

    raw_canslim = json.loads(canslim_path.read_text(encoding="utf-8"))
    raw_s2      = json.loads(s2_path.read_text(encoding="utf-8")) if s2_path.exists() else {}

    merged = {**raw_s2, **raw_canslim}

    stocks = {}
    for code, data in merged.items():
        closes  = data.get("closes",  [])
        volumes = data.get("volumes", [])
        opens   = data.get("opens",   [])
        if len(closes) >= MIN_DATA_DAYS:
            n = min(len(closes), 300)
            stocks[code] = {
                "closes":  np.array(closes[-n:],  dtype=float),
                "volumes": (np.array(volumes[-n:], dtype=float)
                            if volumes else np.zeros(n)),
                "opens":   (np.array(opens[-n:],  dtype=float)
                            if opens else None),
            }
    return stocks


# ── 조건 함수 ────────────────────────────────────────────────────────
def _check_darvas(closes: np.ndarray, volumes: np.ndarray, i: int) -> tuple:
    """
    Darvas Box 브레이크아웃 체크.
    반환: (all_pass: bool, box_high: float, box_low: float)
    """
    if i < BOX_DAYS + 50:
        return False, 0.0, 0.0

    box_window  = closes[i - BOX_DAYS : i]     # 오늘 제외 20일
    box_high    = float(box_window.max())
    box_low     = float(box_window.min())

    if box_high <= 0:
        return False, 0.0, 0.0

    box_range_pct = (box_high - box_low) / box_high * 100
    tight_box     = box_range_pct <= BOX_MAX_RANGE_PCT
    breakout      = closes[i] > box_high

    # 거래량: 전일 기준 50일 평균 vs 오늘
    vol_avg   = volumes[i - 50 : i].mean()
    vol_ratio = volumes[i] / vol_avg if vol_avg > 0 else 0.0
    vol_ok    = vol_ratio >= BREAKOUT_VOL_X

    return (tight_box and breakout and vol_ok), box_high, box_low


def _check_market(kodex_closes: np.ndarray, i: int) -> bool:
    """KODEX200 MA20 > MA60"""
    if i < MARKET_MA_SLOW - 1:
        return True
    ma_fast = kodex_closes[i - MARKET_MA_FAST + 1 : i + 1].mean()
    ma_slow = kodex_closes[i - MARKET_MA_SLOW + 1 : i + 1].mean()
    return bool(ma_fast > ma_slow)


# ── 시뮬레이션 ───────────────────────────────────────────────────────
def run_backtest(initial_capital: int = 10_000_000) -> tuple[list, list]:
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")

    if KODEX200_CODE not in stocks:
        print(f"[오류] KODEX200 데이터 없음")
        sys.exit(1)

    kodex_closes = stocks[KODEX200_CODE]["closes"]
    total_days   = len(kodex_closes)

    capital   = float(initial_capital)
    positions = {}   # code → {entry_i, entry_price, qty, peak, days_held, early_triggered, box_low}
    trades    = []
    port_hist = []

    start = MARKET_MA_SLOW + BOX_DAYS + 52   # MA60 + 박스20 + 거래량50 여유
    end   = total_days - 1

    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end - start) / 252:.1f}년)\n")

    for i in range(start, end):
        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items()
            if c in stocks and i < len(stocks[c]["closes"])
        )
        total_eval = capital + pos_val

        # ── 매도 신호 (i 종가, i+1 시가 체결) ───────────────────
        for code in list(positions):
            if code not in stocks or i >= len(stocks[code]["closes"]) - 1:
                del positions[code]
                continue

            pos   = positions[code]
            cur   = stocks[code]["closes"][i]
            entry = pos["entry_price"]
            gain  = (cur - entry) / entry
            days  = pos["days_held"]
            bl    = pos["box_low"]

            if cur > pos["peak"]:
                pos["peak"] = cur

            if (not pos["early_triggered"]
                    and gain >= EARLY_GAIN_TRIGGER
                    and days <= EARLY_GAIN_DAYS):
                pos["early_triggered"] = True

            target = TAKE_PROFIT_EXT if pos["early_triggered"] else TAKE_PROFIT

            reason = None
            if gain <= -STOP_LOSS:
                reason = "손절"
            elif bl > 0 and cur < bl:
                reason = "박스하단이탈"
            elif days >= TIME_STOP_DAYS:
                reason = "타임스탑"
            elif gain >= target:
                reason = "익절"

            if reason:
                o        = stocks[code]["opens"]
                sell_px  = o[i + 1] if o is not None else stocks[code]["closes"][i + 1]
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
        if len(positions) < MAX_SLOTS and _check_market(kodex_closes, i):
            slot_budget = total_eval * SLOT_RATIO

            for code, data in stocks.items():
                if len(positions) >= MAX_SLOTS:
                    break
                if code in positions or code == KODEX200_CODE:
                    continue
                if i >= len(data["closes"]) - 1:
                    continue

                closes  = data["closes"]
                volumes = data["volumes"]

                all_pass, box_high, box_low = _check_darvas(closes, volumes, i)
                if not all_pass:
                    continue

                o      = data["opens"]
                buy_px = o[i + 1] if o is not None else closes[i + 1]
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
                    "entry_i":         i + 1,
                    "entry_price":     buy_px,
                    "qty":             qty,
                    "peak":            buy_px,
                    "days_held":       0,
                    "early_triggered": False,
                    "box_low":         box_low,
                }

        for pos in positions.values():
            pos["days_held"] += 1

        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items()
            if c in stocks and i < len(stocks[c]["closes"])
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
    print(f"  S5 Darvas Box 전략 백테스트 결과")
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
        print(f"    {reason:12s}: {len(pnls):3d}회  평균 {avg:+.2%}")
    print("=" * W)
    print(f"  ※ 박스 20거래일, 범위≤{BOX_MAX_RANGE_PCT}%, 거래량≥{BREAKOUT_VOL_X}배")
    print(f"  ※ 시장 필터: KODEX200 MA{MARKET_MA_FAST} > MA{MARKET_MA_SLOW}")
    print(f"  ※ 타임스탑 {TIME_STOP_DAYS}영업일 ≈ 56 캘린더일")
    print("  ※ 실제 전략은 S2~S6 슬롯 공유 → 실매매와 결과 차이 있음")
    print("=" * W + "\n")


def main():
    parser = argparse.ArgumentParser(description="S5 Darvas Box 전략 백테스트")
    parser.add_argument("--capital", type=int, default=10_000_000)
    args = parser.parse_args()

    trades, port_hist = run_backtest(args.capital)
    _report(trades, port_hist, args.capital)


if __name__ == "__main__":
    main()
