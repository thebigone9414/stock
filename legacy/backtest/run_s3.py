#!/usr/bin/env python3
"""
S3 CANSLIM 전략 백테스트

조건: N(52주신고가10%이내) · S(거래량150%급증) · L(RS>KOSPI) · M(KODEX200 MA5>MA20)
※ I(기관+외국인 순매수): API 전용 — 백테스트 제외 (N·S·L·M 4조건으로 근사)

신호: i일 종가 기준  →  체결: i+1일 종가 (오픈 데이터 없음, 근사치)
데이터: data/canslim_ohlcv_cache.json (최대 300일, closes+volumes)
시장기준: KODEX200(069500) closes

Usage:
    python backtest/run_s3.py
    python backtest/run_s3.py --capital 50000000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 전략 파라미터 (strategies/canslim.py 와 동일) ────────────────────
MAX_SLOTS          = 4
SLOT_RATIO         = 0.20
STOP_LOSS          = 0.07
TAKE_PROFIT        = 0.20
TAKE_PROFIT_EXT    = 0.25
EARLY_GAIN_TRIGGER = 0.15
EARLY_GAIN_DAYS    = 15      # 영업일 ≈ 21 캘린더일
TIME_STOP_DAYS     = 40      # 영업일 ≈ 56 캘린더일

# ── 거래비용 ─────────────────────────────────────────────────────────
BUY_FEE  = 0.00015
SELL_FEE = 0.00015 + 0.002

KODEX200_CODE = "069500"
MIN_DATA_DAYS = 65           # L 조건(63일) + 여유


# ── 데이터 로드 ──────────────────────────────────────────────────────
def _load_data() -> dict:
    canslim_path = Path(__file__).parent.parent / "data" / "canslim_ohlcv_cache.json"
    s2_path      = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"

    if not canslim_path.exists():
        print(f"[오류] {canslim_path} 파일 없음 — canslim-batch 먼저 실행 필요")
        sys.exit(1)

    raw_canslim = json.loads(canslim_path.read_text(encoding="utf-8"))
    raw_s2      = json.loads(s2_path.read_text(encoding="utf-8")) if s2_path.exists() else {}

    # canslim 캐시 우선, 없으면 S2 캐시 사용 (KODEX200 등 S2 전용 종목 포함)
    merged = {**raw_s2, **raw_canslim}

    stocks = {}
    for code, data in merged.items():
        closes  = data.get("closes",  [])
        volumes = data.get("volumes", [])
        opens   = data.get("opens",   [])
        if len(closes) >= MIN_DATA_DAYS:
            stocks[code] = {
                "closes":  np.array(closes[-300:],  dtype=float),
                "volumes": (np.array(volumes[-300:], dtype=float)
                            if volumes else np.zeros(min(len(closes), 300))),
                "opens":   (np.array(opens[-300:],  dtype=float)
                            if opens else None),
            }
    return stocks


# ── 조건 함수 ────────────────────────────────────────────────────────
def _check_N(closes: np.ndarray, i: int) -> bool:
    """52주(252일) 신고가 대비 종가 10% 이내"""
    start  = max(0, i - 251)
    hi_52w = closes[start : i + 1].max()
    return bool(hi_52w > 0 and closes[i] / hi_52w >= 0.90)


def _check_S(volumes: np.ndarray, i: int) -> bool:
    """당일 거래량 ≥ 전일 기준 50일 평균의 150%"""
    if i < 51 or volumes[i] == 0:
        return False
    vol_avg = volumes[max(0, i - 50) : i].mean()
    return bool(vol_avg > 0 and volumes[i] >= vol_avg * 1.50)


def _check_L(stock_closes: np.ndarray, kodex_closes: np.ndarray, i: int) -> bool:
    """3개월(63거래일) 수익률 > KODEX200"""
    if i < 63 or i >= len(kodex_closes):
        return False
    stock_rs = stock_closes[i] / stock_closes[i - 63] - 1
    kospi_rs = kodex_closes[i] / kodex_closes[i - 63] - 1
    return bool(stock_rs > kospi_rs)


def _check_M(kodex_closes: np.ndarray, i: int) -> bool:
    """KODEX200 MA5 > MA20"""
    if i < 19:
        return False
    ma5  = kodex_closes[i - 4  : i + 1].mean()
    ma20 = kodex_closes[i - 19 : i + 1].mean()
    return bool(ma5 > ma20)


# ── 시뮬레이션 ───────────────────────────────────────────────────────
def run_backtest(initial_capital: int = 10_000_000) -> tuple[list, list]:
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")

    if KODEX200_CODE not in stocks:
        print(f"[오류] KODEX200({KODEX200_CODE}) 데이터 없음 — canslim-batch 확인 필요")
        sys.exit(1)

    kodex_closes = stocks[KODEX200_CODE]["closes"]
    total_days   = len(kodex_closes)

    capital   = float(initial_capital)
    positions = {}   # code → {entry_i, entry_price, qty, peak, days_held, early_triggered}
    trades    = []
    port_hist = []

    # N 조건에 충분한 데이터(~50일) + L 조건(63일)
    start = 63 + 1
    end   = total_days - 1   # i+1 종가 접근 가능한 마지막

    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end - start) / 252:.1f}년)\n")

    for i in range(start, end):
        # 현재 포지션 평가
        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items()
            if c in stocks and i < len(stocks[c]["closes"])
        )
        total_eval = capital + pos_val

        # ── 매도 신호 (i 종가 기준, i+1 종가로 체결) ────────────
        for code in list(positions):
            if code not in stocks or i >= len(stocks[code]["closes"]) - 1:
                del positions[code]
                continue

            pos   = positions[code]
            cur   = stocks[code]["closes"][i]
            entry = pos["entry_price"]
            gain  = (cur - entry) / entry
            days  = pos["days_held"]

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
            elif days >= TIME_STOP_DAYS:
                reason = "타임스탑"
            elif gain >= target:
                reason = "익절"

            if reason:
                o = stocks[code]["opens"]
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
        if len(positions) < MAX_SLOTS and _check_M(kodex_closes, i):
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

                if not _check_N(closes, i):
                    continue
                if not _check_S(volumes, i):
                    continue
                if not _check_L(closes, kodex_closes, i):
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

    sells     = trades
    wins      = [t for t in sells if t["pnl_pct"] > 0]
    by_reason: dict = {}
    for t in sells:
        by_reason.setdefault(t["reason"], []).append(t["pnl_pct"])

    W = 57
    print("\n" + "=" * W)
    print(f"  S3 CANSLIM 전략 백테스트 결과")
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
        wr       = len(wins) / len(sells) * 100
        avg_win  = np.mean([t["pnl_pct"] for t in wins]) if wins else 0.0
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
    print("  ※ 체결가 = i+1일 시가 (S2캐시 종목) 또는 종가 근사 (KOSDAQ150)")
    print("  ※ I 조건(기관+외국인) 제외 — N·S·L·M 4조건 사용")
    print(f"  ※ 타임스탑 {TIME_STOP_DAYS}영업일 ≈ 56 캘린더일")
    print("  ※ 실제 전략은 S2/S4와 슬롯 공유 → 실매매와 결과 차이 있음")
    print("=" * W + "\n")


def main():
    parser = argparse.ArgumentParser(description="S3 CANSLIM 전략 백테스트")
    parser.add_argument("--capital", type=int, default=10_000_000,
                        help="초기자본 (기본: 1,000만원)")
    args = parser.parse_args()

    trades, port_hist = run_backtest(args.capital)
    _report(trades, port_hist, args.capital)


if __name__ == "__main__":
    main()
