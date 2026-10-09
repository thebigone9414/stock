# engine_v3 - 코스피200 11패턴 + 개별주식 v1 신호 엔진 (자동매매용)

백테스트에 쓴 코드와 기본 데이터를 그대로 묶은 폴더입니다. 레포에 `engine_v3/` 로 두고 import 해서 씁니다. 규칙을 다시 구현하지 않습니다 (지시서 원칙 1).

## 1. 폴더 내용

| 파일 | 역할 |
|---|---|
| `anchor.py` `lib.py` `hunt.py` `vehicle.py` `roll.py` | 코스피200 지표, 트리거, 11패턴 조립, 백테스트 엔진 |
| `daily_v3.py` | 코스피200 ETF 11패턴 일일 판정. 자동매매는 `daily_v3.judge()` 와 `daily_v3.add_bar()` 를 호출 |
| `stock_lib.py` `stock_rules.py` | 개별주식 지표, 패턴 15개, 카드 매핑, 공통 필터. 자동매매는 `stock_rules.judge()` 와 `stock_lib.append_bars()` 를 호출 |
| `daily_stock.py` `port_stock.py` `mom_lib.py` | 종목 스크리너(CLI), 포트폴리오 백테스트, 변형 B(월말 모멘텀, 참고용) |
| `k200.csv` | 코스피200 지수 일봉 2000-12-26 ~ 2026-10-08 (`date,open,high,low,close`) |
| `stockdata/ohlcv.csv.gz` | 344종목 일봉 2016-10-11 ~ 2026-10-08 (`code,name,date,open,high,low,close,volume`, 수정주가, 거래정지일은 open/high/low 0, volume 0) |
| `stockdata/panel.pkl` | 지표 패널. 첫 실행 때 자동 생성 (약 40초, 330MB). 지워도 다시 만들어짐. git 에 넣지 말 것 |
| `positions_v3.csv` | ETF 보유 상태 (`패턴,진입일,진입가,계약수,손절`). 진입일 = 신호일, 진입가 = 신호일 지수 종가. 현재 #7 1건 |
| `positions_stock.csv` | 종목 보유 상태 (`code,패턴,매수일,매수가,손절가`, 열 추가 가능). 현재 없음 |
| `docs/` | 규칙집 2개, 자동매매 지시서, 백테스트 거래 목록 xlsx 2개 |

## 2. 설치

- Python 3.10 이상, `pip install -r requirements.txt` (numpy, pandas, numba, openpyxl)
- 처음 한 번 `python3 engine_v3/daily_stock.py` 를 실행해 `panel.pkl` 을 만든다.
- 코드 안의 경로는 전부 이 폴더 기준이라 레포 어디에 두어도 된다. 실행 위치(cwd)와 무관.

## 3. 매일 20:10 호출 순서 (지시서 4절)

```python
import sys; sys.path.insert(0, 'engine_v3')
import daily_v3, stock_lib, stock_rules

# 1) 코스피200 지수 당일 시가 고가 저가 종가 (지수 자체, KRX 정규장 확정값)
daily_v3.add_bar('2026-10-09', o, h, l, c)

# 2) 종목 당일 일봉 추가 + 패널 재생성 (약 40초)
#    rows: code,name,date,open,high,low,close,volume 의 DataFrame 또는 dict 리스트. 수정주가 기준
P = stock_lib.append_bars(rows)

# 3) 판정 (출력 없음, dict 반환)
etf = daily_v3.judge(dep=ETF_EQUITY)                 # ETF 규칙 몫의 평가자산 (현금 + 보유 평가액)
stk = stock_rules.judge(P=P, capital=STOCK_EQUITY)   # 종목 규칙 몫의 평가자산
```

반환값과 주문 계획의 대응:

| 반환 | 뜻 | 주문 |
|---|---|---|
| `etf['positions'][i]['exit_signal']` | 패턴 청산 조건이 오늘 종가에 성립 | 내일 08:50 장전 동시호가 시장가 매도 (숏 #11 은 인버스 매도) |
| `etf['positions'][i]['stop_hit']` | 오늘 저가(숏은 고가)가 손절선 통과 | 장중 체크포인트에서 이미 팔렸어야 함. 20:10 에 True 인데 보유 중이면 체크포인트가 놓친 것 - 내일 08:50 매도 + 경고 |
| `etf['signals']` 중 `dup == False` 인 것, 앞에서부터 `free_slots` 건 | 오늘 새 신호 | 내일 15:21 장 마감 동시호가 시장가 매수. `vehicle` (KODEX 레버리지 / KODEX 인버스), `amount` = dep x `frac`(0.2 / 0.4), `stop` = 지수 손절선 |
| `etf['near']` | 사건은 있었지만 조건 미달 | 참고용, 주문 없음 |
| `stk['positions']` 의 `exit_signal` / `stop_hit` | 종가 < 62일선 / 저가 <= 손절가 | 위와 같음 |
| `stk['buys']` | 국면 상승 + 빈 자리 있을 때 실제 매수 대상 (`amount`, `qty` 포함) | 내일 15:21 시장가 매수. 손절가 = `손절` 열 |
| `stk['signals']` | 공통 필터 통과 신호 전체 (6개월 수익률 순) | 국면 하락이면 참고용 |
| `stk['regime_on']`, `ma62`, `ma62_20ago` | 코스피200 62일선 > 20일 전 | 텔레그램 보고의 국면 |

체결 뒤 상태 파일 기록:
- `positions_v3.csv`: `패턴, 진입일(신호일), 진입가(신호일 지수 종가 = etf['close']), 계약수(ETF 주수), 손절(signals 의 stop)`. ETF 체결가는 별도 열(예: `etf매수가`)로 추가해도 된다. 엔진은 앞의 5개 열만 읽는다.
- `positions_stock.csv`: `code, 패턴(카드), 매수일, 매수가(체결가), 손절가(signals 의 손절), 수량`.
- 매도·손절 체결 뒤에는 해당 행을 지운다.

## 4. CLI (사람이 눈으로 볼 때)

```
python3 engine_v3/daily_v3.py --dep 31401153 [--add 2026-10-09 o h l c]
python3 engine_v3/daily_stock.py [--xlsx 새파일.xlsx]
```

## 5. 백테스트 재현 (지시서 10절 검수용)

```python
import sys; sys.path.insert(0, 'engine_v3')
from lib import build; from vehicle import patterns; import roll
x = build('engine_v3/k200.csv'); P = patterns(x)
P11 = {k: v for k, v in P.items() if k not in ('MHup|ma62up', 'MA21dn|ma200dn+align')}
tr, cv = roll.multi_mtm(x, P11, lev=2, cash_frac=0.2, maxpos=5, stopcap=0.10, entry_at='next_close', exit_at='open')
# 2026-10-08 데이터 기준 767건, CAGR 15.9%, MDD -31.8%, Sharpe 0.89. 거래 목록은 docs/11패턴_매매시점_전체목록.xlsx
```

종목 규칙의 재현 코드는 `docs/개별주식_매매기법_v1.md` 6절과 `port_stock.simulate` 를 참조 (589건, CAGR 27.0%, MDD -44.0%).

## 6. 라이브 데이터 주의

- 종목 일봉은 수정주가 기준이어야 한다 (기본 데이터가 수정주가). 증권사 일봉 API 의 수정주가 옵션을 켜고, 액면분할·감자 등 수정 이벤트가 생긴 종목은 과거 전체를 다시 받아 `ohlcv.csv.gz` 의 그 종목 행을 교체한다.
- 거래정지 종목은 open/high/low 0, volume 0 으로 넣으면 엔진이 처리한다.
- 유니버스에 새로 들어온 종목은 이력 300일 미만이면 자동으로 판정에서 빠진다 (`today_signals` 가 300일 미만 제외).
- 지수(k200.csv)와 종목 데이터의 마지막 날짜가 다르면 판정하지 않는다.
- 판정은 전부 KRX 정규장 시고저종. NXT 체결가는 넣지 않는다.
