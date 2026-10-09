"""
스케줄러 - 매매 사이클 주기적 실행
장중 매 N분마다 전략 실행, 장 시작/종료 이벤트 처리
"""
import time
from datetime import datetime
from typing import Callable

import schedule
import pytz
from loguru import logger


KST = pytz.timezone("Asia/Seoul")


class TradingScheduler:
    def __init__(self, cycle_minutes: int = 5):
        self.cycle_minutes = cycle_minutes

    def setup(
        self,
        on_cycle: Callable,
        on_market_open: Callable,
        on_market_close: Callable,
    ) -> None:
        # 장 시작 (09:01 여유)
        schedule.every().day.at("09:01").do(self._with_log("장 시작", on_market_open))
        # 매매 사이클
        schedule.every(self.cycle_minutes).minutes.do(self._with_log("매매 사이클", on_cycle))
        # 장 종료
        schedule.every().day.at("15:35").do(self._with_log("장 종료", on_market_close))

        logger.info(
            f"스케줄러 등록: 장시작=09:01, 사이클={self.cycle_minutes}분, 장종료=15:35"
        )

    def _with_log(self, name: str, func: Callable) -> Callable:
        def wrapper():
            now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[스케줄] {name} 실행 @ {now}")
            try:
                func()
            except Exception as e:
                logger.error(f"[스케줄] {name} 오류: {e}")
        return wrapper

    def run_forever(self) -> None:
        logger.info("스케줄러 시작 (Ctrl+C로 종료)")
        while True:
            schedule.run_pending()
            time.sleep(10)
