"""
옥동자 자동매매 시스템 진입점

Usage:
    python main.py --mode morning          # 옥동자 오전 수급 전략 (기본)
    python main.py --mode ma-morning       # MA 이평선 전략 (S2)
    python main.py --mode ma-batch         # MA 배치 업데이트
    python main.py --mode canslim-morning  # CANSLIM 전략 (S3)
    python main.py --mode canslim-batch    # CANSLIM 일일 스크리닝 배치
    python main.py --mode dart-batch       # DART 재무 데이터 배치 (분기 1회)
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


def run_ma_morning(kis: KIS, notifier: Notifier) -> None:
    """MA 이평선 중장기 전략 (Strategy 2) 실행"""
    from strategies.ma_cross import MACrossStrategy
    strategy = MACrossStrategy(
        market=kis.market,
        order=kis.order,
        account=kis.account,
        notifier=notifier,
        is_paper=kis.is_paper,
    )
    strategy.run()


def run_ma_batch(kis: KIS, notifier: Notifier) -> None:
    """MA 이평선 배치 업데이트 + 손절 체크 + 잔고 현황 알림"""
    from batch.update_ma import run_batch
    run_batch(kis.market, account=kis.account, notifier=notifier)


def run_canslim_morning(kis: KIS, notifier: Notifier) -> None:
    """CANSLIM 전략3 실행 (09:00 시장가 매수/매도)"""
    from strategies.canslim import CANSLIMStrategy
    strategy = CANSLIMStrategy(
        market=kis.market,
        order=kis.order,
        account=kis.account,
        notifier=notifier,
        is_paper=kis.is_paper,
    )
    strategy.run()


def run_canslim_batch(kis: KIS, notifier: Notifier, force: bool = False) -> None:
    """CANSLIM 일일 스크리닝 배치"""
    from batch.update_canslim import run_batch
    run_batch(kis.market, notifier=notifier, force=force)


def run_sepa_morning(kis: KIS, notifier: Notifier) -> None:
    """SEPA 전략4 실행 (09:00 시장가 매수/매도)"""
    from strategies.sepa import SEPAStrategy
    SEPAStrategy(
        market=kis.market,
        order=kis.order,
        account=kis.account,
        notifier=notifier,
        is_paper=kis.is_paper,
    ).run()


def run_sepa_batch(kis: KIS, notifier: Notifier, force: bool = False) -> None:
    """SEPA 트렌드 템플릿 + VCP 스크리닝 배치"""
    from batch.update_sepa import run_batch
    run_batch(kis.market, notifier=notifier, force=force)


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


def run_debug_investor(kis: KIS, code: str) -> None:
    """프로그램 매매 API raw 응답 전체 출력 — 실제 필드명 확인용"""
    import json
    from data.watchlist import CODE_MAP
    name = CODE_MAP.get(code, {}).get("name", code)
    print(f"\n[디버그] [{code}] {name} 현재가시세(FHKST01010100) raw 응답\n")
    print("※ pgtr_ntby_tr_pbmn 필드 존재 여부 및 값 확인이 목적\n")

    try:
        data = kis.market.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        output = data.get("output", {})

        print("=== 응답 최상위 keys ===")
        print(list(data.keys()))
        print(f"\n=== output keys ({len(output)}개) ===")
        print(list(output.keys()))

        # 프로그램 매매 관련 필드만 따로 출력
        pgtr_fields = {k: v for k, v in output.items() if "pgtr" in k or "program" in k.lower()}
        print(f"\n=== 프로그램 매매 관련 필드 ===")
        if pgtr_fields:
            print(json.dumps(pgtr_fields, ensure_ascii=False, indent=2))
        else:
            print("  pgtr 관련 필드 없음 — output 전체 출력:")
            print(json.dumps(output, ensure_ascii=False, indent=2))

        # get_program_trade 결과도 출력
        print("\n=== get_program_trade() 결과 ===")
        pt = kis.market.get_program_trade(code)
        pt_display = {k: v for k, v in pt.items() if k != "_raw"}
        print(json.dumps(pt_display, ensure_ascii=False, indent=2))

    except Exception as e:
        logger.error(f"디버그 호출 실패: {e}")
        import traceback
        traceback.print_exc()


def run_check_watchlist(kis: KIS) -> None:
    """프로그램 매매 API 연결 및 데이터 로딩 테스트
    - get_program_trade() 1회 호출 (실제 전략과 동일)
    - Throttle 적용 (초당 9건 이하)
    - 장 마감 시간엔 pgtr 값 0이 정상
    """
    from utils.throttler import RateThrottler
    throttler = RateThrottler(max_per_second=9)

    n = len(OKDONGJA_WATCHLIST)
    logger.info(f"=== 프로그램 매매 API 연결 테스트 ({n}개 종목, 초당 9건 throttle) ===")
    ok_list, fail_list = [], []

    for i, stock in enumerate(OKDONGJA_WATCHLIST, 1):
        code = stock["code"]
        name = stock["name"]
        sect = stock["sector"]
        try:
            with throttler:
                pt = kis.market.get_program_trade(code)
            pgtr_qty = pt.get("pgtr_ntby_qty", 0)
            pgtr_est = pt.get("pgtr_est_amt", 0)
            logger.info(
                f"[{i:02d}/{n}] ✓ [{code}] {name:12s} ({sect:10s})  "
                f"순매수수량:{pgtr_qty:>10,}주  추정금액:{pgtr_est:>14,}원  현재가:{pt.get('price',0):,}"
            )
            ok_list.append(code)
        except Exception as e:
            logger.warning(f"[{i:02d}/{n}] ✗ [{code}] {name} — {e}")
            fail_list.append(code)

    print("\n" + "="*60)
    print(f"테스트 완료: 성공 {len(ok_list)}개 / 실패 {len(fail_list)}개")
    if fail_list:
        print(f"실패 종목: {fail_list}")
    print("※ 장 마감 시간엔 pgtr=0 정상 / 장중엔 실제 프로그램 매매 금액 표시")
    print("="*60)


def main():
    parser = argparse.ArgumentParser(description="옥동자 KIS 자동매매 시스템")
    parser.add_argument(
        "--mode",
        choices=["morning", "ma-morning", "ma-batch",
                 "canslim-morning", "canslim-batch",
                 "sepa-morning", "sepa-batch",
                 "balance", "market", "check-watchlist", "debug-investor", "auto"],
        default="morning",
        help="실행 모드 (기본: morning)"
    )
    parser.add_argument("--code",      help="종목코드 (--mode market 전용)")
    parser.add_argument("--run-once",  action="store_true", help="단일 사이클 (구형 auto 전용)")
    parser.add_argument("--cycle",     type=int, default=5)
    parser.add_argument("--force",     action="store_true", help="휴장일·중복실행 체크 무시 (canslim-batch·sepa-batch 전용)")
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

    elif args.mode == "ma-morning":
        run_ma_morning(kis, notifier)

    elif args.mode == "ma-batch":
        run_ma_batch(kis, notifier)

    elif args.mode == "canslim-morning":
        run_canslim_morning(kis, notifier)

    elif args.mode == "canslim-batch":
        run_canslim_batch(kis, notifier, force=args.force)

    elif args.mode == "sepa-morning":
        run_sepa_morning(kis, notifier)

    elif args.mode == "sepa-batch":
        run_sepa_batch(kis, notifier, force=args.force)

    elif args.mode == "balance":
        run_balance(kis)

    elif args.mode == "market":
        if not args.code:
            print("--code 옵션으로 종목코드를 입력하세요.  예: --mode market --code 005930")
            sys.exit(1)
        run_market(kis, args.code)

    elif args.mode == "check-watchlist":
        run_check_watchlist(kis)

    elif args.mode == "debug-investor":
        code = args.code or "005930"
        run_debug_investor(kis, code)

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
