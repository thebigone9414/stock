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
    load_positions,
    add_position,
    remove_position,
    load_data as load_sepa_data,
    get_entry_pending,
    set_entry_pending,
)
from data.shared_slots import count_shared

KST              = pytz.timezone("Asia/Seoul")
S2_S3_S4_BASE    = 10     # S2+S3+S4 공유 슬롯 기본값
SLOT_RATIO       = 0.10   # 슬롯당 총자산 비율
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

    # ── 매도 처리 (저녁 배치 플래그 실행) ────────────────────────────
    def _process_exits(self) -> None:
        positions = load_positions()
        if not positions:
            logger.info("[S4] 보유 포지션 없음")
            return

        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        for code, tranches in list(positions.items()):
            for entry_date, pos in list(tranches.items()):
                name        = pos.get("name", code)
                entry_price = pos.get("entry_price", 0)
                quantity    = pos.get("quantity", 0)
                early_trig  = pos.get("early_gain_triggered", False)
                peak_price  = pos.get("peak_price", entry_price)
                peak_gain   = (peak_price - entry_price) / entry_price if entry_price else 0.0

                if not entry_price or not quantity:
                    continue

                # 매도 사유 — 저녁 배치가 설정한 플래그 기반 (우선순위 순)
                if pos.get("stop_loss_pending"):
                    reason = "손절(-7%)"
                elif pos.get("ma_exit_pending"):
                    reason = f"MA이탈(고점{peak_gain:+.1%})"
                elif pos.get("trail_stop_pending"):
                    reason = f"트레일링스탑(고점-{TRAIL_STOP_PCT:.0%})"
                elif pos.get("take_profit_pending"):
                    target = TAKE_PROFIT_EXT if early_trig else TAKE_PROFIT
                    reason = f"익절(+{target:.0%}" + (" 확장목표)" if early_trig else ")")
                else:
                    # 플래그 없음 → 보유
                    try:
                        days_held = (
                            datetime.strptime(today_str, "%Y-%m-%d")
                            - datetime.strptime(entry_date, "%Y-%m-%d")
                        ).days
                    except ValueError:
                        days_held = 0
                    target = TAKE_PROFIT_EXT if early_trig else TAKE_PROFIT
                    logger.info(
                        f"[S4 보유] [{code}] {name}  "
                        f"매수:{entry_price:,}  고점:{peak_gain:+.2%}  "
                        f"목표:{target:+.0%}  보유:{days_held}일"
                    )
                    continue

                # 시장가 매도 실행 (현재가는 PnL 로깅용)
                try:
                    current = self.market.get_quote(code).price
                except Exception:
                    current = entry_price
                self._sell_market(code, name, quantity, entry_price, current, reason, entry_date)

    # ── 매수 처리 (저녁 배치 entry_pending 실행) ─────────────────────
    def _process_entries(self) -> None:
        entry_pending = get_entry_pending()
        today_str     = datetime.now(KST).strftime("%Y-%m-%d")

        # 당일 저녁 배치가 결정한 후보만 유효 (배치 누락 시 stale 후보 방지)
        entry_pending = [e for e in entry_pending if e.get("date") == today_str]

        if not entry_pending:
            logger.info("[S4] 매수 대기 종목 없음 (전날 저녁 후보 없음)")
            return

        # 시장 추세 재확인 (안전장치)
        sepa_data      = load_sepa_data()
        market_uptrend = sepa_data.get("market_uptrend", False)
        if not market_uptrend:
            logger.info("[S4] 시장 하락장 — 저녁 결정 취소")
            set_entry_pending([])
            return

        # 슬롯 계산
        s2_n, s3_n, s4_n, s5_n, manual_n = count_shared()
        total_shared = s2_n + s3_n + s4_n + s5_n + manual_n

        try:
            bal      = self.account.get_balance()
            base_cap = ma_store.get_base_capital()
            extra    = ma_store.extra_slots(base_cap, bal.total_eval) if base_cap else 0
        except Exception as e:
            logger.error(f"[S4] 잔고 조회 실패: {e}")
            set_entry_pending([])
            return

        max_shared = S2_S3_S4_BASE + extra
        slots_free = max_shared - total_shared

        if slots_free <= 0:
            logger.info(
                f"[S4] 공유슬롯 만석 ({total_shared}/{max_shared}, "
                f"S2:{s2_n} S3:{s3_n} S4:{s4_n}) — 매수 보류"
            )
            set_entry_pending([])
            return

        per_slot = int(bal.total_eval * SLOT_RATIO)
        if per_slot > bal.cash:
            per_slot = bal.cash
        if per_slot < 10_000:
            logger.info(f"[S4] 가용 예산 부족 (슬롯당 {per_slot:,}원) — 매수 건너뜀")
            set_entry_pending([])
            return

        s4_positions = load_positions()
        candidates   = [e for e in entry_pending if e["code"] not in s4_positions]

        logger.info(
            f"[S4] 매수 대기 {len(candidates)}종목  "
            f"잔여슬롯 {slots_free}개  슬롯당 예산 {per_slot:,}원 (총자산 20%)"
        )

        bought = 0
        for entry in candidates[:slots_free]:
            code = entry["code"]
            name = entry.get("name", code)
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
                    f"금액:{price*qty:,}원  RS={entry.get('rs_score',0):.0f}  "
                    f"pivot={entry.get('pivot',0):,}"
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
                        f"RS={entry.get('rs_score',0):.0f}  "
                        f"pivot={entry.get('pivot',0):,}  "
                        f"tight={entry.get('tight_range_pct',0):.1f}%"
                    )

            except Exception as e:
                logger.error(f"[S4] [{code}] {name} 매수 실패: {e}")

        set_entry_pending([])  # 처리 완료 후 초기화

        if bought == 0:
            logger.info("[S4] 실제 매수 없음")

    # ── 시장가 매도 ───────────────────────────────────────────────────
    def _sell_market(
        self, code: str, name: str, quantity: int,
        entry_price: int, current_price: int, reason: str, entry_date: str = "",
    ) -> None:
        pnl     = (current_price - entry_price) * quantity
        pnl_pct = (current_price - entry_price) / entry_price

        try:
            if not self.is_paper:
                resp = self.order.sell_market(code, quantity)
                logger.info(f"[S4] 매도 주문 응답: {resp}")
            else:
                logger.info("[S4] 모의투자 — 주문 생략")

            remove_position(code, entry_date)

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
