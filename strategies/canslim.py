"""
전략3: CANSLIM 자동매매 (William O'Neil)

매수 조건: canslim_data.json 의 all_pass=True 종목 (N·S·L·I·M 5개 조건 모두 충족)
매도 조건:
  - 손절: 매수가 대비 -7% (O'Neil 기본 룰)
  - 트레일링스탑: 고점 대비 -10% (고점수익률 +10% 이상일 때만 활성화)
  - 익절(기본): +20%
  - 익절(확장): 진입 후 21 캘린더일 이내 +15% 달성 시 → 목표 +25%로 상향
  - 타임스탑: 8주(56 캘린더일) 경과 후 목표 미달성 시 청산

포지션 관리:
  - S2+S3 공유 슬롯: 기본 4개 (S1=1 고정, 총 5개)
  - 자산 증가(수익+추가입금) 기준 자산의 20%마다 슬롯 1개 추가
  - 신규 매수 예산: 총자산의 20% (슬롯당)
  - 시장 하락장(M=False): 신규 매수 보류
  - 손절 후 90일간 동일 종목 재진입 금지

실행 시각: 09:00 정규장 개장 시 시장가 주문
"""
import time
from datetime import datetime, date, timedelta

import pytz
from loguru import logger

import data.ma_store as ma_store
from data.shared_slots import count_shared
from data.canslim_store import (
    get_buy_candidates,
    load_positions,
    add_position,
    remove_position,
    update_position_peak,
    load_data as load_canslim_data,
    load_ca_screened,
    get_stop_blacklist,
    add_to_stop_blacklist,
    STOP_BLACKLIST_DAYS,
)

KST              = pytz.timezone("Asia/Seoul")
S2_S3_BASE_SLOTS = 4     # S2+S3 공유 슬롯 기본값 (총 5: S1=1 고정, S2+S3=4)
MAX_POSITIONS    = S2_S3_BASE_SLOTS   # 하위 호환 유지
STOP_LOSS        = 0.07   # -7%
TRAIL_STOP_PCT   = 0.10   # 고점 대비 -10% 트레일링 스탑
TRAIL_STOP_MIN   = 0.10   # 트레일링 스탑 활성화 최소 고점 수익률
TAKE_PROFIT      = 0.20   # +20%
TAKE_PROFIT_EXT  = 0.25   # +25% (조기 +15% 달성 시)
TIME_STOP_DAYS   = 56     # 8주


class CANSLIMStrategy:
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
            logger.info(f"[S3] 09:00 개장 대기 ({wait_sec:.0f}초)")
            time.sleep(wait_sec)

        # 지연 감지: 09:45 이후면 시장가 주문 시간대 초과
        _now = datetime.now(KST)
        if _now.hour > 9 or (_now.hour == 9 and _now.minute >= 45):
            logger.warning(f"[S3] 지연 실행 감지 ({_now.strftime('%H:%M')}) — 시장가 주문 불가, 종료")
            return

        logger.info(f"[S3 CANSLIM] 실행 시작 {_now.strftime('%Y-%m-%d %H:%M')}")

        try:
            self._process_exits()
        except Exception as e:
            logger.error(f"[S3] 매도 처리 중 오류: {e}")

        try:
            self._process_entries()
        except Exception as e:
            logger.error(f"[S3] 매수 처리 중 오류: {e}")

        logger.info("[S3 CANSLIM] 실행 완료")

    # ── 매도 처리 ─────────────────────────────────────────────────────
    def _process_exits(self) -> None:
        positions = load_positions()
        if not positions:
            logger.info("[S3] 보유 포지션 없음")
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
                quote       = self.market.get_quote(code)
                current     = quote.price
                gain        = (current - entry_price) / entry_price
                days_held   = (
                    datetime.strptime(today_str, "%Y-%m-%d")
                    - datetime.strptime(entry_date, "%Y-%m-%d")
                ).days
            except Exception as e:
                logger.warning(f"[S3] [{code}] {name} 현재가 조회 실패: {e}")
                continue

            # 고점 갱신
            update_position_peak(code, current, today_str)
            peak_price = max(pos.get("peak_price", entry_price), current)
            peak_gain  = (peak_price - entry_price) / entry_price

            # 목표가 결정
            target = TAKE_PROFIT_EXT if early_trig else TAKE_PROFIT

            # ── 손절 ───────────────────────────────────────────────
            if gain <= -STOP_LOSS:
                logger.warning(
                    f"[S3 손절] [{code}] {name}  "
                    f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  시장가 매도"
                )
                add_to_stop_blacklist(code, today_str)
                self._sell_market(code, name, quantity, entry_price, current, "손절(-7%)")
                continue

            # ── 익절 ───────────────────────────────────────────────
            if gain >= target:
                label = f"익절(+{target:.0%}" + (" 확장목표)" if early_trig else ")")
                logger.info(
                    f"[S3 익절] [{code}] {name}  "
                    f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  {label}"
                )
                self._sell_market(code, name, quantity, entry_price, current, label)
                continue

            # ── 트레일링스탑 ────────────────────────────────────────
            if peak_gain >= TRAIL_STOP_MIN and current < peak_price * (1 - TRAIL_STOP_PCT):
                logger.info(
                    f"[S3 트레일링스탑] [{code}] {name}  "
                    f"고점:{peak_price:,}(+{peak_gain:.2%}) → 현재:{current:,}({gain:+.2%})"
                )
                self._sell_market(
                    code, name, quantity, entry_price, current,
                    f"트레일링스탑(고점-{TRAIL_STOP_PCT:.0%})",
                )
                continue

            # ── 타임스탑 ───────────────────────────────────────────
            if days_held >= TIME_STOP_DAYS:
                logger.info(
                    f"[S3 타임스탑] [{code}] {name}  "
                    f"{days_held}일 경과 ({gain:+.2%}) — 청산"
                )
                self._sell_market(code, name, quantity, entry_price, current, f"타임스탑({days_held}일)")
                continue

            logger.info(
                f"[S3 보유] [{code}] {name}  "
                f"매수:{entry_price:,} → 현재:{current:,}  {gain:+.2%}  "
                f"목표:{target:+.0%}  보유:{days_held}일"
            )

    # ── 매수 처리 ─────────────────────────────────────────────────────
    def _process_entries(self) -> None:
        canslim_data    = load_canslim_data()
        market_uptrend  = canslim_data.get("market_uptrend", False)
        updated_at      = canslim_data.get("updated_at", "")
        today_str       = datetime.now(KST).strftime("%Y-%m-%d")

        if not market_uptrend:
            logger.info("[S3] 시장 하락장(M=False) — 신규 매수 보류")
            return

        # S2+S3+S4 공유 슬롯 계산
        s2_n, s3_n, s4_n = count_shared()
        total_shared = s2_n + s3_n + s4_n
        s3_positions = load_positions()

        # 슬롯 확장: 자산 증가(수익 + 추가 입금)마다 슬롯 추가
        try:
            bal      = self.account.get_balance()
            base_cap = ma_store.get_base_capital()
            extra    = ma_store.extra_slots(base_cap, bal.total_eval) if base_cap else 0
        except Exception as e:
            logger.error(f"[S3] 잔고 조회 실패: {e}")
            return

        max_shared = S2_S3_BASE_SLOTS + extra
        slots_free = max_shared - total_shared

        if slots_free <= 0:
            logger.info(
                f"[S3] 공유슬롯 만석 ({total_shared}/{max_shared}, "
                f"S2:{s2_n} S3:{s3_n} S4:{s4_n}) — 매수 보류"
            )
            return

        # 스크리닝 데이터가 오늘 것인지 확인 (오래된 데이터로 매수 방지)
        if updated_at < today_str:
            logger.warning(
                f"[S3] canslim_data가 오늘 업데이트 안됨 (업데이트:{updated_at}, 오늘:{today_str}) "
                f"— 신규 매수 보류"
            )
            return

        # A 사전 필터 로드 (DART 스크리닝 결과 — A 조건만 적용)
        # C 조건은 2025Q1 데이터 수집 후(8월 Q2 배치) C AND A 로 전환 예정
        ca_data     = load_ca_screened()
        ca_screened = ca_data.get("screened", [])
        ca_codes    = {s["code"] for s in ca_screened if s.get("A")}  # A 통과 종목만
        ca_updated  = ca_data.get("updated_at", "")
        use_ca      = bool(ca_codes)

        if use_ca:
            logger.info(
                f"[S3] A 필터 적용 — {len(ca_codes)}종목 (갱신:{ca_updated})"
                f" ※C 조건은 8월 Q2 배치 후 AND 조건으로 전환 예정"
            )
        else:
            logger.warning("[S3] A 스크리닝 데이터 없음 — A 필터 미적용 (전체 후보 사용)")

        positions  = s3_positions
        raw_cands  = [(code, info) for code, info in get_buy_candidates()
                      if code not in positions]

        # 손절 블랙리스트 필터 (90일 재진입 금지)
        blacklist    = get_stop_blacklist()
        cutoff_date  = (datetime.now(KST) - timedelta(days=STOP_BLACKLIST_DAYS)).strftime("%Y-%m-%d")
        bl_filtered  = [(code, info) for code, info in raw_cands
                        if blacklist.get(code, "0000-00-00") < cutoff_date]
        bl_skipped   = len(raw_cands) - len(bl_filtered)
        if bl_skipped:
            logger.info(f"[S3] 손절 블랙리스트 {bl_skipped}종목 제외 (90일 재진입 금지)")

        candidates = [(code, info) for code, info in bl_filtered
                      if not use_ca or code in ca_codes]

        if not candidates:
            if use_ca and raw_cands:
                logger.info(
                    f"[S3] 매수 후보 없음 — N·S·L·M 통과 {len(raw_cands)}종목이 "
                    f"A 필터 미통과 (연간 EPS CAGR +15% 미달)"
                )
            else:
                logger.info("[S3] 매수 후보 없음 (all_pass 종목 없음 또는 이미 보유)")
            return

        # 슬롯당 예산: 총자산의 20%
        cash     = bal.cash
        per_slot = int(bal.total_eval * 0.20)
        if per_slot > cash:
            per_slot = cash  # 현금 초과 방지

        if per_slot < 10_000:
            logger.info(f"[S3] 가용 예산 부족 (슬롯당 {per_slot:,}원) — 매수 건너뜀")
            return

        logger.info(
            f"[S3] 매수 후보 {len(candidates)}종목  "
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
                    logger.info(f"[S3] [{code}] {name}  주가({price:,}) > 슬롯예산 → 건너뜀")
                    continue

                ca_info = next((s for s in ca_screened if s["code"] == code), None)
                ca_tag  = ""
                if ca_info:
                    ca_tag = " C+A" if (ca_info.get("C") and ca_info.get("A")) else (" C" if ca_info.get("C") else " A")
                logger.info(
                    f"[S3 매수] [{code}] {name}  현재가:{price:,}  수량:{qty}주  "
                    f"금액:{price*qty:,}원  (score={info.get('score',0)}/5{ca_tag})"
                )

                if not self.is_paper:
                    resp = self.order.buy_market(code, qty)
                    logger.info(f"[S3] 매수 주문 응답: {resp}")
                else:
                    logger.info("[S3] 모의투자 — 주문 생략, 포지션만 기록")

                add_position(code, name, today_str, price, qty)
                bought += 1

                if self.notifier:
                    self.notifier.notify(
                        f"[S3 매수] [{code}] {name}\n"
                        f"현재가:{price:,}원  수량:{qty}주  금액:{price*qty:,}원\n"
                        f"CANSLIM score={info.get('score',0)}/5{ca_tag}"
                    )

            except Exception as e:
                logger.error(f"[S3] [{code}] {name} 매수 실패: {e}")

        if bought == 0:
            logger.info("[S3] 실제 매수 없음")

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
                logger.info(f"[S3] 매도 주문 응답: {resp}")
            else:
                logger.info("[S3] 모의투자 — 주문 생략")

            remove_position(code)

            msg = (
                f"[S3 매도] [{code}] {name}  {reason}\n"
                f"매수:{entry_price:,} → 현재:{current_price:,}  "
                f"{pnl_pct:+.2%} ({pnl:+,}원)"
            )
            logger.info(msg)
            if self.notifier:
                self.notifier.notify(msg)

        except Exception as e:
            logger.error(f"[S3] [{code}] {name} 매도 실패: {e}")
