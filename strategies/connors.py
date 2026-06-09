"""
전략6: Connors RSI(2) 자동매매 (Larry Connors)

매수 조건: connors_data.json 의 all_pass=True 종목
  - KODEX200 종가 > MA200 (장기 상승장)
  - 개별 종목 종가 > MA200
  - RSI(2) < 10 (단기 과매도)

매도 조건 (단기 평균회귀):
  - RSI(2) >= 65 청산 신호  (배치에서 계산, 다음날 시가 매도)
  - 손절       : 매수가 대비 -5% (단기 전략이므로 타이트)
  - 타임스탑   : 10 거래일

포지션 관리:
  - S2~S6 공유 슬롯: 기본 4개
  - 슬롯당 예산: 총자산의 20%
  - 시장 하락(market_uptrend=False): 신규 매수 보류

실행 시각: 09:00 정규장 개장 시 시장가 주문
"""
import time
from datetime import datetime

import pytz
from loguru import logger

import data.ma_store as ma_store
from data.shared_slots import count_shared
from data.connors_store import (
    get_buy_candidates,
    load_positions,
    add_position,
    remove_position,
    get_exit_signal,
    get_rsi2,
    load_data as load_connors_data,
)

KST          = pytz.timezone("Asia/Seoul")
S_BASE_SLOTS = 4
STOP_LOSS    = 0.05    # -5% (단기 전략)
TIME_STOP    = 10      # 거래일


class ConnorsRSIStrategy:
    def __init__(self, market, order, account, notifier=None, is_paper: bool = True):
        self.market   = market
        self.order    = order
        self.account  = account
        self.notifier = notifier
        self.is_paper = is_paper

    # ── 진입점 ────────────────────────────────────────────────────────
    def run(self) -> None:
        _now_dt  = datetime.now(KST)
        _open_dt = _now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        if _now_dt < _open_dt:
            wait_sec = (_open_dt - _now_dt).total_seconds()
            logger.info(f"[S6] 09:00 개장 대기 ({wait_sec:.0f}초)")
            time.sleep(wait_sec)

        _now = datetime.now(KST)
        if _now.hour > 9 or (_now.hour == 9 and _now.minute >= 45):
            logger.warning(f"[S6] 지연 실행 감지 ({_now.strftime('%H:%M')}) — 시장가 주문 불가, 종료")
            return

        logger.info(f"[S6 Connors RSI(2)] 실행 시작 {_now.strftime('%Y-%m-%d %H:%M')}")

        try:
            self._process_exits()
        except Exception as e:
            logger.error(f"[S6] 매도 처리 중 오류: {e}")

        try:
            self._process_entries()
        except Exception as e:
            logger.error(f"[S6] 매수 처리 중 오류: {e}")

        logger.info("[S6 Connors RSI(2)] 실행 완료")

    # ── 매도 처리 ─────────────────────────────────────────────────────
    def _process_exits(self) -> None:
        positions = load_positions()
        if not positions:
            logger.info("[S6] 보유 포지션 없음")
            return

        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        for code, pos in list(positions.items()):
            name        = pos.get("name", code)
            entry_price = pos.get("entry_price", 0)
            entry_date  = pos.get("entry_date", today_str)
            quantity    = pos.get("quantity", 0)

            if not entry_price or not quantity:
                continue

            try:
                quote   = self.market.get_quote(code)
                current = quote.price
                gain    = (current - entry_price) / entry_price
                days_held = (
                    datetime.strptime(today_str, "%Y-%m-%d")
                    - datetime.strptime(entry_date, "%Y-%m-%d")
                ).days
            except Exception as e:
                logger.warning(f"[S6] [{code}] {name} 현재가 조회 실패: {e}")
                continue

            rsi2       = get_rsi2(code)
            rsi2_label = f"RSI(2)={rsi2:.1f}" if rsi2 >= 0 else "RSI(2)=N/A"

            reason = None
            if gain <= -STOP_LOSS:
                reason = f"손절(-5%)  {rsi2_label}"
            elif days_held >= TIME_STOP:
                reason = f"타임스탑({days_held}일)  {rsi2_label}"
            elif get_exit_signal(code):
                reason = f"RSI(2)청산(>=65)  {rsi2_label}"

            if reason:
                logger.info(
                    f"[S6 매도] [{code}] {name}  "
                    f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  {reason}"
                )
                self._sell_market(code, name, quantity, entry_price, current, reason)
                continue

            logger.info(
                f"[S6 보유] [{code}] {name}  "
                f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  "
                f"보유:{days_held}일  {rsi2_label}"
            )

    # ── 매수 처리 ─────────────────────────────────────────────────────
    def _process_entries(self) -> None:
        connors_data   = load_connors_data()
        market_uptrend = connors_data.get("market_uptrend", False)
        updated_at     = connors_data.get("updated_at", "")
        today_str      = datetime.now(KST).strftime("%Y-%m-%d")

        if not market_uptrend:
            logger.info("[S6] 시장 하락장(KODEX200<=MA200) — 신규 매수 보류")
            return

        s2_n, s3_n, s4_n, s5_n, s6_n = count_shared()
        total_shared = s2_n + s3_n + s4_n + s5_n + s6_n

        try:
            bal      = self.account.get_balance()
            base_cap = ma_store.get_base_capital()
            extra    = ma_store.extra_slots(base_cap, bal.total_eval) if base_cap else 0
        except Exception as e:
            logger.error(f"[S6] 잔고 조회 실패: {e}")
            return

        max_shared = S_BASE_SLOTS + extra
        slots_free = max_shared - total_shared

        if slots_free <= 0:
            logger.info(
                f"[S6] 공유슬롯 만석 ({total_shared}/{max_shared}, "
                f"S2:{s2_n} S3:{s3_n} S4:{s4_n} S5:{s5_n} S6:{s6_n}) — 매수 보류"
            )
            return

        if updated_at < today_str:
            logger.warning(
                f"[S6] connors_data가 오늘 업데이트 안됨 (업데이트:{updated_at}) — 신규 매수 보류"
            )
            return

        positions  = load_positions()
        candidates = [
            (code, info)
            for code, info in get_buy_candidates()
            if code not in positions
        ]

        if not candidates:
            logger.info("[S6] 매수 후보 없음 (RSI(2)<10 종목 없음 또는 이미 보유)")
            return

        cash     = bal.cash
        per_slot = int(bal.total_eval * 0.20)
        if per_slot > cash:
            per_slot = cash

        if per_slot < 10_000:
            logger.info(f"[S6] 가용 예산 부족 ({per_slot:,}원) — 매수 건너뜀")
            return

        logger.info(
            f"[S6] 매수 후보 {len(candidates)}종목  "
            f"잔여슬롯 {slots_free}개  슬롯당 예산 {per_slot:,}원"
        )

        bought = 0
        for code, info in candidates[:slots_free]:
            name = info.get("name", code)
            rsi2 = info.get("rsi2", -1)
            try:
                quote = self.market.get_quote(code)
                price = quote.price
                if price <= 0:
                    continue
                qty = per_slot // price
                if qty <= 0:
                    logger.info(f"[S6] [{code}] {name}  주가({price:,}) > 슬롯예산 → 건너뜀")
                    continue

                logger.info(
                    f"[S6 매수] [{code}] {name}  현재가:{price:,}  수량:{qty}주  "
                    f"금액:{price*qty:,}원  RSI(2)={rsi2:.1f}"
                )

                if not self.is_paper:
                    resp = self.order.buy_market(code, qty)
                    logger.info(f"[S6] 매수 주문 응답: {resp}")
                else:
                    logger.info("[S6] 모의투자 — 주문 생략, 포지션만 기록")

                add_position(code, name, today_str, price, qty)
                bought += 1

                if self.notifier:
                    self.notifier.notify(
                        f"[S6 Connors 매수] [{code}] {name}\n"
                        f"현재가:{price:,}원  수량:{qty}주  금액:{price*qty:,}원\n"
                        f"RSI(2)={rsi2:.1f}  MA200:{info.get('ma200',0):,.0f}"
                    )

            except Exception as e:
                logger.error(f"[S6] [{code}] {name} 매수 실패: {e}")

        if bought == 0:
            logger.info("[S6] 실제 매수 없음")

    # ── 시장가 매도 ───────────────────────────────────────────────────
    def _sell_market(
        self, code: str, name: str, quantity: int,
        entry_price: int, current_price: int, reason: str,
    ) -> None:
        pnl     = (current_price - entry_price) * quantity
        pnl_pct = (current_price - entry_price) / entry_price

        try:
            if not self.is_paper:
                resp = self.order.sell_market(code, quantity)
                logger.info(f"[S6] 매도 주문 응답: {resp}")
            else:
                logger.info("[S6] 모의투자 — 주문 생략")

            remove_position(code)

            msg = (
                f"[S6 매도] [{code}] {name}  {reason}\n"
                f"매수:{entry_price:,} → 현재:{current_price:,}  "
                f"{pnl_pct:+.2%} ({pnl:+,}원)"
            )
            logger.info(msg)
            if self.notifier:
                self.notifier.notify(msg)

        except Exception as e:
            logger.error(f"[S6] [{code}] {name} 매도 실패: {e}")
