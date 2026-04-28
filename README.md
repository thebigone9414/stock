# KIS 자동매매 시스템

한국투자증권 OpenAPI 기반 주식 자동매매 시스템

## 특징

- **모의투자 / 실전투자 전환** — `.env`의 `KIS_IS_PAPER_TRADING` 값 하나로 전환
- **플러그인 전략** — `strategies/` 아래 클래스 추가만으로 새 전략 적용
- **리스크 관리** — 종목당 최대 투자비율, 일일 손실 한도, 자동 손절/익절
- **GitHub Actions 자동화** — 평일 장중 5분마다 자동 실행, 별도 서버 불필요
- **Telegram 알림** — 매수/매도/결산 알림 (선택)

## 내장 전략

| 전략 | 설명 |
|------|------|
| `MA_Cross` | 5일/20일 이동평균 골든·데드크로스 |
| `RSI` | RSI 30↓ 매수, 70↑ 매도 |
| `Momentum` | 20일 수익률 상위 종목 매수 |
| `VolumeBreakout` | 거래량 3배 급증 + 가격 2% 상승 |

## 설치 및 설정

```bash
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 API 키 입력
```

## 실행

```bash
# 잔고 조회
python main.py --mode balance

# 종목 시세 조회
python main.py --mode market --code 005930

# 단일 매매 사이클 (테스트)
python main.py --run-once

# 자동 스케줄 실행 (로컬)
python main.py
```

## GitHub 배포 설정

1. 레포지토리 **Settings → Secrets → Actions**에 다음 추가:
   - `KIS_APP_KEY`
   - `KIS_APP_SECRET`
   - `KIS_ACCOUNT_NO` (예: `12345678-01`)
   - `KIS_IS_PAPER_TRADING` (`true` / `false`)
   - `TELEGRAM_BOT_TOKEN` (선택)
   - `TELEGRAM_CHAT_ID` (선택)

2. `.github/workflows/trading.yml`이 평일 장중 자동 실행

## 새 전략 추가

```python
# strategies/my_strategy.py
from strategies.base import BaseStrategy, Signal, SignalType

class MyStrategy(BaseStrategy):
    name = "MyStrategy"

    def generate_signals(self, market, portfolio):
        signals = []
        # ... 로직 작성
        return signals
```

`main.py`의 `build_strategies()`에 추가하면 즉시 적용.

## 리스크 설정 (`main.py`)

```python
RiskConfig(
    max_position_ratio=0.15,    # 종목당 최대 15%
    max_positions=8,            # 최대 8종목
    daily_loss_limit_ratio=0.05,# 일일 손실 5% 한도
    stop_loss_ratio=0.07,       # 손절 7%
    take_profit_ratio=0.15,     # 익절 15%
)
```

## 모의 → 실전 전환

`.env`에서:
```
KIS_IS_PAPER_TRADING=false
```
GitHub Secrets `KIS_IS_PAPER_TRADING`도 `false`로 변경.
