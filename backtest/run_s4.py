#!/usr/bin/env python3
"""
S4 SEPA 전략 백테스트 (Mark Minervini VCP 브레이크아웃)

조건: 트렌드템플릿(T1~T7) · RS≥70퍼센타일 · VCP패턴 · 브레이크아웃(종가>피벗)
신호: i일 종가 기준  →  체결: i+1일 시가
데이터: data/ohlcv_cache.json (820일, closes+opens)
        data/canslim_ohlcv_cache.json (최대 300일, volumes — 후반 300일 정렬)

비교:
  원본: 손절(-7%) + 타임스탑(40영업일) + 익절(+20/25%)
  신규: 손절(-7%) + 트레일링스탑(고점-10%, 고점+10%이상일 때) + 익절(+20/25%)

※ 유니버스: KOSPI200+KOSDAQ150+ETF (ohlcv_cache 기준, 820일 이상 종목)
※ 거래량 데이터: 후반 ~300일만 유효. 앞 구간은 가격 기반 VCP만 검증

Usage:
    python backtest/run_s4.py
    python backtest/run_s4.py --capital 50000000
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 전략 파라미터 ─────────────────────────────────────────────────────
MAX_SLOTS          = 4
SLOT_RATIO         = 0.20
STOP_LOSS          = 0.07
TAKE_PROFIT        = 0.20
TAKE_PROFIT_EXT    = 0.25
EARLY_GAIN_TRIGGER = 0.15
EARLY_GAIN_DAYS    = 15      # 영업일 ≈ 21 캘린더일
TIME_STOP_DAYS     = 40      # 원본 파라미터 (영업일)
TRAIL_STOP_PCT     = 0.10    # 트레일링스탑: 고점 대비 -10%
TRAIL_STOP_MIN     = 0.10    # 트레일링스탑 활성화 최소 고점 수익률

# ── 거래비용 ──────────────────────────────────────────────────────────
BUY_FEE  = 0.00015
SELL_FEE = 0.00015 + 0.002

# ── SEPA 파라미터 ─────────────────────────────────────────────────────
VCP_BASE_DAYS  = 60
VCP_STAGE_DAYS = 20
VCP_MAX_TIGHT  = 8.0    # 타이트 구간 최대 변동폭 %
VCP_MIN_BASE   = 5.0    # 최소 base 변동폭 %
VCP_VOL_SHRINK = 0.80   # 거래량 수축 기준
BREAKOUT_VOL_X = 1.50   # 브레이크아웃 거래량 배수 (50일 평균 대비)
RS_MIN         = 70.0   # RS 퍼센타일 최소값

KODEX200_CODE  = "069500"
OHLCV_DAYS     = 820


# ── 데이터 로드 ───────────────────────────────────────────────────────
def _load_data() -> dict:
    ohlcv_path   = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"
    canslim_path = Path(__file__).parent.parent / "data" / "canslim_ohlcv_cache.json"

    if not ohlcv_path.exists():
        print(f"[오류] {ohlcv_path} 없음 — ma-batch 먼저 실행 필요")
        sys.exit(1)

    raw_ohlcv   = json.loads(ohlcv_path.read_text(encoding="utf-8"))
    raw_canslim = json.loads(canslim_path.read_text(encoding="utf-8")) if canslim_path.exists() else {}

    stocks = {}
    for code, data in raw_ohlcv.items():
        closes = data.get("closes", [])
        opens  = data.get("opens",  [])
        if len(closes) < OHLCV_DAYS or len(opens) < OHLCV_DAYS:
            continue

        # volumes: canslim 캐시의 마지막 n일이 ohlcv 뒤쪽 n일과 정렬됨
        c_vols = raw_canslim.get(code, {}).get("volumes", [])
        n_vol  = len(c_vols)
        # ohlcv[OHLCV_DAYS - n_vol + j] ↔ c_vols[j]
        vol_offset = OHLCV_DAYS - n_vol   # ohlcv 인덱스 → canslim 인덱스 변환에 사용

        stocks[code] = {
            "closes":     np.array(closes[-OHLCV_DAYS:], dtype=float),
            "opens":      np.array(opens[-OHLCV_DAYS:],  dtype=float),
            "volumes":    np.array(c_vols, dtype=float),
            "vol_offset": vol_offset,
        }
    return stocks


# ── MA 사전 계산 ──────────────────────────────────────────────────────
def _precompute_mas(stocks: dict) -> None:
    for data in stocks.values():
        c = pd.Series(data["closes"])
        data["mas"] = {
            "ma21":  c.rolling(21).mean().values,
            "ma50":  c.rolling(50).mean().values,
            "ma62":  c.rolling(62).mean().values,
            "ma150": c.rolling(150).mean().values,
            "ma200": c.rolling(200).mean().values,
        }


# ── 트렌드 템플릿 T1~T7 ───────────────────────────────────────────────
def _check_tt(closes: np.ndarray, mas: dict, i: int) -> bool:
    """Minervini 트렌드 템플릿 7개 조건 모두 충족"""
    ma50, ma150, ma200 = mas["ma50"][i], mas["ma150"][i], mas["ma200"][i]
    if np.isnan(ma50) or np.isnan(ma150) or np.isnan(ma200):
        return False

    price = closes[i]
    T1 = price > ma150 and price > ma200
    T2 = ma150 > ma200

    # T3: MA200 상승추세 (20거래일 전보다 높음)
    ma200_20 = mas["ma200"][i - 20] if i >= 220 else np.nan
    T3 = not np.isnan(ma200_20) and ma200 > ma200_20

    T4 = ma50 > ma150 and ma50 > ma200
    T5 = price > ma50

    lookback = min(252, i + 1)
    segment  = closes[i + 1 - lookback : i + 1]
    hi_52w, lo_52w = segment.max(), segment.min()
    T6 = lo_52w > 0 and price >= lo_52w * 1.25
    T7 = hi_52w > 0 and price >= hi_52w * 0.75

    return T1 and T2 and T3 and T4 and T5 and T6 and T7


# ── VCP 패턴 + 브레이크아웃 ───────────────────────────────────────────
def _check_vcp_breakout(closes: np.ndarray, volumes: np.ndarray,
                        vol_offset: int, i: int) -> tuple[bool, int]:
    """
    VCP 감지 + 브레이크아웃 확인.
    반환: (breakout_confirmed, pivot)
    거래량 데이터 없는 구간은 가격 조건만 적용 (완화).
    """
    # tight 구간은 오늘 종가 제외 (i-20 ~ i-1)
    # 오늘 종가를 tight에 포함하면 pivot >= closes[i] → breakout 조건 항상 False
    if i < VCP_BASE_DAYS + 1:
        return False, 0

    early = closes[i - 60 : i - 40]   # 20일 (61 ~ 42일 전)
    mid   = closes[i - 40 : i - 20]   # 20일 (41 ~ 22일 전)
    tight = closes[i - 20 : i]         # 20일 (21 ~ 1일 전, 오늘 제외)

    def rng(seg):
        h, l = seg.max(), seg.min()
        return (h - l) / h * 100 if h > 0 else 0.0

    er, mr, tr = rng(early), rng(mid), rng(tight)

    # 가격 수축: early > mid > tight, tight ≤ 8%, base ≥ 5%
    if not (er > mr > 0 and mr > tr and tr <= VCP_MAX_TIGHT and er >= VCP_MIN_BASE):
        return False, 0

    pivot = int(tight.max())

    # 거래량 수축 (데이터 있을 때만, 오늘 제외)
    vi = i - vol_offset   # canslim volumes 내 인덱스 (오늘)
    has_vol = 0 <= vi < len(volumes)

    if has_vol and vi >= VCP_BASE_DAYS + 1:
        v_early = volumes[vi - 60 : vi - 40].mean()
        v_tight = volumes[vi - 20 : vi].mean()        # 오늘 제외
        if v_tight >= v_early * VCP_VOL_SHRINK:
            return False, pivot   # 거래량 수축 미충족

    # 브레이크아웃: 오늘 종가 > 피벗 (오늘 제외 20일 최고가)
    if closes[i] <= pivot:
        return False, pivot

    # 브레이크아웃 거래량 급증 (오늘 거래량 vs 50일 평균, 데이터 있을 때만)
    if has_vol and vi >= 50:
        vol_avg50 = volumes[max(0, vi - 50) : vi].mean()
        if vol_avg50 > 0 and volumes[vi] < vol_avg50 * BREAKOUT_VOL_X:
            return False, pivot

    return True, pivot


# ── 포트폴리오 시뮬레이션 ─────────────────────────────────────────────
def run_backtest(initial_capital: int = 10_000_000,
                 use_trail: bool = False,
                 use_ma_exit: bool = False) -> tuple[list, list]:
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")

    if KODEX200_CODE not in stocks:
        print(f"[오류] KODEX200({KODEX200_CODE}) 없음")
        sys.exit(1)

    print("MA 사전 계산 중...")
    _precompute_mas(stocks)

    capital   = float(initial_capital)
    positions = {}   # code → {entry_i, entry_price, qty, peak, days_held, early_triggered}
    trades    = []
    port_hist = []

    # MA200(200일) + T3 lookback(20일) = 최소 220일 필요
    start = 221
    end   = OHLCV_DAYS - 1   # opens[i+1] 접근 가능한 마지막

    if use_ma_exit:
        mode_str = "MA이탈(+20%이상) + 트레일링(미만)"
    elif use_trail:
        mode_str = "트레일링스탑"
    else:
        mode_str = f"타임스탑({TIME_STOP_DAYS}영업일)"
    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end - start) / 252:.1f}년, 모드: {mode_str})\n")

    for i in range(start, end):
        # 현재 포지션 평가
        pos_val = sum(
            stocks[c]["closes"][i] * p["qty"]
            for c, p in positions.items()
            if c in stocks
        )
        total_eval = capital + pos_val

        # ── 매도 신호 (i 종가 기준, i+1 시가 체결) ──────────────────
        for code in list(positions):
            if code not in stocks:
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

            reason    = None
            peak_gain = (pos["peak"] - entry) / entry

            if gain <= -STOP_LOSS:
                reason = "손절"
            elif use_ma_exit and peak_gain >= TAKE_PROFIT:
                # +20% 이상 도달한 종목: MA21 < MA62 이고 MA62 5일 하락 시 청산
                mas   = stocks[code]["mas"]
                ma21  = mas["ma21"][i]
                ma62  = mas["ma62"][i]
                ma62p = mas["ma62"][i - 5] if i >= 5 else ma62
                if (not np.isnan(ma21) and not np.isnan(ma62) and not np.isnan(ma62p)
                        and ma21 < ma62 and ma62 < ma62p):
                    reason = "MA이탈"
            elif not use_ma_exit and gain >= target:
                reason = "익절"
            elif not use_ma_exit and use_trail and peak_gain >= TRAIL_STOP_MIN and cur < pos["peak"] * (1 - TRAIL_STOP_PCT):
                reason = "트레일링스탑"
            elif not use_ma_exit and not use_trail and days >= TIME_STOP_DAYS:
                reason = "타임스탑"

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

        # ── 매수 신호 ─────────────────────────────────────────────────
        if len(positions) < MAX_SLOTS:
            # RS 퍼센타일 계산 (63일 수익률 기준)
            rs_raw = {
                code: data["closes"][i] / data["closes"][i - 63] - 1
                for code, data in stocks.items()
            }
            sorted_rs   = sorted(rs_raw, key=lambda c: rs_raw[c])
            n_rs        = len(sorted_rs)
            rs_pct      = {code: rank / n_rs * 100
                           for rank, code in enumerate(sorted_rs)}

            slot_budget = total_eval * SLOT_RATIO

            for code, data in stocks.items():
                if len(positions) >= MAX_SLOTS:
                    break
                if code in positions or code == KODEX200_CODE:
                    continue

                # RS 필터
                if rs_pct.get(code, 0) < RS_MIN:
                    continue

                # 트렌드 템플릿
                if not _check_tt(data["closes"], data["mas"], i):
                    continue

                # VCP + 브레이크아웃
                breakout, _pivot = _check_vcp_breakout(
                    data["closes"], data["volumes"], data["vol_offset"], i
                )
                if not breakout:
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
            if c in stocks
        )
        port_hist.append(capital + pos_val)

    return trades, port_hist


# ── 결과 집계 ─────────────────────────────────────────────────────────
def _stats(trades: list, port_hist: list, initial_capital: int) -> dict:
    if not port_hist:
        return {}
    final   = port_hist[-1]
    n_days  = len(port_hist)
    years   = n_days / 252
    cagr    = (final / initial_capital) ** (1 / max(years, 0.01)) - 1
    arr     = np.array(port_hist)
    peak    = np.maximum.accumulate(arr)
    mdd     = float(((arr - peak) / peak).min())
    wins    = [t for t in trades if t["pnl_pct"] > 0]
    losses  = [t for t in trades if t["pnl_pct"] <= 0]
    by_reason: dict = {}
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


def _print_comparison(scenarios: list[tuple[str, dict]], initial_capital: int) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  S4 SEPA 전략 백테스트 — 3가지 매도 전략 비교")
    print("=" * W)
    labels = [s[0] for s in scenarios]
    stats  = [s[1] for s in scenarios]
    header = f"  {'':28s}"
    for l in labels:
        header += f"  {l:>12s}"
    print(header)
    print("-" * W)

    def row(name, vals):
        s = f"  {name:28s}"
        for v in vals:
            s += f"  {v:>12s}"
        print(s)

    row("최종자산",     [f"{s['final']/1e6:.2f}M"    for s in stats])
    row("총 수익률",    [f"{s['total_r']:+.1%}"       for s in stats])
    row("CAGR",        [f"{s['cagr']:+.1%}"           for s in stats])
    row("MDD",         [f"{s['mdd']:+.1%}"            for s in stats])
    print("-" * W)
    row("매매 횟수",    [f"{s['n_trades']}회"          for s in stats])
    row("승률",         [f"{s['wr']:.1f}%"             for s in stats])
    row("평균수익(승)", [f"{s['avg_win']:+.1%}"        for s in stats])
    row("평균손실(패)", [f"{s['avg_loss']:+.1%}"       for s in stats])
    row("평균 보유일",  [f"{s['avg_days']:.1f}d"       for s in stats])
    print("=" * W)

    print("\n  매도 사유별:")
    all_reasons = sorted(set(r for s in stats for r in s.get("by_reason", {})))
    hdr = f"  {'사유':12s}"
    for l in labels:
        hdr += f"  {l:>16s}"
    print(hdr)
    for reason in all_reasons:
        line = f"  {reason:12s}"
        for s in stats:
            br = s.get("by_reason", {}).get(reason)
            line += f"  {f'{br[0]}회 {br[1]:+.1%}' if br else '—':>16s}"
        print(line)

    print("\n" + "=" * W)
    print("  ※ 타임스탑:   손절(-7%) + 타임스탑(40영업일) + 익절(+20/25%)")
    print(f"  ※ 트레일링:   손절(-7%) + 트레일링(고점-{TRAIL_STOP_PCT:.0%}, >{TRAIL_STOP_MIN:.0%}활성) + 익절(+20/25%)")
    print("  ※ MA이탈:     손절(-7%) + [<+20%] 트레일링 / [≥+20%] MA21<MA62+MA62하락")
    print("  ※ 유니버스: KOSPI200+KOSDAQ150+ETF (~405종목, 820일 이상)")
    print("=" * W + "\n")


def main():
    parser = argparse.ArgumentParser(description="S4 SEPA 전략 백테스트")
    parser.add_argument("--capital", type=int, default=10_000_000,
                        help="초기자본 (기본: 1,000만원)")
    args = parser.parse_args()

    runs = [
        ("타임스탑",  dict(use_trail=False, use_ma_exit=False)),
        ("트레일링",  dict(use_trail=True,  use_ma_exit=False)),
        ("MA이탈",    dict(use_trail=True,  use_ma_exit=True)),
    ]

    results = []
    for label, kwargs in runs:
        print(f"\n[{label}] 시뮬레이션...")
        trades, port_hist = run_backtest(args.capital, **kwargs)
        s = _stats(trades, port_hist, args.capital)
        results.append((label, s))
        print(f"  완료: {s['n_trades']}회 매매, 수익률 {s['total_r']:+.1%}")

    _print_comparison(results, args.capital)


if __name__ == "__main__":
    main()
