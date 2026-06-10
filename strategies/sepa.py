"""
전략4: SEPA 자동매매 (Mark Minervini)

매수 조건: sepa_data.json 의 breakout_confirmed=True 종목
  - 트렌드 템플릿 7개 조건 모두 충족
  - RS (상대강도) ≥ 70 (유니버스 내 상위 30%)
  - VCP 패턴 감지 + 당일 피벗 브레이크아웃 + 거래량 급증

매도 조건:
  - 손절: 매수가 대비 -7%
  - [고점 +20% 미달] 트레일링스탑: 고점 대비 -10% (고점 +10% 이상일 때 활성화)
  - [고점 +20% 이상] MA이탈: MA21 < MA62 && MA62 5일 하락 → 청산 (익절 천장 제거, 러너 보유)

포지션 관리:
  - S2+S3+S4 공유 슬롯: 기본 4개 (S1=1 고정, 총 5개)
  - 슬롯당 예산: 총자산의 20%
  - 시장 하락장: 신규 매수 보류

실행 시각: 09:00 정규장 개장 시 시장가 주문
"""
import time
from datetime import datetime

import pytz
from loguru import logger

import data.ma_store as ma_store
from data.sepa_store import (
    get_buy_candidates,
    load_positions,
    add_position,
    remove_position,
    update_position_peak,
    load_data as load_sepa_data,
)
from data.shared_slots import count_shared

KST              = pytz.timezone("Asia/Seoul")
S2_S3_S4_BASE    = 4      # S2+S3+S4 공유 슬롯 기본값
SLOT_RATIO       = 0.20   # 슬롯당 총자산 비율
STOP_LOSS        = 0.07   # -7%
RUNNER_THRESHOLD = 0.20   # 고점이 이 수준 이상이면 MA이탈 청산으로 전환
TRAIL_STOP_PCT   = 0.10   # 고점 대비 -10% (러너 미달 구간 적용)
TRAIL_STOP_MIN   = 0.10   # 트레일링스탑 활성화 최소 고점 수익률
TAKE_PROFIT      = 0.20   # +20% (러너 미달 구간 고정 익절선)
TAKE_PROFIT_EXT  = 0.25   # +25% (21일 이내 +15% 달성 시)


class SEPAStrategy:
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
            logger.info(f"[S4] 09:00 개장 대기 ({wait_sec:.0f}초)")
            time.sleep(wait_sec)

        # 지연 감지: 09:45 이후면 시장가 주문 시간대 초과
        _now = datetime.now(KST)
        if _now.hour > 9 or (_now.hour == 9 and _now.minute >= 45):
            logger.warning(f"[S4] 지연 실행 감지 ({_now.strftime('%H:%M')}) — 시장가 주문 불가, 종료")
            return

        logger.info(f"[S4 SEPA] 실행 시작 {_now.strftime('%Y-%m-%d %H:%M')}")

        try:
            self._process_exits()
        except Exception as e:
            logger.error(f"[S4] 매도 처리 중 오류: {e}")

        try:
            self._process_entries()
        except Exception as e:
            logger.error(f"[S4] 매수 처리 중 오류: {e}")

        logger.info("[S4 SEPA] 실행 완료")

    # ── 매도 처리 ─────────────────────────────────────────────────────
    def _process_exits(self) -> None:
        positions = load_positions()
        if not positions:
            logger.info("[S4] 보유 포지션 없음")
            return

        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        for code, pos in list(positions.items()):
            name        = pos.get("name", code)
            entry_price = pos.get("entry_price", 0)
            entry_date  = pos.get("entry_date", today_str)
            quantity    = pos.get("quantity", 0)
            early_trig  = pos.get("early_gain_triggered", False)

            if not entry_price or not quantity:
                continue

            try:
                quote     = self.market.get_quote(code)
                current   = quote.price
                gain      = (current - entry_price) / entry_price
                days_held = (
                    datetime.strptime(today_str, "%Y-%m-%d")
                    - datetime.strptime(entry_date, "%Y-%m-%d")
                ).days
            except Exception as e:
                logger.warning(f"[S4] [{code}] {name} 현재가 조회 실패: {e}")
                continue

            update_position_peak(code, current, today_str)
            peak_price = max(pos.get("peak_price", entry_price), current)
            peak_gain  = (peak_price - entry_price) / entry_price
            target     = TAKE_PROFIT_EXT if early_trig else TAKE_PROFIT

            # 손절 -7%
            if gain <= -STOP_LOSS:
                logger.warning(
                    f"[S4 손절] [{code}] {name}  "
                    f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}"
                )
                self._sell_market(code, name, quantity, entry_price, current, "손절(-7%)")
                continue

            # 러너 모드: 고점 +20% 이상 → MA이탈 시 청산
            # MA이탈 판정은 전날 저녁 SEPA 배치에서 설정 (ma_exit_pending 플래그)
            if peak_gain >= RUNNER_THRESHOLD:
                if pos.get("ma_exit_pending"):
                    label = f"MA이탈(고점{peak_gain:+.1%})"
                    logger.info(
                        f"[S4 MA이탈] [{code}] {name}  "
                        f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  {label}"
                    )
                    self._sell_market(code, name, quantity, entry_price, current, label)
                else:
                    logger.info(
                        f"[S4 러너보유] [{code}] {name}  "
                        f"현재:{gain:+.2%}  고점:{peak_gain:+.2%}  보유:{days_held}일"
                    )
                continue

            # +20% 미달 구간: 트레일링스탑 / 익절
            if peak_gain >= TRAIL_STOP_MIN and current < peak_price * (1 - TRAIL_STOP_PCT):
                logger.info(
                    f"[S4 트레일링스탑] [{code}] {name}  "
                    f"고점:{peak_price:,}(+{peak_gain:.2%}) → 현재:{current:,}({gain:+.2%})"
                )
                self._sell_market(
                    code, name, quantity, entry_price, current,
                    f"트레일링스탑(고점-{TRAIL_STOP_PCT:.0%})",
                )
                continue

            if gain >= target:
                label = f"익절(+{target:.0%}" + (" 확장목표)" if early_trig else ")")
                logger.info(
                    f"[S4 익절] [{code}] {name}  "
                    f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  {label}"
                )
                self._sell_market(code, name, quantity, entry_price, current, label)
                continue

            logger.info(
                f"[S4 보유] [{code}] {name}  "
                f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  "
                f"목표:{target:+.0%}  보유:{days_held}일"
            )

    # ── 매수 처리 ─────────────────────────────────────────────────────
    def _process_entries(self) -> None:
        sepa_data      = load_sepa_data()
        market_uptrend = sepa_data.get("market_uptrend", False)
        updated_at     = sepa_data.get("updated_at", "")
        today_str      = datetime.now(KST).strftime("%Y-%m-%d")

        if not market_uptrend:
            logger.info("[S4] 시장 하락장 — 신규 매수 보류")
            return

        # 스크리닝 데이터 신선도 확인
        if updated_at < today_str:
            logger.warning(
                f"[S4] sepa_data가 오늘 업데이트 안됨 (업데이트:{updated_at}, 오늘:{today_str}) "
                f"— 신규 매수 보류"
            )
            return

        # S2~S4 공유 슬롯 계산
        s2_n, s3_n, s4_n = count_shared()
        total_shared = s2_n + s3_n + s4_n

        try:
            bal      = self.account.get_balance()
            base_cap = ma_store.get_base_capital()
            extra    = ma_store.extra_slots(base_cap, bal.total_eval) if base_cap else 0
        except Exception as e:
            logger.error(f"[S4] 잔고 조회 실패: {e}")
            return

        max_shared = S2_S3_S4_BASE + extra
        slots_free = max_shared - total_shared

        if slots_free <= 0:
            logger.info(
                f"[S4] 공유슬롯 만석 ({total_shared}/{max_shared}, "
                f"S2:{s2_n} S3:{s3_n} S4:{s4_n}) — 매수 보류"
            )
            return

        s4_positions = load_positions()
        candidates = [
            (code, info)
            for code, info in get_buy_candidates()
            if code not in s4_positions
        ]

        if not candidates:
            logger.info("[S4] 매수 후보 없음 (브레이크아웃 종목 없음 또는 이미 보유)")
            return

        per_slot = int(bal.total_eval * SLOT_RATIO)
        if per_slot > bal.cash:
            per_slot = bal.cash
        if per_slot < 10_000:
            logger.info(f"[S4] 가용 예산 부족 (슬롯당 {per_slot:,}원) — 매수 건너뜀")
            return

        logger.info(
            f"[S4] 매수 후보 {len(candidates)}종목  "
            f"잔여슬롯 {slots_free}개  슬롯당 예산 {per_slot:,}원 (총자산 20%)"
        )

        bought = 0
        for code, info in candidates[:slots_free]:
            name = info.get("name", code)
            try:
                quote = self.market.get_quote(code)
                price = quote.price
                if price <= 0:
                    continue
                qty = per_slot // price
                if qty <= 0:
                    logger.info(f"[S4] [{code}] {name}  주가({price:,}) > 슬롯예산 → 건너뜀")
                    continue

                logger.info(
                    f"[S4 매수] [{code}] {name}  현재가:{price:,}  수량:{qty}주  "
                    f"금액:{price*qty:,}원  RS={info.get('rs_score',0):.0f}  "
                    f"pivot={info.get('pivot',0):,}"
                )

                if not self.is_paper:
                    resp = self.order.buy_market(code, qty)
                    logger.info(f"[S4] 매수 주문 응답: {resp}")
                else:
                    logger.info("[S4] 모의투자 — 주문 생략, 포지션만 기록")

                add_position(code, name, today_str, price, qty)
                bought += 1

                if self.notifier:
                    self.notifier.notify(
                        f"[S4 매수] [{code}] {name}\n"
                        f"현재가:{price:,}원  수량:{qty}주  금액:{price*qty:,}원\n"
                        f"RS={info.get('rs_score',0):.0f}  "
                        f"pivot={info.get('pivot',0):,}  "
                        f"tight={info.get('tight_range_pct',0):.1f}%"
                    )

            except Exception as e:
                logger.error(f"[S4] [{code}] {name} 매수 실패: {e}")

        if bought == 0:
            logger.info("[S4] 실제 매수 없음")

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
                logger.info(f"[S4] 매도 주문 응답: {resp}")
            else:
                logger.info("[S4] 모의투자 — 주문 생략")

            remove_position(code)

            msg = (
                f"[S4 매도] [{code}] {name}  {reason}\n"
                f"매수:{entry_price:,} → 현재:{current_price:,}  "
                f"{pnl_pct:+.2%} ({pnl:+,}원)"
            )
            logger.info(msg)
            if self.notifier:
                self.notifier.notify(msg)

        except Exception as e:
            logger.error(f"[S4] [{code}] {name} 매도 실패: {e}")
