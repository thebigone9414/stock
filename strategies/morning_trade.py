"""
통합 아침 매매 실행 (S2/S3/S4 공통)

08:55 시작 → 09:00 정규장 개장 → trade_queue.json 실행
  1. 매도: sell 리스트 순서대로 시장가 매도 → 각 전략 포지션 제거
  2. 매수: buy  리스트 순서대로 시장가 매수 → 각 전략 포지션 추가

trade_decision.py(저녁 20:00)가 결정한 trade_queue.json만 실행.
결정 없으면 종료 (stale 큐 실행 방지).
"""
import time
from datetime import datetime

import pytz
from loguru import logger

import data.ma_store as ma_store
import data.canslim_store as canslim_store
import data.sepa_store as sepa_store
import data.momentum_store as momentum_store
import data.manual_store as manual_store
from data.trade_queue_store import get_today_queue, save_queue, QUEUE_PATH, git_commit_push

KST = pytz.timezone("Asia/Seoul")


class MorningTradeStrategy:
    def __init__(self, market, order, account, notifier=None, is_paper: bool = True):
        self.market   = market
        self.order    = order
        self.account  = account
        self.notifier = notifier
        self.is_paper = is_paper

    def run(self) -> None:
        _now_dt  = datetime.now(KST)
        _open_dt = _now_dt.replace(hour=9, minute=0, second=0, microsecond=0)

        if _now_dt < _open_dt:
            wait_sec = (_open_dt - _now_dt).total_seconds()
            logger.info(f"[morning] 09:00 개장 대기 ({wait_sec:.0f}초)")
            time.sleep(wait_sec)

        _now = datetime.now(KST)
        if _now.hour > 9 or (_now.hour == 9 and _now.minute >= 45):
            logger.warning(
                f"[morning] 지연 실행 감지 ({_now.strftime('%H:%M')}) — "
                "시장가 주문 시간대 초과, 종료"
            )
            return

        today_str = _now.strftime("%Y-%m-%d")
        logger.info(f"[morning] 실행 시작 {_now.strftime('%Y-%m-%d %H:%M')}")

        queue = get_today_queue(today_str)
        if queue is None:
            logger.warning(
                f"[morning] {today_str} 매매 큐 없음 "
                "(trade_decision 미실행 또는 이미 완료) — 종료"
            )
            return

        sell_list = queue.get("sell", [])
        buy_list  = queue.get("buy",  [])
        logger.info(f"[morning] 큐 로드: 매도 {len(sell_list)}건  매수 {len(buy_list)}건")

        # ── 매도 실행 ──────────────────────────────────────────────────
        for item in sell_list:
            self._execute_sell(item)

        # ── 매수 실행 ──────────────────────────────────────────────────
        for item in buy_list:
            self._execute_buy(item, today_str)

        # 큐 실행 완료 표시 — get_today_queue가 executed=True 큐를 반환하지 않아 중복 실행 방지
        queue["executed"] = True
        save_queue(queue)
        git_commit_push([str(QUEUE_PATH)], f"chore: 매매큐 실행완료 {today_str}")

        logger.info("[morning] 실행 완료")

    def _execute_sell(self, item: dict) -> None:
        code        = item["code"]
        name        = item["name"]
        strategy    = item["strategy"]
        reason      = item["reason"]
        quantity    = item["quantity"]
        entry_price = item["entry_price"]
        entry_date  = item.get("entry_date", "")

        try:
            current = self.market.get_quote(code).price
        except Exception:
            current = entry_price

        pnl     = (current - entry_price) * quantity
        pnl_pct = (current - entry_price) / entry_price if entry_price else 0.0

        try:
            if not self.is_paper:
                resp = self.order.sell_market(code, quantity)
                logger.info(f"[morning] [{strategy}] 매도 주문 응답: {resp}")
            else:
                logger.info(f"[morning] [{strategy}] 모의투자 — 주문 생략")

            if strategy == "S2":
                ma_store.remove_position(code, entry_date)
            elif strategy == "S3":
                canslim_store.remove_position(code, entry_date)
                if "손절" in reason:
                    canslim_store.add_to_stop_blacklist(
                        code, datetime.now(KST).strftime("%Y-%m-%d")
                    )
            elif strategy == "S4":
                sepa_store.remove_position(code, entry_date)
            elif strategy == "S5":
                momentum_store.remove_position(code, entry_date)
            elif strategy == "수동":
                manual_store.remove_position(code, entry_date)

            msg = (
                f"[{strategy} 매도] [{code}] {name}  {reason}\n"
                f"매수:{entry_price:,} → 현재:{current:,}  "
                f"{pnl_pct:+.2%} ({pnl:+,}원)"
            )
            logger.info(msg)
            if self.notifier:
                self.notifier.notify(msg)

        except Exception as e:
            logger.error(f"[morning] [{strategy}] [{code}] {name} 매도 실패: {e}")

    def _execute_buy(self, item: dict, today_str: str) -> None:
        code     = item["code"]
        name     = item["name"]
        strategy = item["strategy"]
        budget   = item["per_slot_budget"]

        try:
            price = self.market.get_quote(code).price
            if price <= 0:
                logger.warning(f"[morning] [{strategy}] [{code}] 현재가 0 — 건너뜀")
                return
            qty = budget // price
            if qty <= 0:
                logger.info(
                    f"[morning] [{strategy}] [{code}] {name}  "
                    f"주가({price:,}) > 슬롯예산({budget:,}) → 건너뜀"
                )
                return

            logger.info(
                f"[morning] [{strategy} 매수] [{code}] {name}  "
                f"현재가:{price:,}원  수량:{qty}주  금액:{price*qty:,}원"
            )

            if not self.is_paper:
                resp = self.order.buy_market(code, qty)
                logger.info(f"[morning] [{strategy}] 매수 주문 응답: {resp}")
            else:
                logger.info(f"[morning] [{strategy}] 모의투자 — 주문 생략, 포지션만 기록")

            if strategy == "S2":
                ma_store.add_position(code, name, today_str, price, qty)
            elif strategy == "S3":
                canslim_store.add_position(code, name, today_str, price, qty)
            elif strategy == "S4":
                sepa_store.add_position(code, name, today_str, price, qty)
            elif strategy == "S5":
                buyer_type = item.get("buyer_type", "foreign")
                momentum_store.add_position(code, name, today_str, price, qty, buyer_type)

            msg = (
                f"[{strategy} 매수] [{code}] {name}\n"
                f"현재가:{price:,}원  수량:{qty}주  금액:{price*qty:,}원"
            )
            if self.notifier:
                self.notifier.notify(msg)

        except Exception as e:
            logger.error(f"[morning] [{strategy}] [{code}] {name} 매수 실패: {e}")
