"""insider_backtest/common.py — 공통 유틸

- SEC HTTP 세션 (User-Agent 필수, rate limit)
- 경로 (project root 기준)
- 로깅
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests


SEC_UA = os.environ.get(
    "SEC_USER_AGENT",
    "Autotrade Research thebigone9414@gmail.com",
)

# 디렉토리 (스크립트 어디서 실행하든 동일)
HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent.parent.resolve()         # /home/user/stock
OUT_DIR = HERE / "out"                       # 산출물
RAW_DIR = HERE / "raw"                       # SEC zip
PRICES_DIR = HERE / "prices"                 # yfinance 캐시
LOG_DIR = HERE / "logs"

for d in (OUT_DIR, RAW_DIR, PRICES_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ── 로깅 ───────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
    log.addHandler(h)
    # 파일 로그
    fh = logging.FileHandler(LOG_DIR / f"{name}.log")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    log.addHandler(fh)
    return log


# ── SEC HTTP 세션 (rate limit) ──────────────────────────
class SECSession:
    def __init__(self, min_interval_s: float = 0.12):  # 10/s → 안전하게 ~8/s
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent":      SEC_UA,
            "Accept-Encoding": "gzip, deflate",
            "Host":            "www.sec.gov",
        })
        self.min_interval = min_interval_s
        self._last = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        # Host 헤더는 URL 호스트에 맞춰 재설정
        from urllib.parse import urlparse
        host = urlparse(url).hostname
        if host:
            kwargs.setdefault("headers", {})
            # per-request header overlay
            h = dict(self.s.headers)
            h["Host"] = host
            kwargs["headers"] = {**h, **kwargs.get("headers", {})}
        r = self.s.get(url, timeout=60, **kwargs)
        return r

    def download_to(self, url: str, path: Path, retries: int = 4) -> bool:
        """지시서 §1-1: 동일 zip 재다운로드 방지 — 이미 존재하면 skip."""
        if path.exists() and path.stat().st_size > 1024:
            return True
        delay = 2.0
        for attempt in range(retries + 1):
            try:
                r = self.get(url)
                if r.status_code == 200 and len(r.content) > 1024:
                    path.write_bytes(r.content)
                    return True
                if r.status_code in (429, 503) and attempt < retries:
                    time.sleep(delay); delay *= 2
                    continue
                # 404 등 재시도 무의미
                return False
            except Exception as e:
                if attempt < retries:
                    time.sleep(delay); delay *= 2
                    continue
                raise
        return False
