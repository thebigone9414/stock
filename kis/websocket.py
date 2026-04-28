"""
KIS WebSocket 실시간 데이터 수신 모듈
- 실시간 체결가 구독
- 실시간 호가 구독
- 실시간 체결 통보
"""
import json
import threading
import time
from typing import Callable, Dict, Optional

import websocket
from loguru import logger

from .auth import KISAuth


class KISWebSocket:
    def __init__(self, auth: KISAuth, ws_url: str, is_paper: bool):
        self.auth = auth
        self.ws_url = ws_url
        self.is_paper = is_paper
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._handlers: Dict[str, Callable] = {}
        self._running = False
        self._approval_key: Optional[str] = None

    def _get_approval_key(self) -> str:
        """WebSocket 접속 승인 키 발급"""
        import requests
        url = f"{self.auth.base_url.replace('openapivts', 'openapi').replace('29443', '9443')}"
        # 승인키 URL은 실전/모의 공통
        if self.is_paper:
            approval_url = "https://openapivts.koreainvestment.com:29443/oauth2/Approval"
        else:
            approval_url = "https://openapi.koreainvestment.com:9443/oauth2/Approval"

        resp = requests.post(
            approval_url,
            json={
                "grant_type": "client_credentials",
                "appkey": self.auth.app_key,
                "secretkey": self.auth.app_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("approval_key", "")

    def subscribe_price(self, code: str, handler: Callable[[dict], None]) -> None:
        """실시간 체결가 구독 (H0STCNT0)"""
        self._handlers[f"H0STCNT0_{code}"] = handler
        self._send_subscribe("H0STCNT0", code)

    def subscribe_orderbook(self, code: str, handler: Callable[[dict], None]) -> None:
        """실시간 호가 구독 (H0STASP0)"""
        self._handlers[f"H0STASP0_{code}"] = handler
        self._send_subscribe("H0STASP0", code)

    def _send_subscribe(self, tr_id: str, code: str) -> None:
        if self._ws and self._ws.sock:
            msg = {
                "header": {
                    "approval_key": self._approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": tr_id, "tr_key": code}},
            }
            self._ws.send(json.dumps(msg))

    def _on_message(self, ws, raw: str) -> None:
        try:
            if raw.startswith("{"):
                data = json.loads(raw)
                # PINGPONG
                if data.get("header", {}).get("tr_id") == "PINGPONG":
                    ws.send(raw)
                    return
                # 체결 통보 등
                tr_id = data.get("header", {}).get("tr_id", "")
                if tr_id in self._handlers:
                    self._handlers[tr_id](data.get("body", {}))
            else:
                # 파이프('|')로 구분된 실시간 데이터
                parts = raw.split("|")
                if len(parts) >= 4:
                    tr_id = parts[1]
                    body_str = parts[3]
                    key = None
                    for k in self._handlers:
                        if k.startswith(tr_id):
                            key = k
                            break
                    if key and key in self._handlers:
                        self._handlers[key]({"raw": body_str})
        except Exception as e:
            logger.warning(f"WebSocket 메시지 처리 오류: {e}")

    def _on_error(self, ws, error) -> None:
        logger.error(f"WebSocket 오류: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        logger.info("WebSocket 연결 종료")
        self._running = False

    def _on_open(self, ws) -> None:
        logger.info("WebSocket 연결 성공")
        # 기존 구독 재전송
        for key in self._handlers:
            parts = key.split("_", 1)
            if len(parts) == 2:
                self._send_subscribe(parts[0], parts[1])

    def start(self) -> None:
        if self._running:
            return
        self._approval_key = self._get_approval_key()
        self._ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )
        self._running = True
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()
        logger.info(f"WebSocket 스트림 시작: {self.ws_url}")

    def _run_forever(self) -> None:
        while self._running:
            try:
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.warning(f"WebSocket 재연결 시도: {e}")
                time.sleep(5)

    def stop(self) -> None:
        self._running = False
        if self._ws:
            self._ws.close()
