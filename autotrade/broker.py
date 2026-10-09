"""autotrade/broker.py — KIS 주문 어댑터 (멱등성·재시도·dry-run 통합)

모든 매수/매도/STOP 주문은 이 모듈을 거친다.
멱등성 보장: order_key 기반 state 파일 체크 → 이미 submitted/filled이면 재주문 안 함.

주문 유형:
  buy_market        — 15:18 매수 (장마감 동시호가에 참여)
  sell_market       — 08:50 매도 (장전 동시호가에 참여) + 손절 즉시 매도
  sell_limit        — STOP 하이브리드: 손절가-margin 지정가 매도 (당일 유효)
  cancel            — 미체결 지정가 취소 (하이브리드 체크포인트 보강 전)

재시도: 네트워크/API 일시 오류 3회 (1·2·4초 백오프). 응답 오류는 재시도 안 함.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from autotrade import common, config as cfg, state as S
from kis.order import KISOrder, OrderType, OrderSide, OrderResult


@dataclass
class BrokerResult:
    ok: bool
    order_no: str = ""
    message: str = ""
    skipped: bool = False  # 멱등성 skip
    dry_run: bool = False


def _retry(fn, *args, retries: int = 3, label: str = "", **kwargs):
    """네트워크·일시 오류 재시도. 응답 자체(ok=False)는 재시도 안 함."""
    delay = 1.0
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                logger.warning(f"[broker] {label} 재시도 {attempt+1}/{retries}: {e}")
                time.sleep(delay)
                delay *= 2
            else:
                logger.error(f"[broker] {label} 최종 실패: {e}")
                raise


def _is_already_done(date_str: str, order_key: str) -> bool:
    st = S.order_status(date_str, order_key)
    return st in ("submitted", "filled")


def place_buy_market(
    order_mgr: KISOrder,
    code: str,
    quantity: int,
    order_key: str,
    date_str: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> BrokerResult:
    """시장가 매수 (15:18 집행용). 멱등성 보장.

    date_str: 멱등성 state 파일 날짜 키 (기본 오늘)
    """
    date_str = date_str or common.today_str()
    dry = common.is_dry_run() if dry_run is None else dry_run

    if _is_already_done(date_str, order_key):
        logger.info(f"[broker] [SKIP-DUP] 매수 {code} {quantity}주 key={order_key} (이미 처리됨)")
        return BrokerResult(ok=True, skipped=True)

    S.mark_order(date_str, order_key, "pending", {"side": "buy", "code": code, "qty": quantity, "type": "market"})

    if dry:
        logger.info(f"[broker] [DRY-RUN] 매수 {code} {quantity}주 (시장가)")
        S.mark_order(date_str, order_key, "submitted", {"order_no": "DRYRUN", "dry_run": True})
        return BrokerResult(ok=True, order_no="DRYRUN", dry_run=True)

    try:
        res: OrderResult = _retry(order_mgr.buy_market, code, quantity, label=f"buy_market {code}")
    except Exception as e:
        S.mark_order(date_str, order_key, "failed", {"error": str(e)[:200]})
        return BrokerResult(ok=False, message=str(e))

    if res.success:
        S.mark_order(date_str, order_key, "submitted", {"order_no": res.order_no})
        logger.info(f"[broker] 매수 접수: {code} {quantity}주 order_no={res.order_no}")
        return BrokerResult(ok=True, order_no=res.order_no)
    else:
        S.mark_order(date_str, order_key, "failed", {"error": res.message[:200]})
        return BrokerResult(ok=False, message=res.message)


def place_sell_market(
    order_mgr: KISOrder,
    code: str,
    quantity: int,
    order_key: str,
    date_str: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> BrokerResult:
    """시장가 매도 (08:50 집행 + 손절 즉시 매도). 멱등성 보장."""
    date_str = date_str or common.today_str()
    dry = common.is_dry_run() if dry_run is None else dry_run

    if _is_already_done(date_str, order_key):
        logger.info(f"[broker] [SKIP-DUP] 매도 {code} {quantity}주 key={order_key} (이미 처리됨)")
        return BrokerResult(ok=True, skipped=True)

    S.mark_order(date_str, order_key, "pending", {"side": "sell", "code": code, "qty": quantity, "type": "market"})

    if dry:
        logger.info(f"[broker] [DRY-RUN] 매도 {code} {quantity}주 (시장가)")
        S.mark_order(date_str, order_key, "submitted", {"order_no": "DRYRUN", "dry_run": True})
        return BrokerResult(ok=True, order_no="DRYRUN", dry_run=True)

    try:
        res: OrderResult = _retry(order_mgr.sell_market, code, quantity, label=f"sell_market {code}")
    except Exception as e:
        S.mark_order(date_str, order_key, "failed", {"error": str(e)[:200]})
        return BrokerResult(ok=False, message=str(e))

    if res.success:
        S.mark_order(date_str, order_key, "submitted", {"order_no": res.order_no})
        logger.info(f"[broker] 매도 접수: {code} {quantity}주 order_no={res.order_no}")
        return BrokerResult(ok=True, order_no=res.order_no)
    else:
        S.mark_order(date_str, order_key, "failed", {"error": res.message[:200]})
        return BrokerResult(ok=False, message=res.message)


def place_sell_limit(
    order_mgr: KISOrder,
    code: str,
    quantity: int,
    price: int,
    order_key: str,
    date_str: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> BrokerResult:
    """지정가 매도 (STOP 하이브리드: 손절가-margin 당일 유효 접수). 멱등성 보장."""
    date_str = date_str or common.today_str()
    dry = common.is_dry_run() if dry_run is None else dry_run

    if _is_already_done(date_str, order_key):
        logger.info(f"[broker] [SKIP-DUP] 지정가 매도 {code} {quantity}@{price:,} key={order_key} (이미 처리됨)")
        return BrokerResult(ok=True, skipped=True)

    S.mark_order(date_str, order_key, "pending", {"side": "sell", "code": code, "qty": quantity, "type": "limit", "price": price})

    if dry:
        logger.info(f"[broker] [DRY-RUN] 지정가 매도 {code} {quantity}주 @ {price:,}원")
        S.mark_order(date_str, order_key, "submitted", {"order_no": "DRYRUN", "dry_run": True})
        return BrokerResult(ok=True, order_no="DRYRUN", dry_run=True)

    try:
        res: OrderResult = _retry(
            order_mgr.sell, code, quantity, price=price, order_type=OrderType.LIMIT,
            label=f"sell_limit {code}@{price}",
        )
    except Exception as e:
        S.mark_order(date_str, order_key, "failed", {"error": str(e)[:200]})
        return BrokerResult(ok=False, message=str(e))

    if res.success:
        S.mark_order(date_str, order_key, "submitted", {"order_no": res.order_no, "price": price})
        logger.info(f"[broker] 지정가 매도 접수: {code} {quantity}주 @ {price:,}원 order_no={res.order_no}")
        return BrokerResult(ok=True, order_no=res.order_no)
    else:
        S.mark_order(date_str, order_key, "failed", {"error": res.message[:200]})
        return BrokerResult(ok=False, message=res.message)


def cancel_order(
    order_mgr: KISOrder,
    order_no: str,
    code: str,
    quantity: int,
    price: int,
    order_type_code: str = OrderType.LIMIT.value,
    dry_run: Optional[bool] = None,
) -> bool:
    """미체결 주문 취소. (하이브리드 체크포인트 보강 전 지정가 취소용)"""
    dry = common.is_dry_run() if dry_run is None else dry_run
    if dry:
        logger.info(f"[broker] [DRY-RUN] 주문 취소 {code} order_no={order_no}")
        return True
    try:
        return _retry(order_mgr.cancel, order_no, code, quantity, price, order_type_code, label=f"cancel {order_no}")
    except Exception as e:
        logger.error(f"[broker] 취소 실패 {order_no}: {e}")
        return False


def list_pending(order_mgr: KISOrder) -> list[dict]:
    """미체결 조회 (매수·매도 지정가 포함)."""
    try:
        return order_mgr.get_pending_orders()
    except Exception as e:
        logger.warning(f"[broker] 미체결 조회 실패: {e}")
        return []
