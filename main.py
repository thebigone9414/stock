"""
주식 자동매매 시스템 진입점
Usage:
    python main.py                    # 자동 스케줄 실행
    python main.py --run-once         # 단일 사이클 실행 (CI 테스트용)
    python main.py --mode balance     # 잔고 조회만
    python main.py --mode market CODE # 종목 현재가 조회
"""
import argparse
import sys

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from kis.factory import KIS
from engine.trader import Trader
from engine.risk import RiskConfig
from engine.scheduler import TradingScheduler

from strategies.moving_average import MovingAverageCrossStrategy
from strategies.momentum import MomentumStrategy
from strategies.rsi import RSIStrategy
from strategies.volume_breakout import VolumeBreakoutStrategy


# ── 관심종목 설정 ──────────────────────────────────────────────────────
WATCHLIST = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "035720",  # 카카오
    "005380",  # 현대차
    "207940",  # 삼성바이오로직스
    "006400",  # 삼성SDI
    "051910",  # LG화학
    "003670",  # 포스코퓨처엠
    "000270",  # 기아
    "105560",  # KB금융
    "055550",  # 신한지주
    "096770",  # SK이노베이션
    "010130",  # 고려아연
    "028260",  # 삼성물산
]


def build_strategies() -> list:
    """전략 목록 구성 - 여기서 원하는 전략 활성화/비활성화"""
    return [
        MovingAverageCrossStrategy(watchlist=WATCHLIST, short_period=5, long_period=20),
        RSIStrategy(watchlist=WATCHLIST, period=14, oversold=30, overbought=70),
        MomentumStrategy(watchlist=WATCHLIST, lookback_days=20, top_n=3, hold_days=10),
        VolumeBreakoutStrategy(watchlist=WATCHLIST, volume_multiplier=3.0, price_change_min=2.0),
    ]


def build_risk_config() -> RiskConfig:
    """리스크 설정"""
    return RiskConfig(
        max_position_ratio=0.15,   # 종목당 최대 15%
        max_positions=8,           # 최대 8종목
        daily_loss_limit_ratio=0.05,  # 일일 손실 5% 한도
        stop_loss_ratio=0.07,      # 손절 7%
        take_profit_ratio=0.15,    # 익절 15%
    )


def main():
    parser = argparse.ArgumentParser(description="KIS 자동매매 시스템")
    parser.add_argument("--run-once", action="store_true", help="단일 사이클 실행 후 종료")
    parser.add_argument("--mode", choices=["auto", "balance", "market"], default="auto")
    parser.add_argument("--code", help="종목코드 (--mode market 사용 시)")
    parser.add_argument("--cycle", type=int, default=5, help="매매 사이클 주기(분), 기본=5")
    args = parser.parse_args()

    settings = get_settings()
    setup_logger(settings.log_level)

    mode_tag = "모의투자" if settings.kis_is_paper_trading else "실전투자"
    logger.info(f"=== KIS 자동매매 시스템 시작 [{mode_tag}] ===")

    kis = KIS(settings)
    notifier = Notifier.from_settings(settings)

    # ── 잔고 조회 모드 ─────────────────────────────────────────
    if args.mode == "balance":
        from engine.portfolio import Portfolio
        portfolio = Portfolio(kis.account)
        balance = portfolio.refresh()
        print(portfolio.summary())
        return

    # ── 종목 시세 조회 모드 ────────────────────────────────────
    if args.mode == "market":
        code = args.code
        if not code:
            print("--code 옵션으로 종목코드를 입력하세요. 예: --mode market --code 005930")
            sys.exit(1)
        quote = kis.market.get_quote(code)
        print(
            f"[{quote.code}] {quote.name}\n"
            f"현재가: {quote.price:,}원 ({quote.change_rate:+.2f}%)\n"
            f"시가: {quote.open:,} 고가: {quote.high:,} 저가: {quote.low:,}\n"
            f"거래량: {quote.volume:,}"
        )
        return

    # ── 자동매매 모드 ──────────────────────────────────────────
    trader = Trader(
        kis=kis,
        strategies=build_strategies(),
        risk_config=build_risk_config(),
        notifier=notifier,
    )
    trader.initialize()

    if args.run_once:
        logger.info("단일 사이클 실행")
        trader.run_cycle()
        logger.info("단일 사이클 완료")
        return

    # 스케줄러 기반 자동 실행
    scheduler = TradingScheduler(cycle_minutes=args.cycle)
    scheduler.setup(
        on_cycle=trader.run_cycle,
        on_market_open=trader.initialize,
        on_market_close=trader.end_of_day,
    )
    scheduler.run_forever()


if __name__ == "__main__":
    main()
