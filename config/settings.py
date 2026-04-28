from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # KIS API
    kis_app_key: str = Field(..., env="KIS_APP_KEY")
    kis_app_secret: str = Field(..., env="KIS_APP_SECRET")
    kis_account_no: str = Field(..., env="KIS_ACCOUNT_NO")
    kis_is_paper_trading: bool = Field(True, env="KIS_IS_PAPER_TRADING")

    # 모의/실전 URL 자동 전환
    @property
    def kis_base_url(self) -> str:
        if self.kis_is_paper_trading:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"

    @property
    def kis_ws_url(self) -> str:
        if self.kis_is_paper_trading:
            return "ws://ops.koreainvestment.com:31000"
        return "ws://ops.koreainvestment.com:21000"

    # 알림
    telegram_bot_token: Optional[str] = Field(None, env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(None, env="TELEGRAM_CHAT_ID")

    # 로깅
    log_level: str = Field("INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def get_settings() -> Settings:
    return Settings()
