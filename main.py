"""
옥동자 자동매매 시스템 진입점

Usage:
    python main.py --mode morning          # 옥동자 오전 수급 전략 (기본)
    python main.py --mode balance          # 잔고 조회
    python main.py --mode market --code 005930   # 종목 시세 조회
    python main.py --mode check-watchlist  # 80개 종목 API 연결 테스트
    python main.py --mode auto             # 구형 스케줄러 전략 (참고용)
    python main.py --run-once              # 단일 사이클 테스트 (구형)
"""
import argparse
import sys
import time

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from kis.factory import KIS
from data.watchlist import WATCHLIST as OKDONGJA_WATCHLIST
from data.holidays import is_market_holiday, is_trading_day


def run_morning_strategy(kis: KIS, notifier: Notifier) -> None:
    """옥동자 오전 수급 전략 실행"""
    from strategies.morning_surge import MorningSurgeStrategy
    strategy = MorningSurgeStrategy(
        market=kis.market,
        order=kis.order,
        account=kis.account,
        notifier=notifier,
        is_paper=kis.is_paper,
    )
    strategy.run()


def run_balance(kis: KIS) -> None:
    """잔고 조회"""
    from engine.portfolio import Portfolio
    portfolio = Portfolio(kis.account)
    portfolio.refresh()
    print(portfolio.summary())


def run_market(kis: KIS, code: str) -> None:
    """종목 현재가 조회"""
    from data.watchlist import CODE_MAP
    quote = kis.market.get_quote(code)
    info  = CODE_MAP.get(code, {})
    name  = info.get("name", quote.name or code)
    sect  = info.get("sector", "-")
    print(
        f"[{quote.code}] {name} ({sect})\n"
        f"현재가: {quote.price:,}원  등락: {quote.change_rate:+.2f}%\n"
        f"시가: {quote.open:,}  고가: {quote.high:,}  저가: {quote.low:,}\n"
        f"거래량: {quote.volume:,}"
    )


def run_check_watchlist(kis: KIS) -> None:
    """80개 종목 API 연결 및 데이터 로딩 테스트"""
    logger.info(f"=== 종목 데이터 로딩 테스트 ({len(OKDONGJA_WATCHLIST)}개 종목) ===")
    ok_list, fail_list = [], []

    for i, stock in enumerate(OKDONGJA_WATCHLIST, 1):
        code = stock["code"]
        name = stock["name"]
        try:
            quote = kis.market.get_quote(code)
            inv   = kis.market.get_investor_trend(code)

            fgn  = int(str(inv.get("frgn_ntby_tr_pbmn", "0") or "0").replace(",", "") or 0)
            orgn = int(str(inv.get("orgn_ntby_tr_pbmn", "0") or "0").replace(",", "") or 0)

            logger.info(
                f"[{i:02d}/{len(OKDONGJA_WATCHLIST)}] ✓ [{code}] {name:12s}  "
                f"현재가:{quote.price:>8,}원  외국인:{fgn:>12,}  기관:{orgn:>12,}"
            )
            ok_list.append(code)
        except Exception as e:
            logger.warning(f"[{i:02d}/{len(OKDONGJA_WATCHLIST)}] ✗ [{code}] {name} — {e}")
            fail_list.append(code)
        time.sleep(0.15)  # rate limit

    print("\n" + "="*60)
    print(f"테스트 완료: 성공 {len(ok_list)}개 / 실패 {len(fail_list)}개")
    if fail_list:
        print(f"실패 종목: {fail_list}")
    if is_market_holiday():
        print("※ 현재 장 마감 시간 — 수급(투자자) 데이터는 0으로 표시될 수 있음 (정상)")
    print("="*60)


def main():
    parser = argparse.ArgumentParser(description="옥동자 KIS 자동매매 시스템")
    parser.add_argument(
        "--mode",
        choices=["morning", "balance", "market", "check-watchlist", "auto"],
        default="morning",
        help="실행 모드 (기본: morning)"
    )
    parser.add_argument("--code",      help="종목코드 (--mode market 전용)")
    parser.add_argument("--run-once",  action="store_true", help="단일 사이클 (구형 auto 전용)")
    parser.add_argument("--cycle",     type=int, default=5)
    args = parser.parse_args()

    settings = get_settings()
    setup_logger(settings.log_level)

    mode_tag = "모의투자" if settings.kis_is_paper_trading else "실전투자"
    logger.info(f"=== 옥동자 자동매매 시스템 [{mode_tag}] mode={args.mode} ===")

    kis      = KIS(settings)
    notifier = Notifier.from_settings(settings)

    # ── 모드 분기 ─────────────────────────────────────────────────
    if args.mode == "morning":
        run_morning_strategy(kis, notifier)

    elif args.mode == "balance":
        run_balance(kis)

    elif args.mode == "market":
        if not args.code:
            print("--code 옵션으로 종목코드를 입력하세요.  예: --mode market --code 005930")
            sys.exit(1)
        run_market(kis, args.code)

    elif args.mode == "check-watchlist":
        run_check_watchlist(kis)

    elif args.mode == "auto":
        # 구형 멀티전략 스케줄러 (참고용)
        from engine.trader import Trader
        from engine.risk import RiskConfig
        from engine.scheduler import TradingScheduler
        from strategies.moving_average import MovingAverageCrossStrategy
        from strategies.rsi import RSIStrategy

        codes = [s["code"] for s in OKDONGJA_WATCHLIST]
        trader = Trader(
            kis=kis,
            strategies=[
                MovingAverageCrossStrategy(watchlist=codes),
                RSIStrategy(watchlist=codes),
            ],
            risk_config=RiskConfig(),
            notifier=notifier,
        )
        trader.initialize()

        if args.run_once:
            trader.run_cycle()
            return

        scheduler = TradingScheduler(cycle_minutes=args.cycle)
        scheduler.setup(
            on_cycle=trader.run_cycle,
            on_market_open=trader.initialize,
            on_market_close=trader.end_of_day,
        )
        scheduler.run_forever()


if __name__ == "__main__":
    main()
