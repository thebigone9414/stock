"""autotrade/sell.py — 08:50 매도 집행 (v2)

orders_YYYYMMDD.json 의 sell.A / sell.B 전부 시장가 매도 → 09:00 시가 체결.
A: 패턴 청산 또는 종가 손절. 체결 후 positions_v3.csv 에서 (패턴,진입일) 제거.
B: 62일선 이탈 또는 월말 교체. 체결 후 positions_B.csv 에서 code 제거.
"""
from __future__ import annotations

import sys
from typing import Optional

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg, state as S, broker as B, report as R
from config.settings import get_settings
from kis.factory import KIS


def _remove_A_position(pat: int, entry_date: str) -> bool:
    df = S.load_A_positions()
    if df.empty:
        return False
    mask = (df["패턴"].astype(str) == str(pat)) & (df["진입일"].astype(str) == str(entry_date))
    before = len(df)
    df = df[~mask]
    if len(df) < before:
        S.save_A_positions(df)
        logger.info(f"[sell/A] 포지션 제거: pat#{pat} entry={entry_date}")
        return True
    return False


def _remove_B_position(code: str) -> bool:
    df = S.load_B_positions()
    if df.empty:
        return False
    code = str(code).zfill(6)
    mask = df["code"] == code
    before = len(df)
    df = df[~mask]
    if len(df) < before:
        S.save_B_positions(df)
        logger.info(f"[sell/B] 포지션 제거: {code}")
        return True
    return False


def _execute_sells(order_mgr, module: str, sells: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    for s in sells:
        code, name = s["code"], s["name"]
        qty = int(s.get("quantity") or 0)
        key = s["order_key"]
        if qty <= 0:
            logger.warning(f"[sell/{module}] {code} 수량 0 — skip")
            continue
        res = B.place_sell_market(order_mgr, code, qty, key, date_str=today)
        if res.ok:
            if not res.skipped and not res.dry_run:
                if module == "A":
                    _remove_A_position(s.get("pat"), s.get("entry_date"))
                else:
                    _remove_B_position(code)
            row = {
                "date": today, "side": "sell", "code": code, "name": name,
                "qty": qty, "reason": s["reason"],
                "entry_date": s.get("entry_date"), "entry_px": s.get("entry_px"),
                "pat": s.get("pat"), "pnl_pct": s.get("pnl_pct"),
                "order_no": res.order_no, "dry_run": res.dry_run,
            }
            S.append_trade_log(module, row)
            msg = R.format_order_filled("sell", module, code, name, qty, 0, reason=s["reason"])
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
        else:
            msg = R.format_order_failed("sell", module, code, name, qty, res.message)
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

    orders = S.load_orders(today)
    if not orders:
        logger.warning(f"[sell] {today} orders 없음 — 종료")
        return 0

    a_sells = orders.get("sell", {}).get("A", [])
    b_sells = orders.get("sell", {}).get("B", [])
    logger.info(f"[sell] 매도 계획: A {len(a_sells)}건  B {len(b_sells)}건")

    if not a_sells and not b_sells:
        logger.info("[sell] 매도 없음 — 종료")
        return 0

    settings = get_settings()
    kis = KIS(settings)
    order_mgr = kis.order

    a_msgs = _execute_sells(order_mgr, "A", a_sells, notifier)
    b_msgs = _execute_sells(order_mgr, "B", b_sells, notifier)
    logger.info(f"[sell] 완료 — A {len(a_msgs)} / B {len(b_msgs)}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
