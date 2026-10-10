"""autotrade 공통 유틸

- KST 시각
- 거래일 체크 (레거시 data/holidays.py 재사용)
- 로깅 셋업
- Telegram 알림 (레거시 utils/notifier.py 재사용)
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pytz

from utils.logger import setup_logger as _setup_logger
from utils.notifier import Notifier
from data.holidays import is_market_holiday, is_trading_day
from config.settings import get_settings

from autotrade import config as cfg

KST = pytz.timezone("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def today_str() -> str:
    return now_kst().strftime("%Y-%m-%d")


def is_trading_today() -> bool:
    return is_trading_day(today_kst())


def next_trading_date(from_date: date | str) -> date:
    """다음 거래일 (오늘 다음, 휴장일 건너뜀)."""
    from datetime import date as _date, timedelta
    if isinstance(from_date, str):
        from_date = datetime.strptime(from_date, "%Y-%m-%d").date()
    d = from_date + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def is_last_trading_day_of_month(d: date | None = None) -> bool:
    """d 가 그 달의 마지막 거래일인가. (휴장일 조정 포함)"""
    from datetime import timedelta
    d = d or today_kst()
    nxt = next_trading_date(d)
    return nxt.month != d.month


def is_eve_of_last_trading_day_of_month(d: date | None = None) -> bool:
    """내일(=다음 거래일)이 그 달의 마지막 거래일인가.
    → True 이면 20:10 판정 시 B 모듈 rebalance=True 로 호출.
    """
    d = d or today_kst()
    nxt = next_trading_date(d)
    return is_last_trading_day_of_month(nxt)


def ensure_dirs() -> None:
    """config에 정의된 디렉토리들을 미리 생성."""
    for key in ("paths.orders_dir", "paths.state_dir"):
        p = cfg.abspath(cfg.get(key))
        p.mkdir(parents=True, exist_ok=True)
    for key in ("paths.trade_log_etf", "paths.trade_log_stock", "paths.equity_log"):
        p = cfg.abspath(cfg.get(key))
        p.parent.mkdir(parents=True, exist_ok=True)


def get_notifier() -> Notifier:
    """config.settings 기반 Telegram Notifier."""
    settings = get_settings()
    return Notifier.from_settings(settings)


def setup(command: str = "autotrade") -> Notifier:
    """공통 셋업: 로거 + 디렉토리 + Notifier 반환."""
    settings = get_settings()
    _setup_logger(settings.log_level)
    ensure_dirs()
    return get_notifier()


def is_stopped(module: str = "both") -> bool:
    """STOP 파일 체크. module='A'/'B'/'both'. True 면 해당 모듈 매수 금지."""
    sa = cfg.abspath(cfg.get("safety.stop_file_A", "autotrade/STOP_A")).exists()
    sb = cfg.abspath(cfg.get("safety.stop_file_B", "autotrade/STOP_B")).exists()
    if module == "A":    return sa
    if module == "B":    return sb
    return sa or sb


def is_dry_run() -> bool:
    return bool(cfg.get("safety.dry_run", True))
