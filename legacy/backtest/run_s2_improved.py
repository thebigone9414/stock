#!/usr/bin/env python3
"""
S2 MA 이평선 전략 백테스트 — 개선 버전

[원본 대비 변경 사항]
1. 타임스탑: 56 영업일 → 40 영업일 (실제 전략의 56 캘린더일 ≈ 40 영업일에 맞춤)
2. 조기익절 판단: 21 영업일 → 15 영업일 (실제 전략의 21 캘린더일에 맞춤)
3. 종목 우선순위: dict 순서 → 63일 RS(상대강도) 높은 순으로 슬롯 배정
4. 시장 필터(M): KODEX200 MA20 < MA60 전환 시 전체 포지션 강제 청산 (★ 추가)
   → 매수 차단은 S2 정배열 조건이 이미 처리 — 시장 붕괴 보호가 실질적 개선

Usage:
    python backtest/run_s2_improved.py
    python backtest/run_s2_improved.py --capital 50000000 --compare
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 전략 파라미터 ─────────────────────────────────────────────────────────
MAX_SLOTS          = 4
SLOT_RATIO         = 0.20
STOP_LOSS          = 0.07
TAKE_PROFIT        = 0.20
TAKE_PROFIT_EXT    = 0.25
EARLY_GAIN_TRIGGER = 0.15
EARLY_GAIN_DAYS    = 15      # ★ 개선: 21 영업일 → 15 영업일 (≈ 21 캘린더일)
TIME_STOP_DAYS     = 40      # ★ 개선: 56 영업일 → 40 영업일 (≈ 56 캘린더일)
MA_TREND_LOOKBACK  = 5

KODEX200_CODE      = "069500"  # ★ 시장 필터용
MARKET_MA_FAST     = 20        # ★ 시장 필터: MA20 > MA60
MARKET_MA_SLOW     = 60

# ★ v3 (S3/S4 통일 매도): 트레일링스탑 + MA이탈 러너 (타임스탑 없음)
RUNNER_THRESHOLD   = 0.20   # 고점 +20% 이상 → 러너 모드 (MA이탈 청산)
TRAIL_STOP_PCT     = 0.10   # 트레일링스탑 폭: 고점 대비 -10%
TRAIL_STOP_MIN     = 0.10   # 트레일링스탑 활성화 최소 고점 수익률

BUY_FEE  = 0.00015
SELL_FEE = 0.00015 + 0.002

MIN_DATA_DAYS = 820


# ── 데이터 로드 ───────────────────────────────────────────────────────────
def _load_data() -> dict:
    path = Path(__file__).parent.parent / "data" / "ohlcv_cache.json"
    if not path.exists():
        print(f"[오류] {path} 파일 없음")
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


def _market_uptrend(kodex_closes: np.ndarray, i: int) -> bool:
    """KODEX200 MA20 > MA60 → 시장 상승추세"""
    if i < MARKET_MA_SLOW - 1:
        return True   # 데이터 부족 시 필터 비활성
    ma_fast = kodex_closes[i - MARKET_MA_FAST + 1 : i + 1].mean()
    ma_slow = kodex_closes[i - MARKET_MA_SLOW + 1 : i + 1].mean()
    return bool(ma_fast > ma_slow)


def _market_turned_bearish(kodex_closes: np.ndarray, i: int) -> bool:
    """오늘 MA20 < MA60 이고 어제는 MA20 >= MA60 → 시장 하락 전환"""
    if i < MARKET_MA_SLOW:
        return False
    today_up   = _market_uptrend(kodex_closes, i)
    yest_up    = _market_uptrend(kodex_closes, i - 1)
    return (not today_up) and yest_up


def _golden_align(mas: dict, i: int) -> bool:
    m5, m21, m62 = mas["ma5"][i], mas["ma21"][i], mas["ma62"][i]
    m248, m744   = mas["ma248"][i], mas["ma744"][i]
    if np.isnan(m62) or np.isnan(m248):
        return False
    if not np.isnan(m744):
        return m5 > m21 > m62 > m248 > m744
    return m5 > m21 > m62 > m248


def _is_first_golden_day(mas: dict, i: int) -> bool:
    return _golden_align(mas, i) and not _golden_align(mas, i - 1)


def _is_trending_up(mas: dict, i: int) -> bool:
    lb = MA_TREND_LOOKBACK
    m62, m62p   = mas["ma62"][i], mas["ma62"][i - lb]
    m248, m248p = mas["ma248"][i], mas["ma248"][i - lb]
    if np.isnan(m62) or np.isnan(m62p):
        return False
    if np.isnan(m248) or np.isnan(m248p):
        return m62 > m62p
    return m62 > m62p and m248 > m248p


def _is_deadcross(mas: dict, i: int) -> bool:
    m21, m62  = mas["ma21"][i], mas["ma62"][i]
    m62_prev  = mas["ma62"][i - MA_TREND_LOOKBACK]
    if np.isnan(m21) or np.isnan(m62) or np.isnan(m62_prev):
        return False
    return m21 < m62 and m62 < m62_prev


# ── RS 점수 (63일 수익률) ─────────────────────────────────────────────────
def _rs_score(closes: np.ndarray, i: int) -> float:
    if i < 63:
        return -999.0
    return float(closes[i] / closes[i - 63] - 1)


# ── 시뮬레이션 ────────────────────────────────────────────────────────────
def run_backtest(initial_capital: int = 10_000_000,
                 use_market_exit: bool = True) -> tuple[list, list]:
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")
    print("MA 계산 중...")
    _precompute_mas(stocks)

    if KODEX200_CODE not in stocks:
        print(f"[오류] KODEX200({KODEX200_CODE}) 없음")
        sys.exit(1)
    kodex_closes = stocks[KODEX200_CODE]["closes"]

    capital   = float(initial_capital)
    positions = {}
    trades    = []
    port_hist = []

    start = 248 + MA_TREND_LOOKBACK + 1
    end   = MIN_DATA_DAYS - 1

    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end - start) / 252:.1f}년)\n")

    for i in range(start, end):
        pos_val    = sum(stocks[c]["closes"][i] * p["qty"]
                         for c, p in positions.items() if c in stocks)
        total_eval = capital + pos_val

        # ── 시장 하락 전환 → 전체 포지션 강제 청산 (i+1 시가) ─────────
        if use_market_exit and _market_turned_bearish(kodex_closes, i):
            for code in list(positions):
                if code not in stocks:
                    del positions[code]
                    continue
                pos      = positions[code]
                sell_px  = stocks[code]["opens"][i + 1]
                received = sell_px * pos["qty"] * (1 - SELL_FEE)
                capital += received
                trades.append({
                    "code":        code,
                    "entry_i":     pos["entry_i"],
                    "exit_i":      i + 1,
                    "entry_price": pos["entry_price"],
                    "exit_price":  sell_px,
                    "qty":         pos["qty"],
                    "pnl_pct":     (sell_px - pos["entry_price"]) / pos["entry_price"],
                    "reason":      "시장하락전환",
                    "days_held":   pos["days_held"],
                })
                del positions[code]

        # ── 매도 ──────────────────────────────────────────────────────
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

        # ── 매수: RS 높은 순 슬롯 배정 (v2는 시장 필터 추가) ────────────
        market_ok = (not use_market_exit) or _market_uptrend(kodex_closes, i)
        if len(positions) < MAX_SLOTS and market_ok:
            slot_budget = total_eval * SLOT_RATIO

            # 1단계: 조건 충족 후보 수집
            candidates = []
            for code, data in stocks.items():
                if code in positions:
                    continue
                mas = data["mas"]
                if not _is_first_golden_day(mas, i):
                    continue
                if data["closes"][i] <= data["opens"][i]:   # 양봉만
                    continue
                if not _is_trending_up(mas, i):
                    continue
                buy_px = data["opens"][i + 1]
                if buy_px <= 0:
                    continue
                candidates.append((code, _rs_score(data["closes"], i)))

            # 2단계: ★ RS 높은 순 정렬
            candidates.sort(key=lambda x: x[1], reverse=True)

            # 3단계: 상위 후보부터 슬롯 채우기
            for code, _rs in candidates:
                if len(positions) >= MAX_SLOTS:
                    break
                data     = stocks[code]
                buy_px   = data["opens"][i + 1]
                qty      = int(slot_budget / (buy_px * (1 + BUY_FEE)))
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

        pos_val = sum(stocks[c]["closes"][i] * p["qty"]
                      for c, p in positions.items() if c in stocks)
        port_hist.append(capital + pos_val)

    return trades, port_hist


# ── 결과 리포트 ───────────────────────────────────────────────────────────
def _report(trades: list, port_hist: list, initial_capital: int,
            label: str = "") -> dict:
    if not port_hist:
        print("시뮬레이션 결과 없음")
        return {}

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
    title = f"S2 MA 이평선 전략 백테스트 결과 {label}"
    print("\n" + "=" * W)
    print(f"  {title}")
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
        print(f"    {reason:10s}: {len(pnls):3d}회  평균 {avg:+.2%}")
    print("=" * W)

    return {"cagr": cagr, "mdd": mdd, "wr": len(wins)/len(trades) if trades else 0,
            "n_trades": len(trades), "total_r": total_r}


def run_backtest_v3(initial_capital: int = 10_000_000) -> tuple[list, list]:
    """v3: S3/S4 통일 매도 전략 (타임스탑 제거, 트레일링스탑 + MA이탈 러너)

    매도 조건 (S3/S4와 동일):
      ① 손절: 진입가 대비 -7%
      ② 러너(고점+20% 이상): MA21 < MA62 && MA62 5일 하락 → MA이탈 청산
      ③ 비러너(고점+10% 이상): 고점 대비 -10% 트레일링스탑
      ④ 비러너 익절: +20% (조기 +15%/15일 달성 시 +25%)
    매수 조건: v1과 동일 (정배열 첫날 + RS 우선순위)
    """
    print("데이터 로딩 중...")
    stocks = _load_data()
    print(f"  → {len(stocks)}종목 로드")
    print("MA 계산 중...")
    _precompute_mas(stocks)

    capital   = float(initial_capital)
    positions = {}
    trades    = []
    port_hist = []

    start = 248 + MA_TREND_LOOKBACK + 1
    end   = MIN_DATA_DAYS - 1

    print(f"시뮬레이션 시작 (구간: {end - start}영업일 ≈ {(end - start) / 252:.1f}년)\n")

    for i in range(start, end):
        pos_val    = sum(stocks[c]["closes"][i] * p["qty"]
                         for c, p in positions.items() if c in stocks)
        total_eval = capital + pos_val

        # ── 매도 (S3/S4 통일 매도 전략) ──────────────────────────────
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
            peak      = pos["peak"]
            peak_gain = (peak - entry) / entry

            # 조기익절 트리거 (15영업일 이내 +15%)
            if (not pos["early_triggered"]
                    and gain >= EARLY_GAIN_TRIGGER
                    and days <= EARLY_GAIN_DAYS):
                pos["early_triggered"] = True

            target = TAKE_PROFIT_EXT if pos["early_triggered"] else TAKE_PROFIT
            mas    = stocks[code]["mas"]

            reason = None
            # ① 손절 -7%
            if gain <= -STOP_LOSS:
                reason = "손절"
            # ② 러너(고점+20% 이상): MA이탈 청산
            elif peak_gain >= RUNNER_THRESHOLD:
                if _is_deadcross(mas, i):
                    reason = "MA이탈(러너)"
            # ③ 비러너: 트레일링스탑 (고점+10% 이상일 때 활성화)
            elif peak_gain >= TRAIL_STOP_MIN and cur < peak * (1 - TRAIL_STOP_PCT):
                reason = "트레일링스탑"
            # ④ 비러너: 익절
            elif gain >= target:
                reason = "익절"

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

        # ── 매수: RS 높은 순 슬롯 배정 (v1과 동일) ───────────────────
        if len(positions) < MAX_SLOTS:
            slot_budget = total_eval * SLOT_RATIO
            candidates  = []
            for code, data in stocks.items():
                if code in positions:
                    continue
                mas = data["mas"]
                if not _is_first_golden_day(mas, i):
                    continue
                if data["closes"][i] <= data["opens"][i]:
                    continue
                if not _is_trending_up(mas, i):
                    continue
                buy_px = data["opens"][i + 1]
                if buy_px <= 0:
                    continue
                candidates.append((code, _rs_score(data["closes"], i)))

            candidates.sort(key=lambda x: x[1], reverse=True)

            for code, _rs in candidates:
                if len(positions) >= MAX_SLOTS:
                    break
                data   = stocks[code]
                buy_px = data["opens"][i + 1]
                qty    = int(slot_budget / (buy_px * (1 + BUY_FEE)))
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

        pos_val = sum(stocks[c]["closes"][i] * p["qty"]
                      for c, p in positions.items() if c in stocks)
        port_hist.append(capital + pos_val)

    return trades, port_hist


def _run_orig(capital: int) -> dict:
    """원본 run_s2.py 를 직접 실행해서 통계 반환"""
    import backtest.run_s2 as orig
    trades, hist = orig.run_backtest(capital)
    if not hist:
        return {}
    final   = hist[-1]
    total_r = (final - capital) / capital
    years   = len(hist) / 252
    cagr    = (final / capital) ** (1 / max(years, 0.01)) - 1
    arr     = np.array(hist)
    mdd     = float(((arr - np.maximum.accumulate(arr)) / np.maximum.accumulate(arr)).min())
    wins    = [t for t in trades if t["pnl_pct"] > 0]
    wr      = len(wins) / len(trades) * 100 if trades else 0
    return {"cagr": cagr, "mdd": mdd, "wr": wr, "n_trades": len(trades),
            "total_r": total_r, "trades": trades, "hist": hist}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=int, default=10_000_000)
    args = parser.parse_args()

    cap = args.capital

    # ── 원본 ─────────────────────────────────────────────────────────────
    import backtest.run_s2 as orig
    print("─" * 57)
    print("  [1] 원본 (타임스탑56일, dict순서)")
    trades_o, hist_o = orig.run_backtest(cap)

    # ── 개선 v1: 타임스탑+RS 우선순위 (시장필터 없음) ─────────────────
    print("\n─" * 28)
    print("  [2] 개선v1 (타임스탑40일·RS우선순위)")
    trades_v1, hist_v1 = run_backtest(cap, use_market_exit=False)
    s_v1 = _report(trades_v1, hist_v1, cap, label="[v1: 타임스탑40일·RS우선순위]")

    # ── 개선 v2: v1 + 시장 필터 ─────────────────────────────────────
    print("\n─" * 28)
    print("  [3] 개선v2 (v1 + 시장필터 KODEX200 MA20>MA60 하락전환 강제청산)")
    trades_v2, hist_v2 = run_backtest(cap, use_market_exit=True)
    s_v2 = _report(trades_v2, hist_v2, cap, label="[v2: +시장필터]")

    # ── 개선 v3: S3/S4 통일 매도 (타임스탑 없음, 트레일링+MA이탈) ────
    print("\n─" * 28)
    print("  [4] v3 (S3/S4 통일 매도: 트레일링스탑+MA이탈 러너, 타임스탑 없음)")
    trades_v3, hist_v3 = run_backtest_v3(cap)
    s_v3 = _report(trades_v3, hist_v3, cap, label="[v3: 트레일링+MA이탈(S3/S4 동일)]")

    # ── 비교 테이블 ───────────────────────────────────────────────────
    # 원본 통계 직접 계산
    if hist_o:
        final_o = hist_o[-1]; yr_o = len(hist_o)/252
        cagr_o  = (final_o/cap)**(1/max(yr_o,0.01))-1
        arr_o   = np.array(hist_o)
        mdd_o   = float(((arr_o-np.maximum.accumulate(arr_o))/np.maximum.accumulate(arr_o)).min())
        wins_o  = [t for t in trades_o if t["pnl_pct"]>0]
        wr_o    = len(wins_o)/len(trades_o)*100 if trades_o else 0
        tr_o    = (final_o-cap)/cap
    else:
        cagr_o=mdd_o=wr_o=tr_o=0; trades_o=[]

    W = 72
    print("\n" + "=" * W)
    print(f"  {'':16s} {'원본':>11s} {'v1(타임+RS)':>12s} {'v2(+시장필터)':>13s} {'v3(통일매도)':>13s}")
    print("-" * W)
    rows = [
        ("총 수익률",
         f"{tr_o:>+10.2%}", f"{s_v1['total_r']:>+11.2%}",
         f"{s_v2['total_r']:>+12.2%}", f"{s_v3['total_r']:>+12.2%}"),
        ("CAGR",
         f"{cagr_o:>+10.2%}", f"{s_v1['cagr']:>+11.2%}",
         f"{s_v2['cagr']:>+12.2%}", f"{s_v3['cagr']:>+12.2%}"),
        ("MDD",
         f"{mdd_o:>+10.2%}", f"{s_v1['mdd']:>+11.2%}",
         f"{s_v2['mdd']:>+12.2%}", f"{s_v3['mdd']:>+12.2%}"),
        ("승률",
         f"{wr_o:>9.1f} %", f"{s_v1['wr']*100:>10.1f} %",
         f"{s_v2['wr']*100:>11.1f} %", f"{s_v3['wr']*100:>11.1f} %"),
        ("매매 횟수",
         f"{len(trades_o):>10}회", f"{s_v1['n_trades']:>11}회",
         f"{s_v2['n_trades']:>12}회", f"{s_v3['n_trades']:>12}회"),
    ]
    for lbl, o, v1, v2, v3 in rows:
        print(f"  {lbl:10s}  {o}  {v1}  {v2}  {v3}")
    print("=" * W + "\n")


if __name__ == "__main__":
    main()
