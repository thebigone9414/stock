#!/usr/bin/env python3
"""autotrade/main.py — CLI 디스패처 (v2, 2026-10-10)

v2 일정 (장중 손절 없음):
  08:20  python -m autotrade --cmd reconcile      # 잔고·장부 대사
  08:50  python -m autotrade --cmd sell           # 매도 집행 (A: 청산·손절, B: 62일선 이탈)
  15:21  python -m autotrade --cmd buy            # 매수 집행 (A: 신호, B: 월말 교체)
  20:10  python -m autotrade --cmd nightly        # datafeed + judge + report

디버그/검수:
  python -m autotrade --cmd judge --date YYYY-MM-DD   # 특정일 리플레이
  python -m autotrade --cmd report                     # 가장 최근 orders의 보고 재생성
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
    """20:10 통합: datafeed → judge → report."""
    from autotrade import common, judge as J, report as R
    notifier = common.setup()

    if not common.is_trading_today():
        logger.info(f"[nightly] {common.today_str()} 휴장일 — 종료")
        return 0

    if not skip_datafeed:
        rc = _run_datafeed()
        if rc != 0:
            logger.error("[nightly] datafeed 실패 — judge/보고 생략")
            if notifier:
                notifier.notify(R.format_data_error("datafeed", "수집 실패로 판정 생략"))
            return rc

    try:
        orders = J.run()
    except Exception as e:
        logger.exception(f"[nightly] judge 실패: {e}")
        if notifier:
            notifier.notify(R.format_data_error("judge", str(e)))
        return 1

    try:
        msg = R.format_daily_report(orders)
        logger.info("\n" + msg)
        if notifier:
            notifier.notify(msg)
    except Exception as e:
        logger.exception(f"[nightly] report 실패: {e}")
        return 1

    logger.info("[nightly] 완료")
    return 0


def _run_report_only() -> int:
    from autotrade import common, report as R
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
    p = argparse.ArgumentParser(description="autotrade CLI dispatcher (v2)")
    p.add_argument(
        "--cmd",
        choices=["reconcile", "sell", "buy", "datafeed", "judge", "nightly", "report"],
        required=True,
    )
    p.add_argument("--date", help="리플레이 기준일 YYYY-MM-DD (judge 전용)")
    p.add_argument("--skip-datafeed", action="store_true", help="nightly 에서 datafeed 생략 (디버그)")
    args = p.parse_args()

    cmd = args.cmd
    if cmd == "reconcile":  return _run_reconcile()
    if cmd == "sell":       return _run_sell()
    if cmd == "buy":        return _run_buy()
    if cmd == "datafeed":   return _run_datafeed()
    if cmd == "judge":      return _run_judge(date=args.date)
    if cmd == "nightly":    return _run_nightly(skip_datafeed=args.skip_datafeed)
    if cmd == "report":     return _run_report_only()

    logger.error(f"[main] 알 수 없는 cmd: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
