"""
KIS OAuth2 토큰 관리 모듈
액세스 토큰은 발급 후 24시간 유효, 자동 갱신 처리
"""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import requests
from loguru import logger


TOKEN_CACHE_FILE = Path("tokens/access_token.json")


class KISAuth:
    def __init__(self, app_key: str, app_secret: str, base_url: str, is_paper: bool):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = base_url
        self.is_paper = is_paper
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def get_access_token(self) -> str:
        if self._is_token_valid():
            return self._access_token

        cached = self._load_cached_token()
        if cached:
            self._access_token = cached["access_token"]
            self._token_expires_at = datetime.fromisoformat(cached["expires_at"])
            if self._is_token_valid():
                logger.info("캐시된 액세스 토큰 사용")
                return self._access_token

        return self._issue_token()

    def _is_token_valid(self) -> bool:
        if not self._access_token or not self._token_expires_at:
            return False
        # 만료 10분 전에 재발급
        return datetime.now() < self._token_expires_at - timedelta(minutes=10)

    def _issue_token(self) -> str:
        logger.info("KIS 액세스 토큰 발급 중...")
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)

        self._save_token(self._access_token, self._token_expires_at)
        logger.info(f"토큰 발급 완료 (만료: {self._token_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
        return self._access_token

    def _save_token(self, token: str, expires_at: datetime) -> None:
        TOKEN_CACHE_FILE.parent.mkdir(exist_ok=True)
        TOKEN_CACHE_FILE.write_text(
            json.dumps({"access_token": token, "expires_at": expires_at.isoformat()}),
            encoding="utf-8",
        )

    def _load_cached_token(self) -> Optional[dict]:
        if not TOKEN_CACHE_FILE.exists():
            return None
        try:
            return json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def revoke_token(self) -> None:
        if not self._access_token:
            return
        url = f"{self.base_url}/oauth2/revokeP"
        payload = {
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "token": self._access_token,
        }
        try:
            requests.post(url, json=payload, timeout=10)
            logger.info("토큰 폐기 완료")
        except Exception as e:
            logger.warning(f"토큰 폐기 실패: {e}")
        finally:
            self._access_token = None
            self._token_expires_at = None
            if TOKEN_CACHE_FILE.exists():
                TOKEN_CACHE_FILE.unlink()
