"""autotrade/judge.py — 20:10 판정 (v2, 2026-10-10)

흐름:
  1. engine_v3.daily_v3.judge(dep=A_initial) → 모듈 A (ETF 11패턴)
  2. engine_v3.mom_lib.judge(capital=B_initial, rebalance=?) → 모듈 B (월말 모멘텀)
     - 평일: rebalance=False → sells_daily (62일선 이탈) 만
     - 월말 전날: rebalance=True → sells_rebalance + buys
  3. orders_YYYYMMDD.json 저장 (집행일 = 다음 거래일)

CAPITAL:
  compound=false (사용자 선택): modules.A.initial / modules.B.initial 고정값.
  compound=true (지시서 v2 원안): 평가자산 기반. 다음 버전.

실행:
  python -m autotrade.judge
  python -m autotrade.judge --date YYYY-MM-DD   # 리플레이
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from autotrade import common, config as cfg, state as S


def _import_engine():
    engine_dir = cfg.abspath(cfg.get("data.engine_dir"))
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    import daily_v3   # type: ignore
    import stock_lib  # type: ignore
    import mom_lib    # type: ignore
    return daily_v3, stock_lib, mom_lib


def _get_module_capital() -> tuple[int, int]:
    """(A_capital, B_capital) 반환. compound=false면 initial 그대로."""
    a = int(cfg.get("modules.A.initial"))
    b = int(cfg.get("modules.B.initial"))
    if cfg.get("modules.A.compound", False) or cfg.get("modules.B.compound", False):
        logger.warning("[judge] compound=true 모드는 미지원 — initial 고정으로 처리")
    return a, b


def _mk_key(exec_date: str, module: str, code: str, tag: str) -> str:
    return f"{exec_date.replace('-', '')}:{module}:{code}:{tag}"


def _etf_vehicle_from_dir(dr: int) -> tuple[str, str]:
    """dr=1 롱, dr=-1 숏. (code, name) 반환."""
    if dr == -1:
        return cfg.get("etf.short_code"), cfg.get("etf.short_name")
    return cfg.get("etf.long_code"), cfg.get("etf.long_name")


# ── 모듈 A (ETF 11패턴) ─────────────────────────────────────────
def _build_A_orders(A: dict, exec_date: str) -> dict:
    """daily_v3.judge 반환 → 매도/매수 리스트.

    positions[i]: pat, dir, name, entry_date, entry_px, qty, stop,
                  pnl_pct, stop_hit (오늘 저가 통과),
                  exit_signal (패턴 청산), + stop_close (종가 손절)*
      *stop_close 는 v2 README 명시, 기존 engine 에 없을 수도 있어 안전 폴백.
    signals[i]: pat, dir, name, stop, exit_desc, vehicle, frac, amount, dup
    """
    sells, buys = [], []

    for pos in A.get("positions", []):
        # v2 종가 판정: stop_hit (장중 저가) 대신 stop_close (종가 손절) 우선
        has_stop_close = pos.get("stop_close")
        stop_fired = bool(has_stop_close if has_stop_close is not None else pos.get("stop_hit"))
        exit_fired = bool(pos.get("exit_signal"))
        if not (stop_fired or exit_fired):
            continue
        reason = "손절" if stop_fired else "청산"
        dr = int(pos.get("dir", 1))
        code, name = _etf_vehicle_from_dir(dr)
        sells.append({
            "rule":         "A",
            "code":         code,
            "name":         name,
            "quantity":     int(pos.get("qty") or 0),
            "entry_date":   str(pos.get("entry_date") or ""),
            "pat":          int(pos.get("pat")) if pos.get("pat") is not None else None,
            "dir":          dr,
            "pattern_name": pos.get("name"),
            "reason":       f"A {reason}",
            "entry_px":     float(pos.get("entry_px") or 0),
            "stop_level":   float(pos.get("stop") or 0),
            "pnl_pct":      float(pos.get("pnl_pct") or 0),
            "order_key":    _mk_key(exec_date, "A", code, f"sell_pat{pos.get('pat')}_{reason}"),
        })

    # 신규 신호 — free_slots 만큼 (dup=False 만)
    free_slots = int(A.get("free_slots", 0))
    taken = 0
    for sig in A.get("signals", []):
        if sig.get("dup") or taken >= free_slots:
            continue
        dr = int(sig.get("dir", 1))
        code, name = _etf_vehicle_from_dir(dr)
        amount = int(sig.get("amount", 0))
        buys.append({
            "rule":         "A",
            "code":         code,
            "name":         name,
            "amount":       amount,
            "pat":          int(sig.get("pat")),
            "dir":          dr,
            "pattern_name": sig.get("name"),
            "signal_date":  str(A.get("date") or ""),
            "entry_px":     float(A.get("close") or 0),
            "stop_level":   float(sig.get("stop") or 0),
            "exit_desc":    sig.get("exit_desc"),
            "vehicle":      sig.get("vehicle"),
            "frac":         float(sig.get("frac", 0)),
            "order_key":    _mk_key(exec_date, "A", code, f"buy_pat{sig.get('pat')}"),
        })
        taken += 1

    return {"sell": sells, "buy": buys}


# ── 모듈 B (월말 모멘텀) ────────────────────────────────────────
def _build_B_orders(B: dict, exec_date: str, is_rebalance: bool) -> dict:
    """mom_lib.judge 반환 → 매도/매수 리스트.

    매일: sells_daily (62일선 이탈) 만.
    월말 전날 (rebalance=True): sells_rebalance + buys 추가.
    """
    sells, buys = [], []

    # 매일: 62일선 이탈 매도 (sells_daily DataFrame)
    sdf = B.get("sells_daily")
    if sdf is not None and len(sdf):
        for _, r in sdf.iterrows():
            code = str(r.get("code", "")).zfill(6)
            sells.append({
                "rule":        "B",
                "code":        code,
                "name":        r.get("name"),
                "quantity":    int(r.get("수량") or 0),
                "entry_date":  str(r.get("매수일") or ""),
                "entry_px":    float(r.get("매수가") or 0),
                "close":       float(r.get("종가") or 0),
                "ma62":        float(r.get("ma62") or 0),
                "pnl_pct":     float(r.get("pnl_pct") or 0),
                "reason":      "B 62일선 이탈",
                "order_key":   _mk_key(exec_date, "B", code, "sell_ma62"),
            })

    if is_rebalance:
        # 월말 교체: sells_rebalance (순위 밖·국면 하락 전량 매도)
        rdf = B.get("sells_rebalance")
        if rdf is not None and len(rdf):
            for _, r in rdf.iterrows():
                code = str(r.get("code", "")).zfill(6)
                why = r.get("why", "월말 교체")
                sells.append({
                    "rule":        "B",
                    "code":        code,
                    "name":        r.get("name"),
                    "quantity":    int(r.get("수량") or 0),
                    "entry_date":  str(r.get("매수일") or ""),
                    "entry_px":    float(r.get("매수가") or 0),
                    "close":       float(r.get("종가") or 0),
                    "pnl_pct":     float(r.get("pnl_pct") or 0),
                    "reason":      f"B {why}",
                    "rebalance":   True,
                    "order_key":   _mk_key(exec_date, "B", code, f"sell_rebal"),
                })

        # 월말 교체: buys (상위 10 중 미보유 + 금액·수량)
        bdf = B.get("buys")
        if bdf is not None and len(bdf):
            for _, b in bdf.iterrows():
                code = str(b.get("code", "")).zfill(6)
                skip = b.get("skip", "")
                buys.append({
                    "rule":        "B",
                    "code":        code,
                    "name":        b.get("name"),
                    "amount":      int(b.get("amount") or 0),
                    "qty":         int(b.get("qty") or 0),
                    "close":       float(b.get("close") or 0),
                    "ma62":        float(b.get("ma62") or 0),
                    "r120":        float(b.get("r120") or 0),
                    "amt20억":     float(b.get("amt20억") or 0),
                    "skip_note":   str(skip) if skip else "",
                    "rebalance":   True,
                    "order_key":   _mk_key(exec_date, "B", code, "buy_rebal"),
                })

    return {"sell": sells, "buy": buys}


def _rank_summary(B: dict, top_n: int = 10) -> list[dict]:
    """B rank DataFrame 상위 N → 보고용 요약."""
    r = B.get("rank")
    if r is None or len(r) == 0:
        return []
    out = []
    for _, row in r.head(top_n).iterrows():
        out.append({
            "code":  str(row.get("code", "")).zfill(6),
            "name":  row.get("name"),
            "r120":  float(row.get("r120") or 0),
            "close": float(row.get("close") or 0),
            "amt":   float(row.get("amt20억") or 0),
        })
    return out


def run(replay_date: str | None = None) -> dict:
    common.setup()
    today = replay_date or common.today_str()

    today_dt = datetime.strptime(today, "%Y-%m-%d").date()
    exec_date_dt = common.next_trading_date(today_dt)
    exec_date = exec_date_dt.strftime("%Y-%m-%d")

    # 월말 전날 여부 체크 (= 내일이 월말 마지막 거래일)
    is_rebalance = common.is_last_trading_day_of_month(exec_date_dt)

    a_cap, b_cap = _get_module_capital()
    logger.info(f"[judge] 판정일 {today}  집행일 {exec_date}  B 리밸런스={is_rebalance}")
    logger.info(f"[judge] 모듈 자본  A={a_cap:,}  B={b_cap:,}")

    daily_v3, stock_lib, mom_lib = _import_engine()

    # 모듈 A
    logger.info(f"[judge/A] daily_v3.judge(dep={a_cap:,})")
    A = daily_v3.judge(dep=a_cap)
    logger.info(
        f"[judge/A] {A['date']} close={A['close']:.2f} ({A['chg']*100:+.2f}%)  "
        f"positions={len(A.get('positions', []))} signals={len(A.get('signals', []))} "
        f"free_slots={A.get('free_slots')}"
    )

    # 모듈 B
    logger.info(f"[judge/B] mom_lib.judge(capital={b_cap:,}, rebalance={is_rebalance})")
    B = mom_lib.judge(capital=b_cap, rebalance=is_rebalance)
    sells_daily = len(B.get("sells_daily", [])) if B.get("sells_daily") is not None else 0
    sells_rebal = len(B.get("sells_rebalance", [])) if B.get("sells_rebalance") is not None else 0
    buys_n = len(B.get("buys", [])) if B.get("buys") is not None else 0
    logger.info(
        f"[judge/B] {B['date']} regime_on={B['regime_on']}  "
        f"positions={len(B.get('positions', []))} "
        f"sells_daily={sells_daily} sells_rebal={sells_rebal} buys={buys_n}  mode={B.get('mode')}"
    )

    A_orders = _build_A_orders(A, exec_date)
    B_orders = _build_B_orders(B, exec_date, is_rebalance)

    orders = {
        "generated_at":  common.now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "judge_date":    today,
        "exec_date":     exec_date,
        "is_rebalance":  is_rebalance,
        "capital_mode":  "fixed" if not cfg.get("modules.A.compound") else "compound",
        "modules": {
            "A": {"capital": a_cap, "max_positions": cfg.get("modules.A.max_positions")},
            "B": {"capital": b_cap, "top": cfg.get("modules.B.top")},
        },
        "regime_on":     bool(B.get("regime_on", False)),
        "regime_detail": {
            "ma62":       float(B.get("ma62", 0)),
            "ma62_20ago": float(B.get("ma62_20ago", 0)),
        },
        "k200": {
            "date":  str(A.get("date")),
            "close": float(A.get("close", 0)),
            "chg":   float(A.get("chg", 0)),
            "ma62":  float(A.get("ma62", 0)),
            "ma248": float(A.get("ma248", 0)),
            "rsi":   float(A.get("rsi", 0)),
            "atr":   float(A.get("atr", 0)),
        },
        "sell": {
            "A": A_orders["sell"],
            "B": B_orders["sell"],
        },
        "buy": {
            "A": A_orders["buy"],
            "B": B_orders["buy"],
        },
        "A_near":        A.get("near", []),
        "B_rank_top":    _rank_summary(B, 10),
    }

    p = S.save_orders(exec_date, orders)
    logger.info(
        f"[judge] orders 저장: {p}  "
        f"매도 A:{len(A_orders['sell'])} B:{len(B_orders['sell'])}  "
        f"매수 A:{len(A_orders['buy'])} B:{len(B_orders['buy'])}"
    )
    return orders


def main() -> int:
    p = argparse.ArgumentParser(description="autotrade/judge v2")
    p.add_argument("--date", help="리플레이 기준일 YYYY-MM-DD")
    args = p.parse_args()
    try:
        run(replay_date=args.date)
        return 0
    except Exception as e:
        logger.exception(f"[judge] 실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
