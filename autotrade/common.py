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


def is_stopped() -> bool:
    """STOP 파일 존재 체크. True면 매수 금지, 매도·손절만 수행."""
    return cfg.abspath(cfg.get("safety.stop_file")).exists()


def is_dry_run() -> bool:
    return bool(cfg.get("safety.dry_run", True))
