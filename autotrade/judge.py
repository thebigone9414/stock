"""autotrade/judge.py — 20:10 판정 (engine_v3 호출 → orders_YYYYMMDD.json)

흐름:
  1. engine_v3.daily_v3.judge(dep=ETF 몫) → ETF 신호/보유 (dict with list positions/signals)
  2. engine_v3.stock_rules.judge(capital=종목 몫) → 종목 신호/보유 (DataFrame positions/signals/buys)
  3. 내일 매도 리스트 (오늘 종가로 청산·손절 조건 충족한 것)
     내일 매수 리스트 (오늘 신규 신호, 빈 자리에 맞춰 선정)
  4. orders_YYYYMMDD.json 저장 (집행일 = 다음 거래일)

CAPITAL 모드:
  compound=false (사용자 지시): capital.initial × share 로 매번 재계산.
    - ETF 몫 2800만 (롱 1건=560만, 숏=1120만)
    - 종목 몫 1200만 (1건=120만)
  compound=true (지시서 v12 원안): 평가자산 기반. 다음 버전.

실행:
  python -m autotrade.judge
  python -m autotrade.judge --date YYYY-MM-DD   # 리플레이 (검수)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from autotrade import common, config as cfg, state as S


def _import_engine():
    engine_dir = cfg.abspath(cfg.get("data.engine_dir"))
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    import daily_v3            # type: ignore
    import stock_lib           # type: ignore
    import stock_rules         # type: ignore
    return daily_v3, stock_lib, stock_rules


def _get_dep_capital() -> tuple[int, int]:
    """(etf_dep, stock_dep) — compound=false면 initial × share."""
    initial = cfg.get("capital.initial")
    etf = int(initial * cfg.get("capital.etf_share"))
    stk = int(initial * cfg.get("capital.stock_share"))
    if cfg.get("capital.compound", False):
        logger.warning("[judge] compound=true 는 아직 미지원 — 고정 모드로 처리")
    return etf, stk


def _next_trading_date(from_date: str) -> str:
    from data.holidays import is_trading_day
    d = datetime.strptime(from_date, "%Y-%m-%d").date() + timedelta(days=1)
    while not is_trading_day(d):
        d = d + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _mk_key(exec_date: str, code: str, tag: str) -> str:
    return f"{exec_date.replace('-', '')}:{code}:{tag}"


def _etf_vehicle_from_dir(dr: int) -> tuple[str, str]:
    """dr=1 롱(KODEX 레버리지), dr=-1 숏(KODEX 인버스). 반환 (code, name)."""
    if dr == -1:
        return cfg.get("etf.short_code"), cfg.get("etf.short_name")
    return cfg.get("etf.long_code"), cfg.get("etf.long_name")


def _build_etf_orders(etf: dict, exec_date: str, etf_dep: int) -> dict:
    """daily_v3.judge 반환을 매도/매수 리스트로 변환.

    etf['positions'] 각 dict: pat, dir, name, entry_date, entry_px, qty, stop,
                              pnl_pct, stop_hit, exit_signal, exit_desc
    etf['signals'] 각 dict: pat, dir, name, stop, exit_desc, vehicle, frac, amount, dup
    """
    sells, buys = [], []

    for pos in etf.get("positions", []):
        if not (pos.get("exit_signal") or pos.get("stop_hit")):
            continue
        reason = "손절" if pos.get("stop_hit") else "청산"
        code, name = _etf_vehicle_from_dir(int(pos.get("dir", 1)))
        sells.append({
            "rule":        "etf",
            "code":        code,
            "name":        name,
            "quantity":    int(pos.get("qty") or 0),
            "entry_date":  str(pos.get("entry_date") or ""),
            "pat":         int(pos.get("pat")) if pos.get("pat") is not None else None,
            "dir":         int(pos.get("dir", 1)),
            "pattern_name": pos.get("name"),
            "reason":      f"ETF {reason}",
            "entry_px":    float(pos.get("entry_px") or 0),  # 지수 기준
            "stop_level":  float(pos.get("stop") or 0),
            "pnl_pct":     float(pos.get("pnl_pct") or 0),
            "order_key":   _mk_key(exec_date, code, f"etf_{reason}_pat{pos.get('pat')}"),
        })

    # 신규 신호 — free_slots 만큼
    free_slots = int(etf.get("free_slots", 0))
    taken = 0
    for sig in etf.get("signals", []):
        if sig.get("dup") or taken >= free_slots:
            continue
        dr = int(sig.get("dir", 1))
        code, name = _etf_vehicle_from_dir(dr)
        amount = int(sig.get("amount", 0))
        buys.append({
            "rule":        "etf",
            "code":        code,
            "name":        name,
            "amount":      amount,       # engine이 dep × frac(0.2/0.4) 로 계산해 넘김
            "pat":         int(sig.get("pat")),
            "dir":         dr,
            "pattern_name": sig.get("name"),
            "signal_date": str(etf.get("date") or ""),
            "entry_px":    float(etf.get("close") or 0),  # 신호일 지수 종가
            "stop_level":  float(sig.get("stop") or 0),
            "exit_desc":   sig.get("exit_desc"),
            "vehicle":     sig.get("vehicle"),  # 'KODEX 레버리지' / 'KODEX 인버스'
            "frac":        float(sig.get("frac", 0)),
            "order_key":   _mk_key(exec_date, code, f"etf_buy_pat{sig.get('pat')}"),
        })
        taken += 1

    return {"sell": sells, "buy": buys}


def _build_stock_orders(stk: dict, exec_date: str) -> dict:
    """stock_rules.judge 반환을 매도/매수 리스트로 변환.
    positions/signals/buys 모두 DataFrame.
    """
    sells, buys = [], []
    pos_df = stk.get("positions")
    buys_df = stk.get("buys")

    if pos_df is not None and len(pos_df):
        for _, r in pos_df.iterrows():
            if not (r.get("exit_signal") or r.get("stop_hit")):
                continue
            reason = "손절" if r.get("stop_hit") else "청산(62일선 이탈)"
            code = str(r.get("code", "")).zfill(6)
            sells.append({
                "rule":        "stock",
                "code":        code,
                "name":        r.get("name"),
                "pattern":     r.get("패턴"),
                "entry_date":  str(r.get("매수일") or ""),
                "entry_px":    float(r.get("매수가") or 0),
                "stop_level":  float(r.get("손절가") or 0),
                "close":       float(r.get("종가") or 0),
                "ma62":        float(r.get("ma62") or 0),
                "low":         float(r.get("저가") or 0),
                "pnl_pct":     float(r.get("pnl_pct") or 0),
                "reason":      f"종목 {reason}",
                "action":      r.get("action"),
                "order_key":   _mk_key(exec_date, code, f"stock_{reason.split('(')[0]}"),
            })

    if buys_df is not None and len(buys_df):
        for _, b in buys_df.iterrows():
            code = str(b.get("code", "")).zfill(6)
            buys.append({
                "rule":       "stock",
                "code":       code,
                "name":       b.get("name"),
                "card":       b.get("카드"),       # 'B1,B2,B3' 같은 조합
                "pattern":    b.get("패턴"),
                "close":      float(b.get("종가") or 0),
                "stop_level": float(b.get("손절") or 0),
                "stop_pct":   float(b.get("손절폭") or 0),
                "r120":       float(b.get("r120") or 0),  # 6개월 수익률
                "ma62":       float(b.get("ma62") or 0),
                "amt_rank":   float(b.get("amt") or 0),  # 20일 평균 거래대금(억)
                "amount":     int(b.get("amount") or 0),
                "qty":        int(b.get("qty") or 0),
                "order_key":  _mk_key(exec_date, code, "stock_buy"),
            })

    return {"sell": sells, "buy": buys}


def _candidates_summary(stk: dict, top_n: int = 10) -> list[dict]:
    """국면 하락이거나 자리 없을 때 참고용으로 보고에 표시할 상위 후보."""
    S = stk.get("signals")
    if S is None or len(S) == 0:
        return []
    out = []
    for _, r in S.head(top_n).iterrows():
        out.append({
            "code":   str(r.get("code", "")).zfill(6),
            "name":   r.get("name"),
            "card":   r.get("카드"),
            "r120":   float(r.get("r120") or 0),
        })
    return out


def run(replay_date: str | None = None) -> dict:
    common.setup()
    today = replay_date or common.today_str()
    exec_date = _next_trading_date(today)

    etf_dep, stock_dep = _get_dep_capital()
    logger.info(f"[judge] 판정 기준일 {today}  집행 예정일 {exec_date}")
    logger.info(f"[judge] CAPITAL compound={cfg.get('capital.compound')}  ETF_dep={etf_dep:,}  Stock_dep={stock_dep:,}")

    daily_v3, stock_lib, stock_rules = _import_engine()

    # ETF 판정
    logger.info(f"[judge] daily_v3.judge(dep={etf_dep:,})")
    etf = daily_v3.judge(dep=etf_dep)
    logger.info(
        f"[judge] ETF {etf['date']} close={etf['close']:.2f} ({etf['chg']*100:+.2f}%)  "
        f"positions={len(etf.get('positions', []))} signals={len(etf.get('signals', []))} "
        f"near={len(etf.get('near', []))} free_slots={etf.get('free_slots')}"
    )

    # 종목 패널 빌드 + 판정
    logger.info("[judge] stock_lib.build_panel() + stock_rules.judge(capital=...)")
    P = stock_lib.build_panel()
    stk = stock_rules.judge(P=P, capital=stock_dep)
    logger.info(
        f"[judge] 종목 regime_on={stk.get('regime_on')}  ma62={stk.get('ma62'):.2f} vs 20일전 {stk.get('ma62_20ago'):.2f}  "
        f"positions={len(stk.get('positions', []))} signals={len(stk.get('signals', []))} "
        f"buys={len(stk.get('buys', []))}  per_slot={stk.get('per_slot')}"
    )

    etf_orders = _build_etf_orders(etf, exec_date, etf_dep)
    stk_orders = _build_stock_orders(stk, exec_date)

    orders = {
        "generated_at":  common.now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "judge_date":    today,
        "exec_date":     exec_date,
        "capital_mode":  "fixed" if not cfg.get("capital.compound") else "compound",
        "capital_initial": cfg.get("capital.initial"),
        "etf_dep":       etf_dep,
        "stock_dep":     stock_dep,
        "regime_on":     bool(stk.get("regime_on", False)),
        "regime_detail": {
            "ma62":       float(stk.get("ma62", 0)),
            "ma62_20ago": float(stk.get("ma62_20ago", 0)),
        },
        "k200": {
            "date":  str(etf.get("date")),
            "close": float(etf.get("close", 0)),
            "chg":   float(etf.get("chg", 0)),
            "ma62":  float(etf.get("ma62", 0)),
            "ma248": float(etf.get("ma248", 0)),
            "rsi":   float(etf.get("rsi", 0)),
            "atr":   float(etf.get("atr", 0)),
        },
        "sell": {
            "etf":   etf_orders["sell"],
            "stock": stk_orders["sell"],
        },
        "buy": {
            "etf":   etf_orders["buy"],
            "stock": stk_orders["buy"],
        },
        "etf_near":        etf.get("near", []),
        "stock_candidates": _candidates_summary(stk, 10),
    }

    p = S.save_orders(exec_date, orders)
    logger.info(
        f"[judge] orders 저장: {p}  "
        f"매도 ETF:{len(etf_orders['sell'])} 종목:{len(stk_orders['sell'])}  "
        f"매수 ETF:{len(etf_orders['buy'])} 종목:{len(stk_orders['buy'])}"
    )
    return orders


def main() -> int:
    p = argparse.ArgumentParser(description="autotrade/judge")
    p.add_argument("--date", help="리플레이 기준일 YYYY-MM-DD (검수용)")
    args = p.parse_args()
    try:
        run(replay_date=args.date)
        return 0
    except Exception as e:
        logger.exception(f"[judge] 실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
