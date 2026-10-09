"""autotrade/sell.py — 08:50 매도 집행 (장전 동시호가)

전날 20:10 judge가 저장한 orders JSON의 매도 리스트를 전부 시장가 매도.
→ 09:00 시가 체결.

- 체결 성공: positions 파일에서 해당 행 제거, trade_log 기록, 텔레그램 알림
- 체결 실패/미체결: "매도 대기" 상태로 다음 거래일 재시도 (상태 파일에 기록)

ETF (positions_v3.csv): 패턴·진입일 매칭으로 행 제거
종목 (positions_stock.csv): code 매칭

실행: python -m autotrade.sell
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg, state as S, broker as B, report as R
from config.settings import get_settings
from kis.factory import KIS


def _prev_trading_date(from_date: str) -> str:
    from data.holidays import is_trading_day
    d = datetime.strptime(from_date, "%Y-%m-%d").date() - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _remove_etf_position(pat: int, entry_date: str) -> bool:
    """engine_v3/positions_v3.csv 에서 (패턴, 진입일) 행 제거."""
    df = S.load_etf_positions()
    if df.empty:
        return False
    mask = (df["패턴"].astype(str) == str(pat)) & (df["진입일"].astype(str) == str(entry_date))
    before = len(df)
    df = df[~mask]
    if len(df) < before:
        S.save_etf_positions(df)
        logger.info(f"[sell/etf] 포지션 제거: pat#{pat} entry={entry_date}")
        return True
    return False


def _remove_stock_position(code: str) -> bool:
    """engine_v3/positions_stock.csv 에서 code 행 제거."""
    df = S.load_stock_positions()
    if df.empty:
        return False
    code = str(code).zfill(6)
    mask = df["code"] == code
    before = len(df)
    df = df[~mask]
    if len(df) < before:
        S.save_stock_positions(df)
        logger.info(f"[sell/stock] 포지션 제거: {code}")
        return True
    return False


def _execute_etf_sells(order_mgr, sells: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    for s in sells:
        code, name = s["code"], s["name"]
        qty, key = s["quantity"], s["order_key"]
        if qty <= 0:
            logger.warning(f"[sell/etf] {code} 수량 0 — skip")
            continue
        res = B.place_sell_market(order_mgr, code, qty, key, date_str=today)
        if res.ok:
            if not res.skipped and not res.dry_run:
                _remove_etf_position(s.get("pat"), s.get("entry_date"))
            S.append_trade_log("etf", {
                "date": today, "side": "sell", "code": code, "name": name,
                "qty": qty, "reason": s["reason"],
                "pat": s.get("pat"), "entry_date": s.get("entry_date"),
                "entry_px": s.get("entry_px"), "stop_level": s.get("stop_level"),
                "order_no": res.order_no, "dry_run": res.dry_run,
            })
            msg = R.format_order_filled("sell", "etf", code, name, qty, 0, reason=s["reason"])
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
        else:
            msg = R.format_order_failed("sell", "etf", code, name, qty, res.message)
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
    return msgs


def _execute_stock_sells(order_mgr, sells: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    stk_pos_df = S.load_stock_positions()
    for s in sells:
        code, name = s["code"], s["name"]
        key = s["order_key"]
        # 수량: judge가 저장한 값 없으면 positions에서 조회
        qty = s.get("quantity")
        if not qty:
            row = stk_pos_df[stk_pos_df["code"] == code]
            qty = int(row.iloc[0]["수량"]) if len(row) else 0
        if qty <= 0:
            logger.warning(f"[sell/stock] {code} 수량 0 — skip")
            continue
        res = B.place_sell_market(order_mgr, code, qty, key, date_str=today)
        if res.ok:
            if not res.skipped and not res.dry_run:
                _remove_stock_position(code)
            S.append_trade_log("stock", {
                "date": today, "side": "sell", "code": code, "name": name,
                "qty": qty, "reason": s["reason"],
                "entry_date": s.get("entry_date"), "entry_px": s.get("entry_px"),
                "stop_level": s.get("stop_level"), "pnl_pct": s.get("pnl_pct"),
                "order_no": res.order_no, "dry_run": res.dry_run,
            })
            msg = R.format_order_filled("sell", "stock", code, name, qty, 0, reason=s["reason"])
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
        else:
            msg = R.format_order_failed("sell", "stock", code, name, qty, res.message)
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
    return msgs


def run() -> int:
    notifier = common.setup()
    today = common.today_str()

    if not common.is_trading_today():
        logger.info(f"[sell] {today} 휴장일 — 종료")
        return 0

    # 오늘 집행 예정 orders 로드 (judge가 전 거래일에 집행일=오늘로 저장)
    orders = S.load_orders(today)
    if not orders:
        logger.warning(f"[sell] {today} orders 없음 — 종료")
        return 0

    etf_sells = orders.get("sell", {}).get("etf", [])
    stk_sells = orders.get("sell", {}).get("stock", [])
    logger.info(f"[sell] 매도 계획: ETF {len(etf_sells)}건  종목 {len(stk_sells)}건")

    if not etf_sells and not stk_sells:
        logger.info("[sell] 매도 없음 — 종료")
        return 0

    settings = get_settings()
    kis = KIS(settings)
    order_mgr = kis.order

    etf_msgs = _execute_etf_sells(order_mgr, etf_sells, notifier)
    stk_msgs = _execute_stock_sells(order_mgr, stk_sells, notifier)
    logger.info(f"[sell] 완료 — ETF {len(etf_msgs)}건 / 종목 {len(stk_msgs)}건 처리")
    return 0


if __name__ == "__main__":
    sys.exit(run())
