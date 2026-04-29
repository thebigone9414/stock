"""
옥동자 전략 2호 — 이동평균선 정배열 중장기 전략

[매수 조건]
  조건 1: 5 > 21 > 62 > 248 > 744 완전 정배열이 전날 처음 달성된 종목
  조건 2: 62, 248, 744 이평선 모두 20일 이상 연속 상승 추세

[매도 조건]
  ma21이 ma62 아래로 이동한 다음날 아침 시장가 매도

[포지션 관리]
  총계좌의 20%씩, 최대 4포지션 동시 보유
  슬롯 만석 + 신규 신호 → 텔레그램 알림
"""
from datetime import datetime
from typing import Optional

import pytz
from loguru import logger

import data.ma_store as ma_store
from kis.market import KISMarket
from kis.order import KISOrder, OrderType
from kis.account import KISAccount
from utils.notifier import Notifier

KST              = pytz.timezone("Asia/Seoul")
MAX_POSITIONS    = 4
SLOT_RATIO       = 0.20
UPTREND_MIN_DAYS = 20


class MACrossStrategy:
    """이동평균선 정배열 중장기 전략 (Strategy 2)"""

    name = "MACross_GoldenAlign"

    def __init__(
        self,
        market: KISMarket,
        order: KISOrder,
        account: KISAccount,
        notifier: Notifier,
        is_paper: bool = True,
    ):
        self.market   = market
        self.order    = order
        self.account  = account
        self.notifier = notifier
        self.is_paper = is_paper

    # ═══════════════════════════════════════════════════════════════════
    def run(self) -> None:
        mode  = "모의투자" if self.is_paper else "실전투자"
        today = datetime.now(KST).strftime("%Y-%m-%d (%a)")
        logger.info(f"[MA전략] 시작 [{mode}] {today}")

        # MA 테이블 로드
        data    = ma_store.load()
        updated = data.get("updated_at", "")
        stocks  = data.get("stocks", {})

        if not stocks:
            msg = "[MA전략] MA 테이블 없음 — 배치(update_ma) 먼저 실행 필요, 오늘 건너뜀"
            logger.warning(msg)
            self.notifier.notify(msg)
            return

        logger.info(f"[MA전략] MA 기준일: {updated} ({len(stocks)}종목)")

        # 잔고 조회
        try:
            balance     = self.account.get_balance()
            total_value = balance.total_eval + balance.cash
            slot_budget = int(total_value * SLOT_RATIO)
        except Exception as e:
            logger.error(f"[MA전략] 잔고 조회 실패: {e}")
            return

        # 포지션 로드 + KIS 실잔고 대조
        positions = ma_store.get_positions()
        positions = self._reconcile(positions, balance)

        logger.info(
            f"[MA전략] 총자산:{total_value:,}원  슬롯예산(20%):{slot_budget:,}원  "
            f"보유:{len(positions)}/{MAX_POSITIONS}"
        )

        # ── 1. 매도 ─────────────────────────────────────────────────
        for code in list(positions):
            s = stocks.get(code, {})
            if s.get("ma21_below_ma62"):
                logger.info(
                    f"[MA전략 매도신호] [{code}] {positions[code]['name']} "
                    f"— ma21({s.get('ma21',0):,.0f}) < ma62({s.get('ma62',0):,.0f})"
                )
                self._sell(code, positions[code])
                del positions[code]
                ma_store.remove_position(code)

        # ── 2. 매수 ─────────────────────────────────────────────────
        available_slots = MAX_POSITIONS - len(positions)
        candidates      = self._find_candidates(stocks, positions)

        if not candidates:
            logger.info("[MA전략] 매수 신호 없음")
        elif available_slots == 0:
            self._notify_full(candidates)
        else:
            avail_cash = balance.cash
            for cand in candidates[:available_slots]:
                code  = cand["code"]
                name  = cand["name"]
                price = cand.get("close", 0)

                # 현재가 재확인
                try:
                    q     = self.market.get_quote(code)
                    price = q.price or price
                except Exception:
                    pass

                if price <= 0:
                    continue

                budget = min(slot_budget, avail_cash)
                qty    = int(budget * 0.99 / price)
                if qty <= 0:
                    logger.warning(f"[MA전략] [{code}] 예산 부족 ({budget:,}원/{price:,}원)")
                    continue

                logger.info(
                    f"[MA전략 매수] [{code}] {name} — "
                    f"정배열첫날 / 62추세:{cand['ma62_trend_days']}일 "
                    f"248추세:{cand['ma248_trend_days']}일 744추세:{cand['ma744_trend_days']}일"
                )
                result = self.order.buy(code, qty, 0, OrderType.MARKET)
                if result.success:
                    entry_date = datetime.now(KST).strftime("%Y-%m-%d")
                    ma_store.add_position(code, name, entry_date, price, qty)
                    avail_cash -= price * qty
                    self.notifier.notify(
                        f"[MA전략 매수] [{code}] {name}\n"
                        f"수량:{qty:,}주  기준가:{price:,}원\n"
                        f"62일추세:{cand['ma62_trend_days']}일  "
                        f"248일추세:{cand['ma248_trend_days']}일  "
                        f"744일추세:{cand['ma744_trend_days']}일"
                    )
                else:
                    logger.error(f"[MA전략 매수 실패] {code}: {result.message}")
                    self.notifier.notify(f"[MA전략 매수 실패] [{code}] {name}: {result.message}")

        logger.info(f"[MA전략] 완료")

    # ═══════════════════════════════════════════════════════════════════
    def _reconcile(self, json_positions: dict, balance) -> dict:
        """JSON 포지션과 KIS 실잔고 대조 — KIS에 없는 포지션은 제거"""
        kis_codes = {p.code for p in balance.positions}
        for code in list(json_positions):
            if code not in kis_codes:
                logger.warning(f"[MA전략] [{code}] KIS 잔고에 없음 → JSON 포지션 제거")
                ma_store.remove_position(code)
                del json_positions[code]
        return json_positions

    def _sell(self, code: str, pos: dict) -> None:
        qty = pos.get("quantity", 0)
        try:
            b    = self.account.get_balance()
            held = next((p for p in b.positions if p.code == code), None)
            if held:
                qty = held.quantity
        except Exception:
            pass

        if qty <= 0:
            logger.warning(f"[MA전략 매도] {code} 수량 0 — 이미 청산")
            return

        result = self.order.sell(code, qty, 0, OrderType.MARKET)
        if result.success:
            msg = (
                f"[MA전략 매도] [{code}] {pos['name']}\n"
                f"수량:{qty:,}주  매수가:{pos.get('entry_price',0):,}원\n"
                f"사유: ma21 < ma62 데드크로스"
            )
            logger.info(msg)
            self.notifier.notify(msg)
        else:
            err = f"[MA전략 매도 실패] [{code}] {pos['name']}: {result.message}"
            logger.error(err)
            self.notifier.notify(err)

    def _find_candidates(self, stocks: dict, positions: dict) -> list:
        """매수 조건 모두 충족 종목 리스트 (62일 추세 내림차순)"""
        result = []
        for code, s in stocks.items():
            if code in positions:
                continue
            if self._is_buy_signal(s):
                result.append({"code": code, **s})
        result.sort(key=lambda x: x.get("ma62_trend_days", 0), reverse=True)
        return result

    def _is_buy_signal(self, s: dict) -> bool:
        """매수 조건 1 + 조건 2 동시 충족 여부"""
        # 조건 1: 완전 정배열이 전날 처음 달성
        if not (s.get("fully_aligned") and not s.get("prev_fully_aligned")):
            return False
        # 조건 2: 62/248/744 이평선 20일 이상 연속 상승
        if s.get("ma62_trend_days", 0)  < UPTREND_MIN_DAYS:
            return False
        if s.get("ma248_trend_days", 0) < UPTREND_MIN_DAYS:
            return False
        if s.get("ma744_trend_days", 0) < UPTREND_MIN_DAYS:
            return False
        return True

    def _notify_full(self, candidates: list) -> None:
        msg = (
            f"[MA전략] 슬롯 만석(4/4) — 신규 신호 {len(candidates)}종목\n"
            + "\n".join(
                f"  [{c['code']}] {c.get('name', c['code'])} "
                f"62추세:{c['ma62_trend_days']}일"
                for c in candidates[:5]
            )
            + "\n※ 잔고 추가 후 신규 매수 가능"
        )
        logger.warning(msg)
        self.notifier.notify(msg)
