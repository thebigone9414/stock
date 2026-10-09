"""autotrade/buy.py — 15:18 매수 집행 (장마감 동시호가)

전날 20:10 judge가 저장한 orders JSON의 매수 리스트를 전부 시장가 매수.
→ 15:30 종가 체결.

ETF: 금액(amount) 기반. 당일 현재가 조회 → qty = floor(amount / current_price)
종목: judge가 이미 qty 계산해둠. 당일 상장폐지·거래정지·고가 종목 cap 재확인.

- 체결 성공: positions 파일에 행 추가 (ETF: positions_v3.csv / 종목: positions_stock.csv)
- 체결 실패: trade_log에 "failed" 기록, 다음날 재시도 안 함 (매수는 1회만, 지시서 §6)

STOP 파일 존재 시 매수 완전 중지. 매도·손절만 수행.

실행: python -m autotrade.buy
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg, state as S, broker as B, report as R
from config.settings import get_settings
from kis.factory import KIS


def _append_etf_position(pat: int, entry_date: str, entry_px: float, qty: int, stop: float, etf_price: int) -> None:
    """engine_v3/positions_v3.csv 에 1행 추가.
    (패턴, 진입일, 진입가=지수 종가, 계약수=ETF 주수, 손절=지수 손절선, etf_매수가=ETF 체결가)
    """
    df = S.load_etf_positions()
    new = pd.DataFrame([{
        "패턴":      pat,
        "진입일":    entry_date,
        "진입가":    entry_px,
        "계약수":    qty,
        "손절":      stop,
        "etf_매수가": etf_price,
    }])
    df = pd.concat([df, new], ignore_index=True)
    S.save_etf_positions(df)
    logger.info(f"[buy/etf] 포지션 추가: pat#{pat} entry={entry_date} px={entry_px:.2f} qty={qty} stop={stop:.2f} etf_px={etf_price:,}")


def _append_stock_position(code: str, name: str, pattern: str, buy_date: str,
                            buy_price: int, stop_price: int, qty: int) -> None:
    df = S.load_stock_positions()
    new = pd.DataFrame([{
        "code":    str(code).zfill(6),
        "패턴":    pattern or "",
        "매수일":  buy_date,
        "매수가":  buy_price,
        "손절가":  stop_price,
        "수량":    qty,
    }])
    df = pd.concat([df, new], ignore_index=True)
    S.save_stock_positions(df)
    logger.info(f"[buy/stock] 포지션 추가: {code} {name} {qty}주 @ {buy_price:,} 손절 {stop_price:,}")


def _calc_etf_qty(amount: int, current_price: int) -> int:
    """금액 / 현재가 → 내림. 최소 1주."""
    if current_price <= 0:
        return 0
    return max(1, int(amount // current_price))


def _execute_etf_buys(market, order_mgr, buys: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    for b in buys:
        code, name = b["code"], b["name"]
        amount = int(b["amount"])
        key = b["order_key"]
        try:
            q = market.get_quote(code)
            if q.price <= 0:
                msg = R.format_order_failed("buy", "etf", code, name, 0, "현재가 0 — 거래정지 가능성")
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
                continue
            qty = _calc_etf_qty(amount, q.price)
            # 현금 부족 체크는 CAPITAL 고정 모드라 생략 — 사용자가 입금 책임
            res = B.place_buy_market(order_mgr, code, qty, key, date_str=today)
            if res.ok:
                if not res.skipped and not res.dry_run:
                    _append_etf_position(
                        pat=b.get("pat"),
                        entry_date=b.get("signal_date"),
                        entry_px=b.get("entry_px", 0),
                        qty=qty,
                        stop=b.get("stop_level", 0),
                        etf_price=q.price,
                    )
                S.append_trade_log("etf", {
                    "date": today, "side": "buy", "code": code, "name": name,
                    "qty": qty, "price": q.price, "amount": qty * q.price,
                    "pat": b.get("pat"), "pattern_name": b.get("pattern_name"),
                    "signal_date": b.get("signal_date"), "entry_px": b.get("entry_px"),
                    "stop_level": b.get("stop_level"),
                    "order_no": res.order_no, "dry_run": res.dry_run,
                })
                msg = R.format_order_filled("buy", "etf", code, name, qty, q.price,
                                             reason=f"pat#{b.get('pat')} {b.get('pattern_name', '')}")
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
            else:
                msg = R.format_order_failed("buy", "etf", code, name, qty, res.message)
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
        except Exception as e:
            logger.exception(f"[buy/etf] {code} 예외: {e}")
            msg = R.format_order_failed("buy", "etf", code, name, 0, str(e))
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
    return msgs


def _execute_stock_buys(market, order_mgr, buys: list[dict], notifier) -> list[str]:
    msgs: list[str] = []
    today = common.today_str()
    cap_ratio = float(cfg.get("capital.stock_price_cap_ratio", 1.5))
    for b in buys:
        code, name = b["code"], b["name"]
        amount = int(b.get("amount", 0))
        qty_planned = int(b.get("qty", 0))
        key = b["order_key"]
        try:
            q = market.get_quote(code)
            if q.price <= 0:
                msg = R.format_order_failed("buy", "stock", code, name, qty_planned, "현재가 0 — 거래정지 가능성")
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
                continue
            # 고가 종목 cap 재확인 (judge 시점 이후 급등 등)
            if q.price > amount * cap_ratio:
                msg = R.format_order_failed(
                    "buy", "stock", code, name, 0,
                    f"1주가 {q.price:,} > 건당 금액 {amount:,} × {cap_ratio} — 고가 종목 cap 초과",
                )
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
                continue
            qty = max(1, int(amount // q.price))
            res = B.place_buy_market(order_mgr, code, qty, key, date_str=today)
            if res.ok:
                if not res.skipped and not res.dry_run:
                    _append_stock_position(
                        code=code, name=name, pattern=b.get("pattern") or b.get("card", ""),
                        buy_date=today, buy_price=q.price,
                        stop_price=int(b.get("stop_level", 0)), qty=qty,
                    )
                S.append_trade_log("stock", {
                    "date": today, "side": "buy", "code": code, "name": name,
                    "qty": qty, "price": q.price, "amount": qty * q.price,
                    "card": b.get("card"), "pattern": b.get("pattern"),
                    "stop_level": b.get("stop_level"), "r120": b.get("r120"),
                    "order_no": res.order_no, "dry_run": res.dry_run,
                })
                msg = R.format_order_filled("buy", "stock", code, name, qty, q.price,
                                             reason=f"카드 {b.get('card', '')}")
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
            else:
                msg = R.format_order_failed("buy", "stock", code, name, qty, res.message)
                msgs.append(msg)
                if notifier:
                    notifier.notify(msg)
        except Exception as e:
            logger.exception(f"[buy/stock] {code} 예외: {e}")
            msg = R.format_order_failed("buy", "stock", code, name, qty_planned, str(e))
            msgs.append(msg)
            if notifier:
                notifier.notify(msg)
    return msgs


def run() -> int:
    notifier = common.setup()
    today = common.today_str()

    if not common.is_trading_today():
        logger.info(f"[buy] {today} 휴장일 — 종료")
        return 0

    if common.is_stopped():
        logger.warning(f"[buy] STOP 파일 존재 — 매수 중지")
        if notifier:
            notifier.notify("[buy] STOP 파일로 매수 전면 중지됨")
        return 0

    orders = S.load_orders(today)
    if not orders:
        logger.warning(f"[buy] {today} orders 없음 — 종료")
        return 0

    etf_buys = orders.get("buy", {}).get("etf", [])
    stk_buys = orders.get("buy", {}).get("stock", [])
    logger.info(f"[buy] 매수 계획: ETF {len(etf_buys)}건  종목 {len(stk_buys)}건")

    if not etf_buys and not stk_buys:
        logger.info("[buy] 매수 없음 — 종료")
        return 0

    # 하루 한도 체크
    max_per_day = int(cfg.get("order.max_buy_per_day", 15))
    total = len(etf_buys) + len(stk_buys)
    if total > max_per_day:
        logger.error(f"[buy] 매수 계획 {total}건 > 한도 {max_per_day} — 중지")
        if notifier:
            notifier.notify(f"[buy] 매수 {total}건 한도 {max_per_day} 초과 — 자동매매 중지")
        return 1

    settings = get_settings()
    kis = KIS(settings)
    etf_msgs = _execute_etf_buys(kis.market, kis.order, etf_buys, notifier)
    stk_msgs = _execute_stock_buys(kis.market, kis.order, stk_buys, notifier)
    logger.info(f"[buy] 완료 — ETF {len(etf_msgs)} / 종목 {len(stk_msgs)}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
