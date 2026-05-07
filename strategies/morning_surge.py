"""
옥동자 전략 1호 — 실시간 프로그램 매매 기반 오전 단기 매매

[수급 포착 방법]
KIS FHKST01010100(현재가시세) output의 pgtr_ntby_qty 필드:
  → 장중 실시간 누적 프로그램 순매수 수량 (실제 API 제공 필드)
  → pgtr_ntby_tr_pbmn(금액)은 API output에 없으므로 qty × price 로 추정
  → 종목 간 비교는 pgtr_est_amt(추정 금액) 기준으로 순위 결정

[Phase 흐름]
Phase 1  09:00~09:09  종목 프로그램 매매 데이터 수집 (초당 9건 이하 throttle)
Phase 2  09:09~09:10  최강 섹터 → 섹터 내 최강 종목 선정
Phase 3  09:10        전량 시장가 매수
Phase 4  09:10~09:55  포지션 모니터링
  · 익절: 매수가 대비 +7% 즉시 매도
  · 손절: 장중 고점 대비 -3% 즉시 매도
  · 타임컷: 11:00 무조건 전량 매도
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz
from loguru import logger

from data.watchlist import WATCHLIST, CODE_MAP
from data.holidays import is_market_holiday
import data.ma_store as ma_store
from kis.market import KISMarket
from kis.order import KISOrder, OrderType
from kis.account import KISAccount
from utils.notifier import Notifier
from utils.throttler import RateThrottler

KST = pytz.timezone("Asia/Seoul")

# ── 전략 상수 ──────────────────────────────────────────────────────────
STOP_LOSS_RATIO   = 0.03     # 고점 대비 손절 3%
TAKE_PROFIT_RATIO = 0.07     # 매수가 대비 익절 7%
FOREIGN_WEIGHT    = 1.0      # 프로그램매매 단일 지표이므로 가중치 1.0
MONITOR_INTERVAL  = 20       # 포지션 모니터링 주기(초)
COLLECT_INTERVAL  = 60       # 수급 수집 반복 주기(초)
CASH_USE_RATIO    = 0.99     # 주문 가능 금액 사용 비율


# ── 데이터 클래스 ──────────────────────────────────────────────────────
@dataclass
class ProgramSnapshot:
    code: str
    name: str
    sector: str
    pgtr_net: int = 0      # 프로그램 순매수 추정금액 = qty × price (원, 음수=순매도)
    pgtr_qty: int = 0      # 프로그램 순매수 수량 (API 실제 제공 필드)
    price: int = 0
    change_rate: float = 0.0


@dataclass
class SectorScore:
    sector: str
    total_pgtr_qty: int = 0


# ── 메인 전략 클래스 ───────────────────────────────────────────────────
class MorningSurgeStrategy:
    """옥동자 프로그램 매매 기반 오전 전략"""

    name = "MorningSurge_ProgramTrade"

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

        # 초당 9건 제한 (KIS TPS 10건 이하 안전 마진)
        self._throttler = RateThrottler(max_per_second=9)

        # 수집 버퍼: code → 스냅샷 리스트
        self._buffer: Dict[str, List[ProgramSnapshot]] = defaultdict(list)

        # 포지션 정보
        self._code:          Optional[str] = None
        self._name:          Optional[str] = None
        self._entry_price:   int = 0
        self._quantity:      int = 0
        self._intraday_high: int = 0

    # ═══════════════════════════════════════════════════════════════════
    #  PUBLIC: 전략 실행 진입점
    # ═══════════════════════════════════════════════════════════════════
    def run(self) -> None:
        today = datetime.now(KST).strftime("%Y-%m-%d (%a)")
        mode  = "모의투자" if self.is_paper else "실전투자"
        logger.info("══════════════════════════════════════════")
        logger.info(f" 옥동자 전략 시작 [{mode}] {today}")
        logger.info(f" 수급 기준: 실시간 프로그램 매매 순매수 (pgtr_ntby_qty × 현재가 추정)")
        logger.info(f" TPS 제한: 초당 9건 이하 (Throttle 적용)")
        logger.info("══════════════════════════════════════════")

        # 휴장일 체크
        if is_market_holiday():
            msg = f"[옥동자] {today} 증시 휴장일 — 전략 미실행"
            logger.info(msg)
            self.notifier.notify(msg)
            return

        # GitHub Actions 스케줄 지연 감지
        # 수집 윈도우(08:00~09:09)가 이미 지났으면 오늘은 건너뜀
        _now = _now_kst()
        if _now.hour > 9 or (_now.hour == 9 and _now.minute >= 5):
            msg = (
                f"[옥동자] 실행 지연 감지 — {_now.strftime('%H:%M')} KST 시작\n"
                f"수집 윈도우(08:00~09:09) 이미 종료, 오늘 전략 건너뜀\n"
                f"원인: GitHub Actions 스케줄러 지연"
            )
            logger.warning(msg)
            self.notifier.notify(msg)
            return

        self.notifier.notify(f"[옥동자] 전략 시작 {today} [{mode}]")

        # Phase 1: 08:00(NXT) ~ 09:09 프로그램 매매 수집
        self._wait_until(8, 0, "NXT 시장 시작")
        self._collect_phase()

        # Phase 2: 09:09 분석, 09:10 매수
        self._wait_until(9, 10, "매수 실행")
        self._buy_phase()

        if not self._code:
            msg = "[옥동자] 매수 대상 없음 — 오늘 전략 종료"
            logger.warning(msg)
            self.notifier.notify(msg)
            return

        # Phase 3: 09:10 ~ 09:55 모니터링
        self._monitor_phase()

        logger.info("══════════════════════════════════════════")
        logger.info(" 옥동자 전략 종료")
        logger.info("══════════════════════════════════════════")

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 1: 프로그램 매매 데이터 수집
    # ═══════════════════════════════════════════════════════════════════
    def _collect_phase(self) -> None:
        logger.info("[Phase 1] 프로그램 매매 수집 시작 (08:00 NXT~09:09, 1분 간격)")
        end_dt = _kst_time(9, 9)
        pass_no = 0

        while _now_kst() < end_dt:
            pass_no += 1
            remaining = (end_dt - _now_kst()).total_seconds()
            logger.info(f"[수집] {pass_no}차 — 잔여 {remaining:.0f}초 / 대상 {len(WATCHLIST)}종목")
            self._fetch_all_stocks()

            remaining = (end_dt - _now_kst()).total_seconds()
            if remaining < 5:
                break
            time.sleep(min(COLLECT_INTERVAL, remaining - 2))

        logger.info(f"[Phase 1] 수집 완료 ({pass_no}회, {len(self._buffer)}종목)")

    def _fetch_all_stocks(self) -> None:
        """Throttle 적용하여 전 종목 프로그램 매매 1회 수집"""
        ok, fail, zero = 0, 0, 0

        for stock in WATCHLIST:
            code   = stock["code"]
            name   = stock["name"]
            sector = stock["sector"]
            try:
                with self._throttler:   # 초당 9건 이하 보장
                    pt = self.market.get_program_trade(code)

                pgtr_qty = pt.get("pgtr_ntby_qty", 0)
                pgtr_net = pt.get("pgtr_est_amt", 0)   # qty × price 추정금액
                snap = ProgramSnapshot(
                    code=code, name=name, sector=sector,
                    pgtr_net=pgtr_net,
                    pgtr_qty=pgtr_qty,
                    price=pt.get("price", 0),
                    change_rate=pt.get("change_rate", 0.0),
                )
                self._buffer[code].append(snap)

                if pgtr_qty == 0:
                    zero += 1
                    logger.debug(f"  [{code}] {name:10s} 프로그램매매=0 (장초반 또는 미집계)")
                else:
                    logger.debug(
                        f"  [{code}] {name:10s} "
                        f"순매수수량:{pgtr_qty:>10,}주  추정금액:{pgtr_net:>13,}원  현재가:{snap.price:,}"
                    )
                ok += 1

            except Exception as e:
                logger.warning(f"  [{code}] {name} 조회 실패: {e}")
                fail += 1

        logger.info(
            f"  → 성공:{ok} / 실패:{fail} / 프로그램0:{zero} "
            f"(0은 장 초반 또는 해당일 프로그램 없음)"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 2: 분석 → 매수
    # ═══════════════════════════════════════════════════════════════════
    def _buy_phase(self) -> None:
        logger.info("[Phase 2] 프로그램 매매 분석 및 매수")
        result = self._analyze()
        if not result:
            logger.warning("[Phase 2] 유효한 프로그램 매매 신호 없음 — 매수 포기")
            return

        best_sector, target = result

        # 현재가 재조회 (최신값)
        try:
            with self._throttler:
                pt = self.market.get_program_trade(target.code)
            current_price = pt.get("price", 0) or target.price
        except Exception as e:
            logger.error(f"현재가 조회 실패 [{target.code}]: {e}")
            return

        if current_price <= 0:
            logger.error(f"현재가 0 — 매수 취소 [{target.code}]")
            return

        # 주문 가능 금액 — 슬롯 확장 반영 (수익률 20%마다 슬롯 1개 추가)
        try:
            balance  = self.account.get_balance()
            cash     = balance.cash
            base_cap = ma_store.get_base_capital()
            extra    = ma_store.extra_slots(base_cap, balance.total_eval) if base_cap else 0
            s1_slots = 1 + extra
            # 슬롯 수만큼 투자 비중 확대 (1슬롯=20%, 2슬롯=40%, ...)
            slot_budget = int(balance.total_eval * 0.20 * s1_slots)
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return

        budget = min(slot_budget, cash)
        if budget < current_price:
            logger.warning(f"예산 부족: {budget:,}원 < {current_price:,}원")
            return

        quantity = int(budget * CASH_USE_RATIO / current_price)
        if quantity <= 0:
            return

        slot_info = f"  (S1 슬롯 {s1_slots}개 × 20%)" if extra > 0 else ""
        msg = (
            f"[옥동자 매수]\n"
            f"섹터: {best_sector}\n"
            f"종목: [{target.code}] {target.name}\n"
            f"현재가: {current_price:,}원 × {quantity:,}주\n"
            f"투자금: {current_price * quantity:,}원{slot_info}\n"
            f"프로그램순매수: {target.pgtr_qty:,}주 (추정금액 {target.pgtr_net:,}원)"
        )
        logger.info(msg)

        result_order = self.order.buy(target.code, quantity, 0, OrderType.MARKET)

        if result_order.success:
            self._code          = target.code
            self._name          = target.name
            self._entry_price   = current_price
            self._quantity      = quantity
            self._intraday_high = current_price
            self.notifier.notify(msg)
            logger.info(f"매수 완료 — 주문번호: {result_order.order_no}")
        else:
            logger.error(f"매수 실패: {result_order.message}")
            self.notifier.notify(
                f"[옥동자 매수 실패] {target.code} {result_order.message}"
            )

    def _analyze(self) -> Optional[Tuple[str, ProgramSnapshot]]:
        """수집 버퍼 → (최강 섹터, 최강 종목) 반환
        프로그램 순매수가 모두 0이면 None 반환 (신호 없음)
        """
        if not self._buffer:
            logger.warning("수집 데이터 없음")
            return None

        # 종목별 평균 프로그램 순매수
        avg_list: List[ProgramSnapshot] = []
        for code, snaps in self._buffer.items():
            if not snaps:
                continue
            info     = CODE_MAP.get(code, {})
            avg_pgtr = int(sum(s.pgtr_net for s in snaps) / len(snaps))
            avg_qty  = int(sum(s.pgtr_qty for s in snaps) / len(snaps))
            last     = snaps[-1]
            avg_list.append(ProgramSnapshot(
                code=code, name=info.get("name", code),
                sector=info.get("sector", "기타"),
                pgtr_net=avg_pgtr,
                pgtr_qty=avg_qty,
                price=last.price, change_rate=last.change_rate,
            ))

        # 프로그램 순매수 수량 > 0인 종목이 하나도 없으면 신호 없음
        positive = [s for s in avg_list if s.pgtr_qty > 0]
        if not positive:
            logger.warning(
                "전 종목 프로그램 순매수 수량 ≤ 0 — 오늘 프로그램 매수 신호 없음\n"
                "원인: 장 초반 미집계 / 프로그램 매도 우위"
            )
            return None

        # 섹터별 순매수량 합산
        sector_map: Dict[str, SectorScore] = {}
        for s in avg_list:
            if s.sector not in sector_map:
                sector_map[s.sector] = SectorScore(sector=s.sector)
            sector_map[s.sector].total_pgtr_qty += s.pgtr_qty

        best_sec = max(sector_map.values(), key=lambda x: x.total_pgtr_qty)

        # 섹터 순위 로그
        logger.info("── 섹터 프로그램 매매 순위 (순매수량) ────────")
        for rank, sec in enumerate(
            sorted(sector_map.values(), key=lambda x: x.total_pgtr_qty, reverse=True)[:8], 1
        ):
            marker = "★" if sec.sector == best_sec.sector else " "
            logger.info(
                f" {marker}{rank}. {sec.sector:12s}  "
                f"프로그램순매수량:{sec.total_pgtr_qty:>12,}주"
            )

        # 최강 섹터 내 순매수량 최강 종목
        sector_stocks = [s for s in avg_list if s.sector == best_sec.sector]
        best_stock    = max(sector_stocks, key=lambda x: x.pgtr_qty)

        logger.info("── 종목 프로그램 매매 순위 (최강 섹터 내) ───")
        for rank, st in enumerate(
            sorted(sector_stocks, key=lambda x: x.pgtr_qty, reverse=True), 1
        ):
            marker = "★" if st.code == best_stock.code else " "
            logger.info(
                f" {marker}{rank}. [{st.code}] {st.name:12s}  "
                f"순매수량:{st.pgtr_qty:>10,}주  추정금액:{st.pgtr_net:>12,}원  현재가:{st.price:,}"
            )

        logger.info(
            f"[분석 결과] 섹터={best_sec.sector}  "
            f"종목=[{best_stock.code}] {best_stock.name}  "
            f"프로그램순매수량={best_stock.pgtr_qty:,}주 / 추정금액={best_stock.pgtr_net:,}원"
        )
        return best_sec.sector, best_stock

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 3: 포지션 모니터링
    # ═══════════════════════════════════════════════════════════════════
    def _monitor_phase(self) -> None:
        time_cut   = _kst_time(11, 0)
        stop_price = self._entry_price * (1 - STOP_LOSS_RATIO)
        tp_price   = self._entry_price * (1 + TAKE_PROFIT_RATIO)

        logger.info(
            f"[Phase 3] 모니터링 시작\n"
            f"  종목: [{self._code}] {self._name}\n"
            f"  매수가:  {self._entry_price:,}원\n"
            f"  익절가:  {tp_price:,.0f}원 (+{TAKE_PROFIT_RATIO*100:.0f}%)\n"
            f"  손절가:  {stop_price:,.0f}원 (고점대비 -{STOP_LOSS_RATIO*100:.0f}%)\n"
            f"  타임컷:  11:00"
        )

        while True:
            now = _now_kst()

            # ── 타임컷 ───────────────────────────────────────
            if now >= time_cut:
                logger.info("[타임컷] 11:00 — 전량 시장가 매도")
                self._sell_all("타임컷 11:00")
                return

            # ── 현재가 조회 (throttle 적용) ───────────────────
            try:
                with self._throttler:
                    quote = self.market.get_quote(self._code)
                cur = quote.price
            except Exception as e:
                logger.warning(f"현재가 조회 실패: {e}")
                time.sleep(MONITOR_INTERVAL)
                continue

            if cur <= 0:
                time.sleep(MONITOR_INTERVAL)
                continue

            # ── 고점 갱신 → 트레일링 손절가 ─────────────────
            if cur > self._intraday_high:
                self._intraday_high = cur
                stop_price = self._intraday_high * (1 - STOP_LOSS_RATIO)
                logger.info(
                    f"  고점 갱신: {self._intraday_high:,}원  "
                    f"손절가 → {stop_price:,.0f}원"
                )

            pnl_rate  = (cur - self._entry_price) / self._entry_price * 100
            remaining = int((time_cut - now).total_seconds() // 60)
            logger.info(
                f"  [{self._code}] 현재:{cur:,}  손익:{pnl_rate:+.2f}%  "
                f"고점:{self._intraday_high:,}  손절:{stop_price:,.0f}  "
                f"잔여:{remaining}분"
            )

            # ── 익절 ─────────────────────────────────────────
            if cur >= tp_price:
                self._sell_all(f"익절 +{pnl_rate:.2f}%")
                return

            # ── 손절 ─────────────────────────────────────────
            if cur <= stop_price:
                drop = (cur / self._intraday_high - 1) * 100
                self._sell_all(f"손절 고점대비 {drop:.2f}%")
                return

            time.sleep(MONITOR_INTERVAL)

    # ═══════════════════════════════════════════════════════════════════
    #  공통: 전량 매도
    # ═══════════════════════════════════════════════════════════════════
    def _sell_all(self, reason: str) -> None:
        if not self._code:
            return
        logger.info(f"[매도] {self._name} 전량 매도 — {reason}")

        qty = self._quantity
        try:
            balance = self.account.get_balance()
            pos = next((p for p in balance.positions if p.code == self._code), None)
            if pos:
                qty = pos.quantity
        except Exception:
            pass

        if qty <= 0:
            logger.warning("매도 수량 0 — 이미 청산된 것으로 간주")
            self._code = None
            return

        result = self.order.sell(self._code, qty, 0, OrderType.MARKET)

        if result.success:
            try:
                with self._throttler:
                    cur = self.market.get_quote(self._code).price
                pnl      = (cur - self._entry_price) * qty
                pnl_rate = (cur - self._entry_price) / self._entry_price * 100
                price_str = f"{cur:,}원"
            except Exception:
                pnl, pnl_rate, price_str = 0, 0.0, "조회실패"

            msg = (
                f"[옥동자 매도] [{self._code}] {self._name}\n"
                f"사유: {reason}\n"
                f"매수가:{self._entry_price:,} → 현재:{price_str}\n"
                f"손익: {pnl:+,}원 ({pnl_rate:+.2f}%)"
            )
            logger.info(msg)
            self.notifier.notify(msg)
        else:
            err = f"[옥동자 매도 실패] {self._code} {result.message}"
            logger.error(err)
            self.notifier.notify(err)

        self._code = None

    # ═══════════════════════════════════════════════════════════════════
    #  유틸
    # ═══════════════════════════════════════════════════════════════════
    def _wait_until(self, hour: int, minute: int, label: str = "") -> None:
        target = _kst_time(hour, minute)
        while True:
            diff = (target - _now_kst()).total_seconds()
            if diff <= 0:
                return
            wait = min(30, diff)
            logger.info(f"{label} 대기 중 ({hour:02d}:{minute:02d}까지 {diff:.0f}초)")
            time.sleep(wait)


# ── 헬퍼 ──────────────────────────────────────────────────────────────
def _now_kst() -> datetime:
    return datetime.now(KST)


def _kst_time(hour: int, minute: int, second: int = 0) -> datetime:
    now = datetime.now(KST)
    return now.replace(hour=hour, minute=minute, second=second, microsecond=0)
