#!/usr/bin/env python3
"""
S3 CANSLIM + DART C·A 조건 비교 백테스트

3가지 시나리오를 나란히 비교:
  Scenario 1 (baseline)  : N·S·L·M  전체 OHLCV 유니버스 (334종목)
  Scenario 2 (dart_sub)  : N·S·L·M  DART 데이터 있는 종목만 (선택편향 통제)
  Scenario 3 (ca_filter) : N·S·L·M  + A 조건 사전 필터 (DART 커버 종목 중 A 통과)

⚠ Look-ahead 주의: 현재 DART 데이터(2026-06)를 과거 전 구간에 정적으로 적용.
  공시 시점 기준 실제 운용과 차이 있음. 성과 방향성 참고용.

데이터 커버리지 현황:
  - OHLCV 캐시: ~334종목 (canslim + s2 병합)
  - DART 데이터: ~500종목 (2026-06-07 기준, 구 유니버스로 수집)
  - DART+OHLCV 교집합: ~37종목
  - C+A 통과: ~3종목 / A만 통과: ~12종목

Usage:
    python backtest/run_s3_ca.py
    python backtest/run_s3_ca.py --capital 50000000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.canslim_store import check_C, check_A

# ── 전략 파라미터 (strategies/canslim.py 와 동일) ────────────────────
MAX_SLOTS          = 4
SLOT_RATIO         = 0.20
STOP_LOSS          = 0.07
TAKE_PROFIT        = 0.20
TAKE_PROFIT_EXT    = 0.25
EARLY_GAIN_TRIGGER = 0.15
EARLY_GAIN_DAYS    = 15
TIME_STOP_DAYS     = 40

BUY_FEE  = 0.00015
SELL_FEE = 0.00015 + 0.002

KODEX200_CODE = "069500"
MIN_DATA_DAYS = 65


def _load_ohlcv() -> dict:
    canslim_path = Path(__file__).parent.parent / "data" / "canslim_ohlcv_cache.json"
    s2_path      = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"

    raw_canslim = json.loads(canslim_path.read_text(encoding="utf-8")) if canslim_path.exists() else {}
    raw_s2      = json.loads(s2_path.read_text(encoding="utf-8"))      if s2_path.exists()      else {}

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
                "opens":   (np.array(opens[-300:],  dtype=float) if opens else None),
            }
    return stocks


def _load_ca_sets() -> tuple[set, set, set]:
    """DART data → (dart_codes, a_codes, ca_codes)"""
    dart_path = Path(__file__).parent.parent / "data" / "dart_data.json"
    if not dart_path.exists():
        return set(), set(), set()
    dart = json.loads(dart_path.read_text(encoding="utf-8"))
    corps = dart.get("corps", {})

    dart_codes, a_codes, ca_codes = set(), set(), set()
    for code, corp in corps.items():
        dart_codes.add(code)
        C = check_C(corp)
        A = check_A(corp)
        if A:
            a_codes.add(code)
        if C and A:
            ca_codes.add(code)
    return dart_codes, a_codes, ca_codes


# ── 조건 함수 ────────────────────────────────────────────────────────
def _check_N(closes, i):
    start  = max(0, i - 251)
    hi_52w = closes[start : i + 1].max()
    return bool(hi_52w > 0 and closes[i] / hi_52w >= 0.90)


def _check_S(volumes, i):
    if i < 51 or volumes[i] == 0:
        return False
    vol_avg = volumes[max(0, i - 50) : i].mean()
    return bool(vol_avg > 0 and volumes[i] >= vol_avg * 1.50)


def _check_L(stock_closes, kodex_closes, i):
    if i < 63 or i >= len(kodex_closes):
        return False
    return bool(stock_closes[i] / stock_closes[i - 63] > kodex_closes[i] / kodex_closes[i - 63])


def _check_M(kodex_closes, i):
    if i < 19:
        return False
    return bool(kodex_closes[i - 4 : i + 1].mean() > kodex_closes[i - 19 : i + 1].mean())


# ── 시뮬레이션 ───────────────────────────────────────────────────────
def _simulate(stocks: dict, allowed_codes: set | None, label: str, initial_capital: int) -> tuple:
    """allowed_codes=None 이면 전체 유니버스 사용"""
    kodex_closes = stocks[KODEX200_CODE]["closes"]
    total_days   = len(kodex_closes)

    capital   = float(initial_capital)
    positions = {}
    trades    = []
    port_hist = []

    tradeable = {
        code: data
        for code, data in stocks.items()
        if code != KODEX200_CODE and (allowed_codes is None or code in allowed_codes)
    }

    start = 63 + 1
    end   = total_days - 1

    for i in range(start, end):
        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items()
            if c in stocks and i < len(stocks[c]["closes"])
        )
        total_eval = capital + pos_val

        # 매도
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
            if not pos["early_triggered"] and gain >= EARLY_GAIN_TRIGGER and days <= EARLY_GAIN_DAYS:
                pos["early_triggered"] = True

            target = TAKE_PROFIT_EXT if pos["early_triggered"] else TAKE_PROFIT
            reason = None
            if gain <= -STOP_LOSS:         reason = "손절"
            elif days >= TIME_STOP_DAYS:   reason = "타임스탑"
            elif gain >= target:           reason = "익절"

            if reason:
                o       = stocks[code]["opens"]
                sell_px = o[i + 1] if o is not None else stocks[code]["closes"][i + 1]
                capital += sell_px * pos["qty"] * (1 - SELL_FEE)
                trades.append({
                    "code": code, "entry_i": pos["entry_i"], "exit_i": i + 1,
                    "entry_price": entry, "exit_price": sell_px,
                    "qty": pos["qty"], "pnl_pct": (sell_px - entry) / entry,
                    "reason": reason, "days_held": days,
                })
                del positions[code]

        # 매수
        if len(positions) < MAX_SLOTS and _check_M(kodex_closes, i):
            slot_budget = total_eval * SLOT_RATIO
            for code, data in tradeable.items():
                if len(positions) >= MAX_SLOTS:
                    break
                if code in positions or i >= len(data["closes"]) - 1:
                    continue
                closes  = data["closes"]
                volumes = data["volumes"]
                if not _check_N(closes, i): continue
                if not _check_S(volumes, i): continue
                if not _check_L(closes, kodex_closes, i): continue

                o      = data["opens"]
                buy_px = o[i + 1] if o is not None else closes[i + 1]
                if buy_px <= 0: continue
                qty  = int(slot_budget / (buy_px * (1 + BUY_FEE)))
                if qty <= 0: continue
                cost = buy_px * qty * (1 + BUY_FEE)
                if cost > capital:
                    qty  = int(capital / (buy_px * (1 + BUY_FEE)))
                    cost = buy_px * qty * (1 + BUY_FEE)
                if qty <= 0: continue

                capital -= cost
                positions[code] = {
                    "entry_i": i + 1, "entry_price": buy_px, "qty": qty,
                    "peak": buy_px, "days_held": 0, "early_triggered": False,
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


# ── 결과 집계 ────────────────────────────────────────────────────────
def _stats(trades, port_hist, initial_capital) -> dict:
    if not port_hist:
        return {}
    final  = port_hist[-1]
    n_days = len(port_hist)
    years  = n_days / 252
    cagr   = (final / initial_capital) ** (1 / max(years, 0.01)) - 1
    arr    = np.array(port_hist)
    peak   = np.maximum.accumulate(arr)
    mdd    = float(((arr - peak) / peak).min())
    wins   = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    by_reason = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["pnl_pct"])
    return {
        "final": final, "total_r": (final - initial_capital) / initial_capital,
        "cagr": cagr, "mdd": mdd, "n_days": n_days, "years": years,
        "n_trades": len(trades), "wins": len(wins), "losses": len(losses),
        "wr": len(wins) / len(trades) * 100 if trades else 0,
        "avg_win":  float(np.mean([t["pnl_pct"] for t in wins]))   if wins   else 0,
        "avg_loss": float(np.mean([t["pnl_pct"] for t in losses])) if losses else 0,
        "avg_days": float(np.mean([t["days_held"] for t in trades])) if trades else 0,
        "by_reason": {r: (len(v), float(np.mean(v))) for r, v in by_reason.items()},
    }


def _print_comparison(results: list[tuple[str, str, dict]], initial_capital: int) -> None:
    W = 65
    print("\n" + "=" * W)
    print("  S3 CANSLIM 전략 비교 백테스트")
    print("=" * W)
    print(f"  {'':30s}{'Baseline':>10s}  {'DART subset':>10s}  {'A-filter':>10s}")
    print(f"  {'시나리오':30s}{'N·S·L·M':>10s}  {'N·S·L·M':>10s}  {'N·S·L·M+A':>10s}")
    print(f"  {'유니버스':30s}{'전체':>10s}  {'DART종목':>10s}  {'A통과종목':>10s}")
    print("-" * W)

    labels = [r[0] for r in results]
    descs  = [r[1] for r in results]
    stats  = [r[2] for r in results]

    def row(name, vals):
        s = f"  {name:30s}"
        for v in vals:
            s += f"  {v:>10s}"
        print(s)

    row("종목수",        [d for d in descs])
    row("기간(영업일)",  [f"{s.get('n_days','?')}d" for s in stats])
    row("초기자본",      [f"{initial_capital/1e6:.0f}백만" for _ in stats])
    print("-" * W)
    row("최종자산",      [f"{s.get('final',0)/1e6:.2f}M" for s in stats])
    row("총 수익률",     [f"{s.get('total_r',0):+.1%}" for s in stats])
    row("CAGR",         [f"{s.get('cagr',0):+.1%}" for s in stats])
    row("MDD",          [f"{s.get('mdd',0):+.1%}" for s in stats])
    print("-" * W)
    row("매매 횟수",     [f"{s.get('n_trades',0)}회" for s in stats])
    row("승률",          [f"{s.get('wr',0):.1f}%" for s in stats])
    row("평균수익(승)",  [f"{s.get('avg_win',0):+.1%}" for s in stats])
    row("평균손실(패)",  [f"{s.get('avg_loss',0):+.1%}" for s in stats])
    row("평균 보유일",   [f"{s.get('avg_days',0):.1f}d" for s in stats])
    print("=" * W)

    # 매도 사유별
    print("\n  매도 사유별:")
    all_reasons = sorted(set(r for s in stats for r in s.get("by_reason", {})))
    header = f"  {'사유':8s}"
    for _ in stats:
        header += f"  {'횟수/평균':>14s}"
    print(header)
    for reason in all_reasons:
        line = f"  {reason:8s}"
        for s in stats:
            br = s.get("by_reason", {}).get(reason)
            if br:
                line += f"  {br[0]:3d}회 {br[1]:+.1%}"
            else:
                line += f"  {'—':>14s}"
        print(line)

    print("\n" + "=" * W)
    print("  ※ Scenario 2·3 = Look-ahead bias 있음 (현재 DART 데이터 소급 적용)")
    print("  ※ C 조건 현재 통과 종목 극히 적음 (2026 상반기 실적 발표 전)")
    print("  ※ DART 데이터가 구 유니버스(255종목)로 수집 → 교집합 제한적")
    print("  ※ 신뢰도 높은 비교를 위해 새 유니버스로 DART 배치 재실행 권장")
    print("=" * W + "\n")


def main():
    parser = argparse.ArgumentParser(description="S3 CANSLIM + DART C·A 비교 백테스트")
    parser.add_argument("--capital", type=int, default=10_000_000)
    args = parser.parse_args()

    print("데이터 로딩 중...")
    stocks = _load_ohlcv()
    print(f"  → OHLCV {len(stocks)}종목 (KODEX200 포함)")

    if KODEX200_CODE not in stocks:
        print("[오류] KODEX200 데이터 없음"); sys.exit(1)

    dart_codes, a_codes, ca_codes = _load_ca_sets()
    ohlcv_codes = set(stocks) - {KODEX200_CODE}
    dart_in_ohlcv = dart_codes & ohlcv_codes
    a_in_ohlcv    = a_codes    & ohlcv_codes
    ca_in_ohlcv   = ca_codes   & ohlcv_codes

    print(f"  → DART 데이터: {len(dart_codes)}종목 (OHLCV 교집합: {len(dart_in_ohlcv)})")
    print(f"  → A 조건 통과: {len(a_codes)}종목 (OHLCV 교집합: {len(a_in_ohlcv)})")
    print(f"  → C+A 모두 통과: {len(ca_codes)}종목 (OHLCV 교집합: {len(ca_in_ohlcv)})")

    scenarios = [
        ("baseline",  f"{len(ohlcv_codes)}종목", None),
        ("dart_sub",  f"{len(dart_in_ohlcv)}종목", dart_in_ohlcv if dart_in_ohlcv else None),
        ("ca_filter", f"{len(a_in_ohlcv)}종목",  a_in_ohlcv    if a_in_ohlcv    else None),
    ]

    results = []
    for label, desc, allowed in scenarios:
        print(f"\n[{label}] 시뮬레이션 ({desc}) ...")
        trades, port_hist = _simulate(stocks, allowed, label, args.capital)
        s = _stats(trades, port_hist, args.capital)
        results.append((label, desc, s))
        print(f"  완료: {s.get('n_trades',0)}회 매매, 수익률 {s.get('total_r',0):+.1%}")

    _print_comparison(results, args.capital)


if __name__ == "__main__":
    main()
