"""
KIS API 호출 속도 제한 (Rate Throttling)
한국투자증권 OpenAPI 초당 호출 제한 대응
"""
import time
import threading


class RateThrottler:
    """초당 최대 max_per_second 건 이하로 API 호출 제어 (thread-safe)

    사용법:
        throttler = RateThrottler(max_per_second=9)
        for code in codes:
            with throttler:
                api.call(code)
    """

    def __init__(self, max_per_second: int = 9):
        self._interval = 1.0 / max_per_second   # 최소 호출 간격(초)
        self._last_call = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        pass
