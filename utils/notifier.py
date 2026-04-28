from typing import Optional
import requests
from loguru import logger


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, message: str) -> bool:
        try:
            resp = requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Telegram 알림 전송 실패: {e}")
            return False


class Notifier:
    def __init__(self, telegram: Optional[TelegramNotifier] = None):
        self._telegram = telegram

    @classmethod
    def from_settings(cls, settings) -> "Notifier":
        telegram = None
        if settings.telegram_bot_token and settings.telegram_chat_id:
            telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        return cls(telegram=telegram)

    def notify(self, message: str) -> None:
        logger.info(f"[알림] {message}")
        if self._telegram:
            self._telegram.send(message)
