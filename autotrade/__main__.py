#!/usr/bin/env python3
"""autotrade/main.py — CLI 디스패처

하루 일정 각 시각에 cron이 호출하는 진입점.

평일 타임라인:
  08:20  python -m autotrade --cmd reconcile      # 잔고 대사
  08:50  python -m autotrade --cmd sell           # 매도 집행
  08:55  python -m autotrade --cmd stop-register  # STOP 등록 (하이브리드)
  09:05  python -m autotrade --cmd stop-check     # 체크포인트 1
  10:00~15:15 (6회)  python -m autotrade --cmd stop-check   # 체크포인트 2~7
  15:15  python -m autotrade --cmd stop-check     # 체크포인트 8
  15:18  python -m autotrade --cmd buy            # 매수 집행
  15:35  python -m autotrade --cmd stop-confirm   # STOP 체결 확인
  20:10  python -m autotrade --cmd nightly        # 데이터 수집 + 판정 + 보고

디버그/검수:
  python -m autotrade --cmd judge --date YYYY-MM-DD   # 특정일 리플레이
  python -m autotrade --cmd report                     # 가장 최근 orders의 보고만 재생성
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger


def _run_reconcile() -> int:
    from autotrade import reconcile
    return reconcile.run()


def _run_sell() -> int:
    from autotrade import sell
    return sell.run()


def _run_buy() -> int:
    from autotrade import buy
    return buy.run()


def _run_stop(sub: str) -> int:
    from autotrade import stoploss
    if sub == "register":
        return stoploss.register()
    if sub == "check":
        return stoploss.check()
    if sub == "confirm":
        return stoploss.confirm()
    logger.error(f"[main] 알 수 없는 stop 서브: {sub}")
    return 1


def _run_judge(date: str | None = None) -> int:
    from autotrade import judge
    try:
        judge.run(replay_date=date)
        return 0
    except Exception as e:
        logger.exception(f"[main/judge] 실패: {e}")
        return 1


def _run_datafeed() -> int:
    from autotrade import datafeed
    return datafeed.run()


def _run_nightly(skip_datafeed: bool = False) -> int:
    """20:10 통합: datafeed → judge → report 전송.
    실패해도 다음 단계로 넘어가지 않음 (지시서 §9).
    """
    from autotrade import common, judge as J, report as R, state as S
    notifier = common.setup()

    if not common.is_trading_today():
        logger.info(f"[nightly] {common.today_str()} 휴장일 — 종료")
        return 0

    # 1) datafeed
    if not skip_datafeed:
        rc = _run_datafeed()
        if rc != 0:
            logger.error("[nightly] datafeed 실패 — judge/보고 생략")
            if notifier:
                notifier.notify(R.format_data_error("datafeed", "수집 실패로 판정 생략. 로그 확인 필요"))
            return rc

    # 2) judge
    try:
        orders = J.run()
    except Exception as e:
        logger.exception(f"[nightly] judge 실패: {e}")
        if notifier:
            notifier.notify(R.format_data_error("judge", str(e)))
        return 1

    # 3) report
    try:
        msg = R.format_daily_report(orders)
        logger.info("\n" + msg)
        if notifier:
            notifier.notify(msg)
    except Exception as e:
        logger.exception(f"[nightly] report 포맷/전송 실패: {e}")
        return 1

    logger.info("[nightly] 완료")
    return 0


def _run_report_only() -> int:
    """가장 최근 orders 로 보고만 재생성 (디버그·보고 재전송)."""
    from autotrade import common, state as S, report as R
    from pathlib import Path
    import json

    common.setup()
    order_dir = Path(__file__).parent / "orders"
    files = sorted(order_dir.glob("orders_*.json"))
    if not files:
        logger.error("[report] orders 파일 없음")
        return 1
    with open(files[-1], "r", encoding="utf-8") as f:
        orders = json.load(f)
    msg = R.format_daily_report(orders)
    print(msg)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="autotrade CLI dispatcher")
    p.add_argument(
        "--cmd",
        choices=[
            "reconcile", "sell", "buy",
            "stop-register", "stop-check", "stop-confirm",
            "datafeed", "judge", "nightly", "report",
        ],
        required=True,
    )
    p.add_argument("--date", help="리플레이 기준일 YYYY-MM-DD (judge 전용)")
    p.add_argument("--skip-datafeed", action="store_true", help="nightly 에서 datafeed 생략 (디버그용)")
    args = p.parse_args()

    cmd = args.cmd
    if cmd == "reconcile":
        return _run_reconcile()
    if cmd == "sell":
        return _run_sell()
    if cmd == "buy":
        return _run_buy()
    if cmd.startswith("stop-"):
        return _run_stop(cmd.replace("stop-", ""))
    if cmd == "datafeed":
        return _run_datafeed()
    if cmd == "judge":
        return _run_judge(date=args.date)
    if cmd == "nightly":
        return _run_nightly(skip_datafeed=args.skip_datafeed)
    if cmd == "report":
        return _run_report_only()

    logger.error(f"[main] 알 수 없는 cmd: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
