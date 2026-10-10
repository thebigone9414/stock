"""autotrade/buy.py — 15:21 매수 집행 (v2, 장 마감 동시호가)

A: ETF 11패턴 신호 → KODEX 레버리지/인버스 매수. 체결 후 positions_v3.csv 에 추가.
B: 월말 교체 매수 (상위 10 중 미보유, judge 가 amount/qty 계산). 체결 후 positions_B.csv 에 추가.

B 월말 교체는 "같은 15:21 매도+매수" 설계지만, sell.py 가 08:50 매도이므로
실제로는 "월말 당일 08:50 매도 → 15:21 매수" 흐름. 이게 더 안전 (매도 대금 결제 걱정 없음).

STOP_A / STOP_B 파일 체크로 모듈별 매수 차단.
"""
from __future__ import annotations

import sys
from typing import Optional

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg, state as S, broker as B, report as R
from config.settings import get_settings
from kis.factory import KIS


def _append_A_position(pat: int, entry_date: str, entry_px: float, qty: int, stop: float,
                       etf_price: int, etf_buy_date: str) -> None:
    df = S.load_A_positions()
    new = pd.DataFrame([{
        "패턴":       pat,
        "진입일":     entry_date,
        "진입가":     entry_px,
        "계약수":     qty,
        "손절":       stop,
        "etf매수가":  etf_price,
        "etf매수일":  etf_buy_date,
    }])
    df = pd.concat([df, new], ignore_index=True)
    S.save_A_positions(df)
    logger.info(f"[buy/A] 포지션 추가: pat#{pat} entry={entry_date} px={entry_px:.2f} qty={qty} stop={stop:.2f} etf_px={etf_price:,}")


def _append_B_position(code: str, name: str, buy_date: str, buy_price: int, qty: int) -> None:
    df = S.load_B_positions()
    new = pd.DataFrame([{
        "code":    str(code).zfill(6),
        "name":    name,
        "매수일":  buy_date,
        "매수가":  buy_price,
        "수량":    qty,
    }])
    df = pd.concat([df, new], ignore_index=True)
    S.save_B_positions(df)
    logger.info(f"[buy/B] 포지션 추가: {code} {name} {qty}주 @ {buy_price:,}")


def _execute_A_buys(market, order_mgr, buys: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    for b in buys:
        code, name = b["code"], b["name"]
        amount = int(b["amount"])
        key = b["order_key"]
        try:
            q = market.get_quote(code)
            if q.price <= 0:
                msg = R.format_order_failed("buy", "A", code, name, 0, "현재가 0")
                msgs.append(msg)
                if notifier: notifier.notify(msg)
                continue
            qty = max(1, int(amount // q.price))
            res = B.place_buy_market(order_mgr, code, qty, key, date_str=today)
            if res.ok:
                if not res.skipped and not res.dry_run:
                    _append_A_position(
                        pat=b.get("pat"),
                        entry_date=b.get("signal_date"),
                        entry_px=b.get("entry_px", 0),
                        qty=qty,
                        stop=b.get("stop_level", 0),
                        etf_price=q.price,
                        etf_buy_date=today,
                    )
                S.append_trade_log("A", {
                    "date": today, "side": "buy", "code": code, "name": name,
                    "qty": qty, "price": q.price, "amount": qty * q.price,
                    "pat": b.get("pat"), "pattern_name": b.get("pattern_name"),
                    "signal_date": b.get("signal_date"), "entry_px": b.get("entry_px"),
                    "stop_level": b.get("stop_level"),
                    "order_no": res.order_no, "dry_run": res.dry_run,
                })
                msg = R.format_order_filled("buy", "A", code, name, qty, q.price,
                                             reason=f"pat#{b.get('pat')} {b.get('pattern_name', '')}")
                msgs.append(msg)
                if notifier: notifier.notify(msg)
            else:
                msg = R.format_order_failed("buy", "A", code, name, qty, res.message)
                msgs.append(msg)
                if notifier: notifier.notify(msg)
        except Exception as e:
            logger.exception(f"[buy/A] {code} 예외: {e}")
            msg = R.format_order_failed("buy", "A", code, name, 0, str(e))
            msgs.append(msg)
            if notifier: notifier.notify(msg)
    return msgs


def _execute_B_buys(market, order_mgr, buys: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    cap_ratio = float(cfg.get("modules.B.stock_price_cap_ratio", 1.5))
    for b in buys:
        code, name = b["code"], b["name"]
        amount = int(b.get("amount", 0))
        qty_planned = int(b.get("qty", 0))
        key = b["order_key"]
        try:
            q = market.get_quote(code)
            if q.price <= 0:
                msg = R.format_order_failed("buy", "B", code, name, qty_planned, "현재가 0")
                msgs.append(msg)
                if notifier: notifier.notify(msg)
                continue
            if q.price > amount * cap_ratio:
                msg = R.format_order_failed(
                    "buy", "B", code, name, 0,
                    f"1주 {q.price:,} > 종목당 금액 {amount:,} × {cap_ratio} — 고가 cap 초과",
                )
                msgs.append(msg)
                if notifier: notifier.notify(msg)
                continue
            qty = max(1, int(amount // q.price))
            res = B.place_buy_market(order_mgr, code, qty, key, date_str=today)
            if res.ok:
                if not res.skipped and not res.dry_run:
                    _append_B_position(code, name, today, q.price, qty)
                S.append_trade_log("B", {
                    "date": today, "side": "buy", "code": code, "name": name,
                    "qty": qty, "price": q.price, "amount": qty * q.price,
                    "r120": b.get("r120"), "rebalance": b.get("rebalance", False),
                    "order_no": res.order_no, "dry_run": res.dry_run,
                })
                msg = R.format_order_filled("buy", "B", code, name, qty, q.price,
                                             reason="월말 교체" if b.get("rebalance") else "")
                msgs.append(msg)
                if notifier: notifier.notify(msg)
            else:
                msg = R.format_order_failed("buy", "B", code, name, qty, res.message)
                msgs.append(msg)
                if notifier: notifier.notify(msg)
        except Exception as e:
            logger.exception(f"[buy/B] {code} 예외: {e}")
            msg = R.format_order_failed("buy", "B", code, name, qty_planned, str(e))
            msgs.append(msg)
            if notifier: notifier.notify(msg)
    return msgs


def run() -> int:
    notifier = common.setup()
    today = common.today_str()

    if not common.is_trading_today():
        logger.info(f"[buy] {today} 휴장일 — 종료")
        return 0

    orders = S.load_orders(today)
    if not orders:
        logger.warning(f"[buy] {today} orders 없음 — 종료")
        return 0

    a_buys = orders.get("buy", {}).get("A", [])
    b_buys = orders.get("buy", {}).get("B", [])

    # 모듈별 STOP 파일 체크
    if common.is_stopped("A") and a_buys:
        logger.warning(f"[buy] STOP_A — A 매수 {len(a_buys)}건 skip")
        if notifier: notifier.notify(f"[buy] STOP_A 활성 — A 매수 {len(a_buys)}건 차단")
        a_buys = []
    if common.is_stopped("B") and b_buys:
        logger.warning(f"[buy] STOP_B — B 매수 {len(b_buys)}건 skip")
        if notifier: notifier.notify(f"[buy] STOP_B 활성 — B 매수 {len(b_buys)}건 차단")
        b_buys = []

    logger.info(f"[buy] 매수 계획: A {len(a_buys)}건  B {len(b_buys)}건")
    if not a_buys and not b_buys:
        logger.info("[buy] 매수 없음 — 종료")
        return 0

    max_per_day = int(cfg.get("order.max_buy_per_day", 15))
    total = len(a_buys) + len(b_buys)
    if total > max_per_day:
        logger.error(f"[buy] 매수 계획 {total}건 > 한도 {max_per_day} — 중지")
        if notifier: notifier.notify(f"[buy] {total}건 한도 초과 — 중지")
        return 1

    settings = get_settings()
    kis = KIS(settings)
    a_msgs = _execute_A_buys(kis.market, kis.order, a_buys, notifier)
    b_msgs = _execute_B_buys(kis.market, kis.order, b_buys, notifier)
    logger.info(f"[buy] 완료 — A {len(a_msgs)} / B {len(b_msgs)}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
