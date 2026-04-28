"""
한국 증시 휴장일 관리 (2025~2026)
매년 연말에 다음 해 휴장일 추가 필요
"""
from datetime import date
from typing import Optional
import pytz

KST = pytz.timezone("Asia/Seoul")

# 한국거래소 공식 휴장일
MARKET_HOLIDAYS = {
    # ── 2025 ──────────────────────────────────────────
    date(2025, 1,  1),   # 신정
    date(2025, 1, 28),   # 설날 연휴
    date(2025, 1, 29),   # 설날
    date(2025, 1, 30),   # 설날 연휴
    date(2025, 3,  1),   # 삼일절
    date(2025, 3,  3),   # 대통령선거일
    date(2025, 5,  5),   # 어린이날
    date(2025, 5,  6),   # 대체공휴일
    date(2025, 5, 13),   # 부처님오신날
    date(2025, 6,  6),   # 현충일
    date(2025, 8, 15),   # 광복절
    date(2025, 10, 3),   # 개천절
    date(2025, 10, 5),   # 추석 연휴
    date(2025, 10, 6),   # 추석
    date(2025, 10, 7),   # 추석 연휴
    date(2025, 10, 8),   # 대체공휴일
    date(2025, 10, 9),   # 한글날
    date(2025, 12, 25),  # 크리스마스
    date(2025, 12, 31),  # 연말 임시 휴장

    # ── 2026 ──────────────────────────────────────────
    date(2026, 1,  1),   # 신정
    date(2026, 1, 15),   # 설날 연휴
    date(2026, 1, 16),   # 설날
    date(2026, 1, 17),   # 설날 연휴
    date(2026, 3,  1),   # 삼일절 (일요일)
    date(2026, 3,  2),   # 삼일절 대체공휴일
    date(2026, 5,  5),   # 어린이날
    date(2026, 5, 24),   # 부처님오신날
    date(2026, 6,  6),   # 현충일 (토요일)
    date(2026, 8, 15),   # 광복절
    date(2026, 9, 24),   # 추석 연휴
    date(2026, 9, 25),   # 추석
    date(2026, 9, 26),   # 추석 연휴
    date(2026, 10, 3),   # 개천절 (토요일)
    date(2026, 10, 9),   # 한글날
    date(2026, 12, 25),  # 크리스마스
}


def is_market_holiday(check_date: Optional[date] = None) -> bool:
    """오늘(또는 지정일)이 증시 휴장일이면 True"""
    from datetime import datetime
    if check_date is None:
        check_date = datetime.now(KST).date()
    # 주말
    if check_date.weekday() >= 5:
        return True
    return check_date in MARKET_HOLIDAYS


def is_trading_day(check_date: Optional[date] = None) -> bool:
    return not is_market_holiday(check_date)


def next_trading_day(from_date: Optional[date] = None) -> date:
    """다음 거래일 반환"""
    from datetime import timedelta, datetime
    if from_date is None:
        from_date = datetime.now(KST).date()
    d = from_date + timedelta(days=1)
    while is_market_holiday(d):
        d += timedelta(days=1)
    return d
