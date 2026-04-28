"""
옥동자 전략 1호 — 오전 수급 분석 단기 매매

Phase 1  09:00~09:10  외국인+기관 순매수 수급 수집 (1분 간격, 전 80종목)
Phase 2  09:10        최강 섹터 → 최강 종목 선정 후 전량 시장가 매수
Phase 3  09:10~09:55  포지션 모니터링
  · 익절: 매수가 대비 +5% 즉시 매도
  · 손절: 장중 고점 대비 -2.36%(피보나치) 즉시 매도
  · 타임컷: 09:55 무조건 전량 매도
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
from kis.market import KISMarket
from kis.order import KISOrder, OrderType
from kis.account import KISAccount
from utils.notifier import Notifier

KST = pytz.timezone("Asia/Seoul")

# ── 전략 상수 ──────────────────────────────────────────────────────────
STOP_LOSS_RATIO    = 0.0236   # 고점 대비 손절 비율 (피보나치 2.36%)
TAKE_PROFIT_RATIO  = 0.05     # 매수가 대비 익절 비율 5%
FOREIGN_WEIGHT     = 1.5      # 외국인 순매수 가중치 (기관 대비)
MONITOR_INTERVAL   = 20       # 포지션 모니터링 주기(초)
API_CALL_DELAY     = 0.12     # 종목 간 API 호출 딜레이(초) — rate limit 대응
CASH_USE_RATIO     = 0.99     # 주문 가능 금액 사용 비율 (수수료 여유)


# ── 데이터 클래스 ──────────────────────────────────────────────────────
@dataclass
class StockSnapshot:
    code: str
    name: str
    sector: str
    foreign_net: int = 0       # 외국인 순매수 금액 (원)
    institution_net: int = 0   # 기관 순매수 금액 (원)

    @property
    def weighted_score(self) -> float:
        """외국인 가중 합산 스코어"""
        return self.foreign_net * FOREIGN_WEIGHT + self.institution_net


@dataclass
class SectorScore:
    sector: str
    total_foreign: int = 0
    total_institution: int = 0

    @property
    def weighted_score(self) -> float:
        return self.total_foreign * FOREIGN_WEIGHT + self.total_institution


# ── 메인 전략 클래스 ───────────────────────────────────────────────────
class MorningSurgeStrategy:
    """옥동자 오전 수급 전략"""

    name = "MorningSurge"

    def __init__(
        self,
        market: KISMarket,
        order: KISOrder,
        account: KISAccount,
        notifier: Notifier,
        is_paper: bool = True,
    ):
        self.market    = market
        self.order     = order
        self.account   = account
        self.notifier  = notifier
        self.is_paper  = is_paper

        # 수집 버퍼: code → 스냅샷 리스트
        self._buffer: Dict[str, List[StockSnapshot]] = defaultdict(list)

        # 포지션 정보
        self._code:           Optional[str] = None
        self._name:           Optional[str] = None
        self._entry_price:    int = 0
        self._quantity:       int = 0
        self._intraday_high:  int = 0

    # ═══════════════════════════════════════════════════════════════════
    #  PUBLIC: 전략 실행 진입점
    # ═══════════════════════════════════════════════════════════════════
    def run(self) -> None:
        today = datetime.now(KST).strftime("%Y-%m-%d (%a)")
        mode  = "모의투자" if self.is_paper else "실전투자"
        logger.info(f"══════════════════════════════════════════")
        logger.info(f" 옥동자 전략 시작 [{mode}] {today}")
        logger.info(f"══════════════════════════════════════════")

        # 휴장일 체크
        if is_market_holiday():
            msg = f"[옥동자] {today} 증시 휴장일 — 전략 미실행"
            logger.info(msg)
            self.notifier.notify(msg)
            return

        self.notifier.notify(f"[옥동자] 전략 시작 {today} [{mode}]")

        # Phase 1: 09:00 ~ 09:10 수급 수집
        self._wait_until(9, 0, "장 시작")
        self._collect_phase()

        # Phase 2: 09:10 매수
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
    #  PHASE 1: 수급 데이터 수집
    # ═══════════════════════════════════════════════════════════════════
    def _collect_phase(self) -> None:
        logger.info("[Phase 1] 수급 수집 시작 (09:00 ~ 09:10, 1분 간격)")
        end_dt = _kst_time(9, 10)
        pass_no = 0

        while _now_kst() < end_dt:
            pass_no += 1
            remaining = (end_dt - _now_kst()).total_seconds()
            logger.info(f"[수급수집] {pass_no}차 수집 — 잔여시간 {remaining:.0f}초")
            self._fetch_all_stocks()

            # 다음 수집까지 대기 (남은 시간이 5초 미만이면 루프 종료)
            remaining = (end_dt - _now_kst()).total_seconds()
            if remaining < 5:
                break
            time.sleep(min(60, remaining - 2))

        logger.info(f"[Phase 1] 수급 수집 완료 ({pass_no}회, {len(self._buffer)}종목)")

    def _fetch_all_stocks(self) -> None:
        """80개 종목 수급 1회 수집"""
        success, fail = 0, 0
        for stock in WATCHLIST:
            code   = stock["code"]
            name   = stock["name"]
            sector = stock["sector"]
            try:
                raw   = self.market.get_investor_trend(code)
                fgn   = _safe_int(raw.get("frgn_ntby_tr_pbmn"))
                orgn  = _safe_int(raw.get("orgn_ntby_tr_pbmn"))
                self._buffer[code].append(
                    StockSnapshot(code=code, name=name, sector=sector,
                                  foreign_net=fgn, institution_net=orgn)
                )
                logger.debug(f"  [{code}] {name:10s} 외국인:{fgn:>12,} 기관:{orgn:>12,}")
                success += 1
            except Exception as e:
                logger.warning(f"  [{code}] {name} 수급 조회 실패: {e}")
                fail += 1
            time.sleep(API_CALL_DELAY)

        logger.info(f"  → 수집 결과: 성공 {success}건 / 실패 {fail}건")

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 2: 수급 분석 → 매수
    # ═══════════════════════════════════════════════════════════════════
    def _buy_phase(self) -> None:
        logger.info("[Phase 2] 수급 분석 및 매수 실행")

        result = self._analyze()
        if not result:
            logger.warning("[Phase 2] 분석 실패 — 매수 포기")
            return

        best_sector, target = result

        # 현재가 조회
        try:
            quote = self.market.get_quote(target.code)
            current_price = quote.price
        except Exception as e:
            logger.error(f"현재가 조회 실패 [{target.code}]: {e}")
            return

        if current_price <= 0:
            logger.error(f"현재가 0 — 매수 취소 [{target.code}]")
            return

        # 주문 가능 금액 조회
        try:
            balance  = self.account.get_balance()
            cash     = balance.cash
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return

        if cash < current_price:
            logger.warning(f"현금 부족: {cash:,}원 < {current_price:,}원")
            return

        quantity = int(cash * CASH_USE_RATIO / current_price)
        if quantity <= 0:
            logger.warning(f"매수 수량 0 (현금:{cash:,} 현재가:{current_price:,})")
            return

        msg = (
            f"[옥동자 매수]\n"
            f"섹터: {best_sector}\n"
            f"종목: [{target.code}] {target.name}\n"
            f"현재가: {current_price:,}원 × {quantity:,}주\n"
            f"투자금액: {current_price * quantity:,}원\n"
            f"외국인순매수: {target.foreign_net:,}원  기관: {target.institution_net:,}원"
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
            self.notifier.notify(f"[옥동자 매수 실패] {target.code} {result_order.message}")

    def _analyze(self) -> Optional[Tuple[str, StockSnapshot]]:
        """수집 버퍼 → (최강 섹터, 최강 종목) 반환"""
        if not self._buffer:
            logger.warning("수집 데이터 없음")
            return None

        # 종목별 평균 스코어
        avg_stocks: List[StockSnapshot] = []
        for code, snaps in self._buffer.items():
            if not snaps:
                continue
            info   = CODE_MAP.get(code, {})
            avg_fgn  = int(sum(s.foreign_net  for s in snaps) / len(snaps))
            avg_orgn = int(sum(s.institution_net for s in snaps) / len(snaps))
            avg_stocks.append(StockSnapshot(
                code=code, name=info.get("name", code),
                sector=info.get("sector", "기타"),
                foreign_net=avg_fgn, institution_net=avg_orgn,
            ))

        if not avg_stocks:
            return None

        # 섹터별 합산
        sector_map: Dict[str, SectorScore] = {}
        for s in avg_stocks:
            if s.sector not in sector_map:
                sector_map[s.sector] = SectorScore(sector=s.sector)
            sector_map[s.sector].total_foreign     += s.foreign_net
            sector_map[s.sector].total_institution += s.institution_net

        # 최강 섹터 선정
        best_sec = max(sector_map.values(), key=lambda x: x.weighted_score)

        # 섹터 순위 로그
        logger.info("── 섹터 수급 순위 ──────────────────────────")
        for rank, sec in enumerate(
            sorted(sector_map.values(), key=lambda x: x.weighted_score, reverse=True)[:8], 1
        ):
            marker = "★" if sec.sector == best_sec.sector else " "
            logger.info(
                f" {marker}{rank}. {sec.sector:12s} "
                f"외국인:{sec.total_foreign:>13,}  기관:{sec.total_institution:>13,}  "
                f"가중:{sec.weighted_score:>14,.0f}"
            )

        # 해당 섹터 내 최강 종목 선정
        sector_stocks = [s for s in avg_stocks if s.sector == best_sec.sector]
        best_stock = max(sector_stocks, key=lambda x: x.weighted_score)

        logger.info("── 종목 수급 순위 (최강 섹터 내) ───────────")
        for rank, st in enumerate(
            sorted(sector_stocks, key=lambda x: x.weighted_score, reverse=True), 1
        ):
            marker = "★" if st.code == best_stock.code else " "
            logger.info(
                f" {marker}{rank}. [{st.code}] {st.name:12s} "
                f"외국인:{st.foreign_net:>12,}  기관:{st.institution_net:>12,}"
            )

        logger.info(
            f"[분석 결과] 섹터={best_sec.sector}  "
            f"종목=[{best_stock.code}] {best_stock.name}  "
            f"가중스코어={best_stock.weighted_score:,.0f}"
        )
        return best_sec.sector, best_stock

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 3: 포지션 모니터링
    # ═══════════════════════════════════════════════════════════════════
    def _monitor_phase(self) -> None:
        time_cut    = _kst_time(9, 55)
        stop_price  = self._entry_price * (1 - STOP_LOSS_RATIO)   # 최초 손절가
        tp_price    = self._entry_price * (1 + TAKE_PROFIT_RATIO)  # 익절가

        logger.info(
            f"[Phase 3] 모니터링 시작\n"
            f"  종목: [{self._code}] {self._name}\n"
            f"  매수가:  {self._entry_price:,}원\n"
            f"  익절가:  {tp_price:,.0f}원 (+{TAKE_PROFIT_RATIO*100:.1f}%)\n"
            f"  손절가:  {stop_price:,.0f}원 (고점대비 -{STOP_LOSS_RATIO*100}%)\n"
            f"  타임컷:  09:55"
        )

        while True:
            now = _now_kst()

            # ── 타임컷 ───────────────────────────────────────
            if now >= time_cut:
                logger.info("[타임컷] 09:55 — 전량 시장가 매도")
                self._sell_all("타임컷 09:55")
                return

            # ── 현재가 조회 ───────────────────────────────────
            try:
                quote = self.market.get_quote(self._code)
                cur   = quote.price
            except Exception as e:
                logger.warning(f"현재가 조회 실패: {e} — 재시도")
                time.sleep(MONITOR_INTERVAL)
                continue

            if cur <= 0:
                time.sleep(MONITOR_INTERVAL)
                continue

            # ── 고점 갱신 → 트레일링 손절가 재계산 ──────────
            if cur > self._intraday_high:
                self._intraday_high = cur
                stop_price = self._intraday_high * (1 - STOP_LOSS_RATIO)
                logger.info(
                    f"  고점 갱신: {self._intraday_high:,}원  "
                    f"손절가 → {stop_price:,.0f}원"
                )

            pnl_rate = (cur - self._entry_price) / self._entry_price * 100
            logger.info(
                f"  [{self._code}] 현재:{cur:,}원  "
                f"손익:{pnl_rate:+.2f}%  "
                f"고점:{self._intraday_high:,}  손절가:{stop_price:,.0f}  "
                f"남은시간:{int((time_cut-now).total_seconds()//60)}분"
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

        # 실제 보유 수량 재확인 (체결 수량 차이 대비)
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
            # 현재가로 근사 손익 계산
            try:
                cur      = self.market.get_quote(self._code).price
                pnl      = (cur - self._entry_price) * qty
                pnl_rate = (cur - self._entry_price) / self._entry_price * 100
                price_str = f"{cur:,}원"
            except Exception:
                pnl, pnl_rate, price_str = 0, 0.0, "조회실패"

            msg = (
                f"[옥동자 매도] [{self._code}] {self._name}\n"
                f"사유: {reason}\n"
                f"매수가:{self._entry_price:,}  현재가:{price_str}\n"
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
        """목표 시각까지 대기 (이미 지났으면 즉시 반환)"""
        target = _kst_time(hour, minute)
        while True:
            diff = (target - _now_kst()).total_seconds()
            if diff <= 0:
                return
            wait = min(30, diff)
            logger.info(f"{label} 대기 중 ({hour:02d}:{minute:02d}까지 {diff:.0f}초)")
            time.sleep(wait)


# ── 헬퍼 함수 ──────────────────────────────────────────────────────────
def _now_kst() -> datetime:
    return datetime.now(KST)


def _kst_time(hour: int, minute: int, second: int = 0) -> datetime:
    now = datetime.now(KST)
    return now.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _safe_int(val) -> int:
    """문자열/None/빈값을 안전하게 int 변환"""
    try:
        return int(str(val).replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0
