"""
KIS API 연결 테스트
실제 API 호출 (모의투자 환경)
실행: python -m pytest tests/ -v
"""
import os
import pytest

# 환경변수 없으면 스킵
pytestmark = pytest.mark.skipif(
    not os.getenv("KIS_APP_KEY"),
    reason="KIS_APP_KEY 환경변수 미설정",
)


@pytest.fixture(scope="module")
def kis():
    from config.settings import get_settings
    from kis.factory import KIS
    settings = get_settings()
    return KIS(settings)


def test_token_issue(kis):
    token = kis.auth.get_access_token()
    assert token and len(token) > 10, "토큰 발급 실패"


def test_get_quote(kis):
    quote = kis.market.get_quote("005930")
    assert quote.code == "005930"
    assert quote.price > 0, "현재가 조회 실패"
    print(f"\n삼성전자 현재가: {quote.price:,}원 ({quote.change_rate:+.2f}%)")


def test_get_ohlcv(kis):
    df = kis.market.get_ohlcv("005930", period="D")
    assert not df.empty, "OHLCV 데이터 없음"
    assert "close" in df.columns
    print(f"\n삼성전자 일봉 {len(df)}개 조회")


def test_get_balance(kis):
    from engine.portfolio import Portfolio
    portfolio = Portfolio(kis.account)
    balance = portfolio.refresh()
    assert balance is not None
    print(f"\n잔고 조회 성공: 현금={balance.cash:,}원")


def test_volume_rank(kis):
    ranks = kis.market.get_volume_rank(top_n=10)
    assert len(ranks) > 0, "거래량 순위 조회 실패"
    print(f"\n거래량 1위: {ranks[0].get('hts_kor_isnm', '?')}")
