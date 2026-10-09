"""autotrade/stoploss.py — 종목 손절 (STOP 등록 + 체크포인트 + 체결 확인)

모드 (config.yaml stoploss.mode):
  stop_market       — KIS STOP 시장가 TR (현재 미지원, placeholder)
  stop_limit_hybrid — 08:55 "손절가 - margin 지정가 매도" 걸고 체크포인트에서 보강
  checkpoint        — STOP 안 걸고 체크포인트에서 현재가 비교 (zero-config fallback)

ETF 손절은 종가 판정 → 다음날 08:50 매도. 이 모듈은 종목에만 적용.

세 가지 서브커맨드:
  stop-register — 08:55 보유 종목 전부에 STOP(또는 지정가) 걸기. 매도 대상 제외.
  stop-check    — 09:05~15:15 체크포인트. 현재가 ≤ 손절가면:
                   - hybrid: 지정가 미체결이면 취소 + 시장가 보강
                   - checkpoint: 시장가 즉시 매도
  stop-confirm  — 15:35 당일 지정가 체결 확인, 미체결은 자동 소멸

실행:
  python -m autotrade.stoploss --cmd register
  python -m autotrade.stoploss --cmd check
  python -m autotrade.stoploss --cmd confirm
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from loguru import logger

from autotrade import common, config as cfg, state as S, broker as B, report as R
from config.settings import get_settings
from kis.factory import KIS


def _hybrid_price(stop_level: int, margin_pct: float) -> int:
    """손절가 - margin 지정가 (하락 터치 시 체결 확률 높임)."""
    return int(stop_level * (1 - margin_pct))


# ── stop-register (08:55) ─────────────────────────────────────
def register() -> int:
    notifier = common.setup()
    today = common.today_str()

    if not common.is_trading_today():
        logger.info(f"[stop-reg] {today} 휴장일 — 종료")
        return 0

    mode = cfg.get("stoploss.mode")
    if mode == "checkpoint":
        logger.info("[stop-reg] mode=checkpoint — STOP 등록 안 함")
        return 0
    if mode == "stop_market":
        logger.warning("[stop-reg] mode=stop_market — KIS STOP TR 미지원 (placeholder)")
        return 0
    # mode == stop_limit_hybrid 아래로

    pos_df = S.load_stock_positions()
    if pos_df.empty:
        logger.info("[stop-reg] 보유 종목 없음 — 종료")
        return 0

    orders = S.load_orders(today) or {}
    sell_codes = {s["code"] for s in orders.get("sell", {}).get("stock", [])}

    settings = get_settings()
    kis = KIS(settings)
    order_mgr = kis.order
    margin = float(cfg.get("stoploss.stop_limit_margin_pct", 0.03))

    registered = 0
    for _, r in pos_df.iterrows():
        code = str(r["code"]).zfill(6)
        if code in sell_codes:
            logger.info(f"[stop-reg] {code} 매도 대상 — STOP 등록 skip")
            continue
        qty = int(r.get("수량") or 0)
        stop_level = int(r.get("손절가") or 0)
        if qty <= 0 or stop_level <= 0:
            logger.warning(f"[stop-reg] {code} 데이터 이상 (qty={qty} stop={stop_level}) — skip")
            continue
        price = _hybrid_price(stop_level, margin)
        key = f"{today.replace('-', '')}:{code}:stop_limit_hybrid"
        res = B.place_sell_limit(order_mgr, code, qty, price, key, date_str=today)
        if res.ok:
            registered += 1
            logger.info(f"[stop-reg] {code} 손절{stop_level:,} → 지정가 {price:,} ({qty}주)")
        else:
            logger.error(f"[stop-reg] {code} 등록 실패: {res.message}")

    logger.info(f"[stop-reg] 완료 {registered} / {len(pos_df)}건 등록")
    if notifier and registered:
        notifier.notify(f"[STOP 등록] {today}  하이브리드 지정가 매도 {registered}건 (손절가-{margin*100:.0f}%)")
    return 0


# ── stop-check (체크포인트) ────────────────────────────────────
def check() -> int:
    notifier = common.setup()
    today = common.today_str()
    now_hhmm = common.now_kst().strftime("%H:%M")

    if not common.is_trading_today():
        logger.info(f"[stop-chk] {today} 휴장일 — 종료")
        return 0

    pos_df = S.load_stock_positions()
    if pos_df.empty:
        logger.info("[stop-chk] 보유 종목 없음 — 종료")
        return 0

    mode = cfg.get("stoploss.mode")
    settings = get_settings()
    kis = KIS(settings)
    market = kis.market
    order_mgr = kis.order

    triggered = 0
    for _, r in pos_df.iterrows():
        code = str(r["code"]).zfill(6)
        name = r.get("name") or r.get("패턴") or code
        qty = int(r.get("수량") or 0)
        stop_level = int(r.get("손절가") or 0)
        if qty <= 0 or stop_level <= 0:
            continue
        try:
            q = market.get_quote(code)
            current = q.price
        except Exception as e:
            logger.warning(f"[stop-chk] {code} 현재가 조회 실패: {e}")
            continue
        if current <= 0 or current > stop_level:
            continue

        # 손절 발동
        key = f"{today.replace('-', '')}:{code}:stop_trigger_{now_hhmm.replace(':', '')}"

        if mode == "stop_limit_hybrid":
            # 지정가 미체결 확인 — 있으면 취소
            pendings = B.list_pending(order_mgr)
            for p in pendings:
                if (p.get("pdno") or p.get("code")) == code and (p.get("sll_buy_dvsn_cd") == "01" or p.get("side") == "SELL"):
                    order_no = p.get("odno") or p.get("order_no", "")
                    o_qty = int(p.get("ord_qty") or p.get("quantity") or qty)
                    o_price = int(p.get("ord_unpr") or p.get("price") or 0)
                    if order_no:
                        B.cancel_order(order_mgr, order_no, code, o_qty, o_price)
                        logger.info(f"[stop-chk] {code} 지정가 취소: order_no={order_no}")

        res = B.place_sell_market(order_mgr, code, qty, key, date_str=today)
        if res.ok and not res.skipped:
            triggered += 1
            msg = R.format_stop_hit(code, name, current, stop_level, qty, mode)
            logger.warning(msg)
            if notifier:
                notifier.notify(msg)
            S.append_trade_log("stock", {
                "date": today, "side": "sell", "code": code, "name": name,
                "qty": qty, "reason": f"손절 (장중 {now_hhmm}, {mode})",
                "current": current, "stop_level": stop_level,
                "order_no": res.order_no, "dry_run": res.dry_run,
            })
            # positions_stock.csv 에서 즉시 제거
            if not res.dry_run:
                remaining = pos_df[pos_df["code"] != code]
                S.save_stock_positions(remaining)
                pos_df = remaining

    logger.info(f"[stop-chk] 완료 {triggered}건 손절 발동 (checkpoint={now_hhmm})")
    return 0


# ── stop-confirm (15:35) ─────────────────────────────────────
def confirm() -> int:
    notifier = common.setup()
    today = common.today_str()

    if not common.is_trading_today():
        logger.info(f"[stop-cfm] {today} 휴장일 — 종료")
        return 0

    mode = cfg.get("stoploss.mode")
    if mode == "checkpoint":
        logger.info("[stop-cfm] mode=checkpoint — 확인 필요 없음")
        return 0

    settings = get_settings()
    kis = KIS(settings)
    order_mgr = kis.order

    pendings = B.list_pending(order_mgr)
    unfilled_sells = [p for p in pendings if (p.get("sll_buy_dvsn_cd") == "01" or p.get("side") == "SELL")]

    # 미체결 지정가가 남아 있으면 취소 (당일 유효지만 안전하게 명시적 취소)
    cancelled = 0
    for p in unfilled_sells:
        code = p.get("pdno") or p.get("code")
        order_no = p.get("odno") or p.get("order_no", "")
        o_qty = int(p.get("ord_qty") or p.get("quantity") or 0)
        o_price = int(p.get("ord_unpr") or p.get("price") or 0)
        if order_no and code:
            if B.cancel_order(order_mgr, order_no, code, o_qty, o_price):
                cancelled += 1

    logger.info(f"[stop-cfm] 완료 — 미체결 지정가 {cancelled}건 취소 (당일 소멸 안전장치)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="autotrade/stoploss")
    p.add_argument("--cmd", choices=["register", "check", "confirm"], required=True)
    args = p.parse_args()
    try:
        if args.cmd == "register":
            return register()
        elif args.cmd == "check":
            return check()
        elif args.cmd == "confirm":
            return confirm()
    except Exception as e:
        logger.exception(f"[stoploss/{args.cmd}] 실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
