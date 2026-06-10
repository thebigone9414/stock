"""
KIS REST API 기본 클라이언트
모든 API 호출의 공통 인증 헤더 처리 및 응답 검증
"""
from typing import Any, Dict, Optional

import requests
from loguru import logger
from tenacity import (retry, stop_after_attempt, wait_exponential,
                      retry_if_exception_type)
from requests.exceptions import ConnectionError as ReqConnError, Timeout as ReqTimeout

_RATE_LIMIT_CD = "EGW00201"

from .auth import KISAuth


class KISRateLimitError(Exception):
    """KIS API 초당 거래건수 초과 (EGW00201) — 재시도 대상"""
    pass


class KISAPIError(Exception):
    def __init__(self, rt_cd: str, message: str, raw: dict):
        super().__init__(f"[{rt_cd}] {message}")
        self.rt_cd = rt_cd
        self.raw = raw


class KISClient:
    def __init__(self, auth: KISAuth, base_url: str, is_paper: bool):
        self.auth = auth
        self.base_url = base_url
        self.is_paper = is_paper
        self._session = requests.Session()

    def _build_headers(self, tr_id: str, extra: Optional[Dict] = None) -> Dict[str, str]:
        token = self.auth.get_access_token()
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
            "appkey": self.auth.app_key,
            "appsecret": self.auth.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        if extra:
            headers.update(extra)
        return headers

    @retry(
        retry=retry_if_exception_type((ReqConnError, ReqTimeout, KISRateLimitError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1.5, min=2, max=15),
    )
    def get(self, path: str, tr_id: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = self._build_headers(tr_id)
        logger.debug(f"GET {url} tr_id={tr_id} params={params}")
        resp = self._session.get(url, headers=headers, params=params, timeout=30)
        return self._handle_response(resp)

    @retry(
        retry=retry_if_exception_type((ReqConnError, ReqTimeout, KISRateLimitError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1.5, min=2, max=15),
    )
    def post(self, path: str, tr_id: str, body: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = self._build_headers(tr_id)
        logger.debug(f"POST {url} tr_id={tr_id}")
        resp = self._session.post(url, headers=headers, json=body or {}, timeout=30)
        return self._handle_response(resp)

    def _handle_response(self, resp: requests.Response) -> Dict[str, Any]:
        if not resp.ok:
            # HTTP 500 본문에서 EGW00201(초당 건수 초과) 먼저 감지
            try:
                body = resp.json()
                if body.get("msg_cd") == _RATE_LIMIT_CD:
                    logger.warning("KIS 초당 거래건수 초과 (EGW00201) — 재시도 대기")
                    raise KISRateLimitError()
            except (ValueError, KISRateLimitError):
                raise
            except Exception:
                pass
            logger.error(f"HTTP {resp.status_code} {resp.reason}: {resp.text[:500]}")
            resp.raise_for_status()
        data = resp.json()
        rt_cd = data.get("rt_cd", "0")
        if rt_cd != "0":
            msg = data.get("msg1", "알 수 없는 오류")
            if data.get("msg_cd") == _RATE_LIMIT_CD:
                logger.warning("KIS 초당 거래건수 초과 (EGW00201) — 재시도 대기")
                raise KISRateLimitError()
            logger.error(f"KIS API 오류 [rt_cd={rt_cd}]: {msg}")
            raise KISAPIError(rt_cd, msg, data)
        return data


