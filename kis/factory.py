"""
KIS API 객체 통합 팩토리
설정에서 모든 KIS 모듈을 생성
"""
from config.settings import Settings
from .auth import KISAuth
from .client import KISClient
from .market import KISMarket
from .account import KISAccount
from .order import KISOrder
from .websocket import KISWebSocket


class KIS:
    """한국투자증권 API 통합 객체"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.auth = KISAuth(
            app_key=settings.kis_app_key,
            app_secret=settings.kis_app_secret,
            base_url=settings.kis_base_url,
            is_paper=settings.kis_is_paper_trading,
        )
        self._client = KISClient(self.auth, settings.kis_base_url, settings.kis_is_paper_trading)
        self.market = KISMarket(self._client)
        self.account = KISAccount(self._client, settings.kis_account_no, settings.kis_is_paper_trading)
        self.order = KISOrder(self._client, settings.kis_account_no, settings.kis_is_paper_trading)
        self.ws = KISWebSocket(self.auth, settings.kis_ws_url, settings.kis_is_paper_trading)

    @property
    def is_paper(self) -> bool:
        return self.settings.kis_is_paper_trading

    @property
    def mode_label(self) -> str:
        return "모의투자" if self.is_paper else "실전투자"
