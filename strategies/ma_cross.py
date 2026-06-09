"""
옥동자 전략 2호 — 이동평균선 정배열 중장기 전략

[매수 조건 - 완전 정배열]
  조건 1: 5 > 21 > 62 > 248 > 744 완전 정배열이 전날 처음 달성된 종목
  조건 2: 62, 248, 744 이평선 모두 20일 이상 연속 상승 추세
  조건 3: 전일 양봉 (종가 > 시가)

[매수 조건 - 부분 정배열 (데이터 250~749일 종목, 신생 ETF 등)]
  조건 1: 5 > 21 > 62 > 248 부분 정배열이 전날 처음 달성된 종목
  조건 2: 62, 248 이평선 모두 20일 이상 연속 상승 추세
  조건 3: 전일 양봉 (종가 > 시가)

[매도 조건]
  ma21이 ma62 아래로 이동한 다음날 아침 시장가 매도

[포지션 관리]
  총계좌의 20%씩, 최대 4포지션 동시 보유
  보유 종목이 다시 매수 신호 발생 시 → 평단가 통합 추가매수 (슬롯 소비 없음)
  슬롯 만석 + 신규 신호 → 텔레그램 알림

[실행 타이밍]
  08:55 시작 → 09:00 정규장 개장 대기 → 개별종목·ETF 모두 시장가 주문
"""
from datetime import datetime
import time
from typing import Optional

import pytz
from loguru import logger

import data.ma_store as ma_store
from data.holidays import is_market_holiday
from kis.market import KISMarket
from kis.order import KISOrder, OrderType
from kis.account import KISAccount
from utils.notifier import Notifier

KST                = pytz.timezone("Asia/Seoul")
MAX_POSITIONS      = 4
SLOT_RATIO         = 0.20
S2_STOP_LOSS       = 0.07   # 손절 -7%
S2_TAKE_PROFIT     = 0.20   # 익절 기본 +20%
S2_TAKE_PROFIT_EXT = 0.25   # 익절 확장 +25% (21일 이내 +15% 달성 시)


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

        # 휴장일 체크
        if is_market_holiday():
            msg = f"[MA전략] {today} 증시 휴장일 — 전략 미실행"
            logger.info(msg)
            self.notifier.notify(msg)
            return

        # 지연 실행 감지: 09:20 이후면 시장가 주문 시간대 초과
        _now = datetime.now(KST)
        if _now.hour > 9 or (_now.hour == 9 and _now.minute >= 20):
            msg = (
                f"[MA전략] 실행 지연 감지 — {_now.strftime('%H:%M')} KST 시작\n"
                f"09:00 시장가 주문 불가, 오늘 건너뜀"
            )
            logger.warning(msg)
            self.notifier.notify(msg)
            return

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
            balance = self.account.get_balance()
        except Exception as e:
            msg = f"[MA전략] 잔고 조회 실패 — 전략 미실행\n오류: {e}"
            logger.error(msg)
            self.notifier.notify(msg)
            return

        # 슬롯 확장: 수익률이 20% 증가할 때마다 S2 슬롯 1개 추가
        base_cap      = ma_store.get_base_capital()
        extra         = ma_store.extra_slots(base_cap, balance.total_eval) if base_cap else 0
        max_positions = MAX_POSITIONS + extra
        slot_budget   = int(balance.total_eval * SLOT_RATIO)

        # 포지션 로드 + KIS 실잔고 대조
        positions = ma_store.get_positions()
        positions = self._reconcile(positions, balance)

        profit_info = ""
        if base_cap and extra > 0:
            profit_r    = (balance.total_eval - base_cap) / base_cap * 100
            profit_info = f"  수익률:{profit_r:+.1f}% → 슬롯 +{extra}개 확장"
        logger.info(
            f"[MA전략] 총자산:{balance.total_eval:,}원  슬롯예산(20%):{slot_budget:,}원  "
            f"보유:{len(positions)}/{max_positions}{profit_info}"
        )

        # 시작 알림 (MA 데이터 신선도 포함)
        try:
            updated_date = datetime.strptime(updated, "%Y-%m-%d").date()
            days_old = (datetime.now(KST).date() - updated_date).days
            stale_note = f"\n⚠ MA 데이터 {days_old}일 경과 — 손절/매도 신호 부정확할 수 있음" if days_old > 1 else ""
        except Exception:
            stale_note = ""
        self.notifier.notify(
            f"[MA전략] 시작 [{mode}] {today}\n"
            f"MA 기준일: {updated} ({len(stocks)}종목){stale_note}\n"
            f"총자산: {balance.total_eval:,}원  보유: {len(positions)}/{max_positions}슬롯"
        )

        _sold   = 0
        _bought = 0
        avail_cash = balance.cash

        # 매수 후보 선정 (슬롯 체크 포함)
        all_candidates = self._find_candidates(stocks, positions)
        slots_full     = len(positions) >= max_positions
        if slots_full:
            new_cands = [c for c in all_candidates if c["code"] not in positions]
            if new_cands:
                self._notify_full(new_cands, max_positions)
            candidates = [c for c in all_candidates if c["code"] in positions]
        else:
            candidates = all_candidates

        # ── 오늘 할 일 없으면 조기 종료 (Actions 분 절약) ────────────────
        any_stop_loss  = any(p.get("stop_loss_pending") for p in positions.values())
        any_sell_sig   = any(
            stocks.get(c, {}).get("ma21_below_ma62") and stocks.get(c, {}).get("ma62_declining_5d")
            for c in positions
        )
        if not any_stop_loss and not any_sell_sig and not candidates:
            msg = "[MA전략] 오늘 매수·매도 신호 없음 — 조기 종료"
            logger.info(msg)
            self.notifier.notify(msg)
            return

        # ── 09:00 정규장 개장 대기 ────────────────────────────────────────
        _now_dt  = datetime.now(KST)
        _open_dt = _now_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        if _now_dt < _open_dt:
            wait_sec = (_open_dt - _now_dt).total_seconds()
            logger.info(f"[MA전략] 09:00 정규장 개장 대기 ({int(wait_sec//60)}분 {int(wait_sec%60)}초)")
            time.sleep(wait_sec)

        # ── 손절 매도 ────────────────────────────────────────────────────
        for code in list(positions):
            if positions[code].get("stop_loss_pending"):
                pos  = positions[code]
                logger.info(
                    f"[MA전략 손절매도] [{code}] {pos['name']} "
                    f"— 매수가 대비 -{S2_STOP_LOSS:.0%} 이하 손절 플래그"
                )
                self._sell(code, pos, reason=f"손절 매수가대비 -{S2_STOP_LOSS:.0%} 이하")
                del positions[code]
                ma_store.remove_position(code)
                _sold += 1

        # ── 익절 매도 ────────────────────────────────────────────────────
        for code in list(positions):
            if positions[code].get("take_profit_pending"):
                pos       = positions[code]
                early_trig = pos.get("early_gain_triggered", False)
                target    = S2_TAKE_PROFIT_EXT if early_trig else S2_TAKE_PROFIT
                ext_note  = " (조기확장목표)" if early_trig else ""
                logger.info(
                    f"[MA전략 익절매도] [{code}] {pos['name']} "
                    f"— +{target:.0%}{ext_note} 익절 플래그"
                )
                self._sell(code, pos, reason=f"익절 +{target:.0%}{ext_note}")
                del positions[code]
                ma_store.remove_position(code)
                _sold += 1

        # ── 데드크로스 매도 ──────────────────────────────────────────────
        for code in list(positions):
            s = stocks.get(code, {})
            if s.get("ma21_below_ma62") and s.get("ma62_declining_5d"):
                logger.info(
                    f"[MA전략 매도신호] [{code}] {positions[code]['name']} "
                    f"— ma21({s.get('ma21',0):,.0f}) < ma62({s.get('ma62',0):,.0f})  "
                    f"& ma62 5일 하락추세"
                )
                self._sell(code, positions[code], reason="ma21<ma62 & ma62 5일 하락추세")
                del positions[code]
                ma_store.remove_position(code)
                _sold += 1

        # ── 매수 ─────────────────────────────────────────────────────────
        if not candidates:
            logger.info("[MA전략] 매수 신호 없음")
        else:
            avail_cash, _bought = self._do_buy(
                candidates, positions, avail_cash, slot_budget, _bought
            )

        logger.info(f"[MA전략] 완료")
        self.notifier.notify(f"[MA전략] 완료 — 매도:{_sold}건  매수:{_bought}건")

    def _do_buy(
        self,
        cands: list,
        positions: dict,
        avail_cash: int,
        slot_budget: int,
        bought: int,
    ):
        """후보 리스트에서 최대 1종목 시장가 매수. (avail_cash, bought) 반환."""
        for cand in cands[:1]:
            code     = cand["code"]
            name     = cand["name"]
            price    = cand.get("close", 0)
            is_rebuy = code in positions

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

            action_label = "추가매수" if is_rebuy else "매수"
            tag = "(전체정배열)" if cand.get("has_ma744") else "(MA248정배열)"
            logger.info(
                f"[MA전략 {action_label}] [{code}] {name} — "
                f"{tag} 정배열첫날 / 62↑:{cand['ma62_uptrend']} "
                f"248↑:{cand['ma248_uptrend']}"
                + (f" 744↑:{cand['ma744_uptrend']}" if cand.get("has_ma744") else "")
                + f" 양봉몸통:{cand.get('candle_body_ratio', 0):.2%}"
            )
            result = self.order.buy(code, qty, 0, OrderType.MARKET)
            if result.success:
                bought += 1
                entry_date = datetime.now(KST).strftime("%Y-%m-%d")
                if is_rebuy:
                    prev       = positions[code]
                    prev_qty   = prev.get("quantity", 0)
                    prev_price = prev.get("entry_price", 0)
                    new_qty    = prev_qty + qty
                    new_avg    = int(round((prev_price * prev_qty + price * qty) / new_qty)) if new_qty > 0 else price
                    ma_store.add_position(code, name, entry_date, price, qty)
                    avail_cash -= price * qty
                    self.notifier.notify(
                        f"[MA전략 추가매수] [{code}] {name} {tag}\n"
                        f"추가: {qty:,}주 @ {price:,}원\n"
                        f"통합: {new_qty:,}주  평단 {prev_price:,}→{new_avg:,}원\n"
                        f"62일추세↑:{cand['ma62_uptrend']}  "
                        f"248일추세↑:{cand['ma248_uptrend']}"
                        + (f"  744일추세↑:{cand['ma744_uptrend']}" if cand.get("has_ma744") else "")
                    )
                else:
                    ma_store.add_position(code, name, entry_date, price, qty)
                    avail_cash -= price * qty
                    self.notifier.notify(
                        f"[MA전략 매수] [{code}] {name} {tag}\n"
                        f"수량:{qty:,}주  기준가:{price:,}원\n"
                        f"62일추세↑:{cand['ma62_uptrend']}  "
                        f"248일추세↑:{cand['ma248_uptrend']}"
                        + (f"  744일추세↑:{cand['ma744_uptrend']}" if cand.get("has_ma744") else "")
                    )
            else:
                logger.error(f"[MA전략 {action_label} 실패] {code}: {result.message}")
                self.notifier.notify(f"[MA전략 {action_label} 실패] [{code}] {name}: {result.message}")

        return avail_cash, bought

    # ═══════════════════════════════════════════════════════════════════
    def _reconcile(self, json_positions: dict, balance) -> dict:
        """JSON 포지션과 KIS 실잔고 대조 — KIS에 없는 포지션 제거, 종목명 동기화"""
        kis_map = {p.code: p for p in balance.positions}
        name_changed = False
        for code in list(json_positions):
            if code not in kis_map:
                logger.warning(f"[MA전략] [{code}] KIS 잔고에 없음 → JSON 포지션 제거")
                ma_store.remove_position(code)
                del json_positions[code]
            else:
                real_name = kis_map[code].name
                if json_positions[code].get("name") != real_name:
                    logger.info(
                        f"[MA전략] [{code}] 종목명 수정: "
                        f"{json_positions[code].get('name')} → {real_name}"
                    )
                    json_positions[code]["name"] = real_name
                    name_changed = True
        if name_changed:
            raw = ma_store.load()
            for code, pos in json_positions.items():
                if code in raw.get("positions", {}):
                    raw["positions"][code]["name"] = pos["name"]
            ma_store.save(raw)
        return json_positions

    def _sell(self, code: str, pos: dict, reason: str = "ma21 < ma62 데드크로스") -> None:
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
                f"사유: {reason}"
            )
            logger.info(msg)
            self.notifier.notify(msg)
        else:
            err = f"[MA전략 매도 실패] [{code}] {pos['name']}: {result.message}"
            logger.error(err)
            self.notifier.notify(err)

    def _find_candidates(self, stocks: dict, positions: dict) -> list:
        """매수 조건 모두 충족 종목 리스트 — 전일 양봉 몸통 크기 내림차순.
        보유 종목도 후보에 포함됨 (재매수 시 평단가 통합 처리).
        """
        result = []
        for code, s in stocks.items():
            if self._is_buy_signal(s):
                result.append({"code": code, **s})
        result.sort(key=lambda x: x.get("candle_body_ratio", 0), reverse=True)
        return result

    def _is_buy_signal(self, s: dict) -> bool:
        """매수 조건 동시 충족 여부 (완전 정배열 or 부분 정배열)"""
        if not s.get("prev_bullish_candle"):
            return False
        if not s.get("ma62_uptrend") or not s.get("ma248_uptrend"):
            return False
        if s.get("has_ma744"):
            # 완전 정배열: MA5>MA21>MA62>MA248>MA744 처음 달성 + 744 상승추세
            return (
                s.get("fully_aligned")
                and not s.get("prev_fully_aligned")
                and s.get("ma744_uptrend")
            )
        else:
            # 부분 정배열: MA5>MA21>MA62>MA248 처음 달성 (신생 ETF 등)
            return bool(s.get("partial_aligned") and not s.get("prev_partial_aligned"))

    def _notify_full(self, candidates: list, max_pos: int = MAX_POSITIONS) -> None:
        msg = (
            f"[MA전략] 슬롯 만석({max_pos}/{max_pos}) — 신규 신호 {len(candidates)}종목\n"
            + "\n".join(
                f"  [{c['code']}] {c.get('name', c['code'])}"
                for c in candidates[:5]
            )
            + "\n※ 수익률 20% 달성 시 슬롯 자동 확장"
        )
        logger.warning(msg)
        self.notifier.notify(msg)
