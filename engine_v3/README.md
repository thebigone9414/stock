# engine_v3 - 모듈 A (코스피200 ETF 11패턴) + 모듈 B (월말 모멘텀) 신호 엔진 (자동매매용, v2 2026-10-10)

백테스트에 쓴 코드와 기본 데이터를 그대로 묶은 폴더입니다. 레포에 `engine_v3/` 로 두고 import 해서 씁니다. 규칙을 다시 구현하지 않습니다 (지시서 v2 원칙 1).

## 1. 폴더 내용

| 파일 | 역할 |
|---|---|
| `anchor.py` `lib.py` `hunt.py` `vehicle.py` `roll.py` | 코스피200 지표, 트리거, 11패턴 조립, 백테스트 엔진 |
| `daily_v3.py` | **모듈 A** 일일 판정. 자동매매는 `daily_v3.add_bar()` 와 `daily_v3.judge()` 호출. CLI 도 됨 |
| `mom_lib.py` | **모듈 B** 월말 모멘텀: `judge()`, `backtest()`, `rank_today()`, `load_universe()`, `load_positions()` |
| `daily_B.py` | 모듈 B CLI (매일 점검 / 월말 교체 계획). 자녀계좌 수동 운용에도 그대로 씀 |
| `stock_lib.py` | 종목 데이터 적재. 자동매매는 `stock_lib.append_bars()` 로 종목 일봉을 매일 추가 |
| `k200.csv` | 코스피200 지수 일봉 2000-12-26 ~ 2026-10-08 (`date,open,high,low,close`) |
| `stockdata/ohlcv.csv.gz` | 344종목(코스피200 + 코스닥150) 일봉 2016-10-11 ~ 2026-10-08 (`code,name,date,open,high,low,close,volume`, 수정주가, 거래정지일은 open/high/low 0, volume 0) |
| `positions_v3.csv` | 모듈 A 보유 (`패턴,진입일,진입가,계약수,손절`). 현재 #7 1건 (10/13 수동 매수 뒤 계약수·etf매수가 기록) |
| `positions_B.csv` | 모듈 B 보유 (`code,name,매수일,매수가,수량`). 현재 없음 |
| `docs/` | 규칙집, 개별주식 문서(B 는 9-1절·10절), 자동매매 지시서 v2, ETF 백테스트 거래 목록 xlsx |

## 2. 설치

- Python 3.10 이상, `pip install -r requirements.txt` (numpy, pandas, numba, openpyxl)
- 처음 한 번 `python3 engine_v3/daily_B.py` 와 `python3 engine_v3/daily_v3.py` 를 실행해 데이터 로드를 확인한다.
- 경로는 전부 이 폴더 기준. 실행 위치(cwd)와 무관.

## 3. 매일 20:10 호출 순서 (지시서 v2 5절)

```python
import sys; sys.path.insert(0, 'engine_v3')
import daily_v3, stock_lib, mom_lib

# 1) 코스피200 지수 당일 시가 고가 저가 종가 (지수 자체, KRX 정규장 확정값)
daily_v3.add_bar('2026-10-13', o, h, l, c)

# 2) 종목 당일 일봉 추가 (rows: code,name,date,open,high,low,close,volume 의 DataFrame 또는 dict 리스트, 수정주가)
stock_lib.append_bars(rows, rebuild=False)

# 3) 판정 (출력 없음, dict 반환)
A = daily_v3.judge(dep=A_EQUITY)                 # 모듈 A 평가자산 (현금 + 보유 평가액)
B = mom_lib.judge(capital=B_EQUITY)                   # 매일: 62일선 이탈 매도만
B = mom_lib.judge(capital=B_EQUITY, rebalance=True)   # 월말 전날: 교체 매도·매수 계획
```

반환값과 주문의 대응:

| 반환 | 뜻 | 주문 |
|---|---|---|
| `A['positions'][i]['sell']` | 패턴 청산(`exit_signal`) 또는 손절선 종가 이탈(`stop_close`). `why` = '청산'/'손절' | 내일 08:50 장전 동시호가 시장가 매도 (숏 #11 은 인버스 매도) |
| `A['signals']` 중 `dup == False`, 앞에서 `free_slots` 건 | 오늘 새 신호 | 내일 15:21 장 마감 동시호가 시장가 매수. `vehicle`, `amount` = dep x `frac`, `stop` = 지수 손절선 |
| `A['near']` | 조건 미달 사건 | 참고용 |
| `B['sells_daily']` | 보유 종목 종가 < 62일선 | 내일 08:50 매도 |
| `B['sells_rebalance']` (rebalance=True) | 월말 순위 밖 또는 국면 하락(전량) | 월말 15:21 시장가 매도 |
| `B['buys']` (rebalance=True) | 상위 10 중 미보유. `amount`, `qty`, `skip`(고가 종목 건너뜀·대체 후보 표시) | 월말 15:21 시장가 매수. `qty >= 1` 인 행만 |
| `B['rank']` | 6개월 수익률 순위표 | 보고용 |
| `B['regime_on']`, `ma62`, `ma62_20ago` | 코스피200 국면 | 보고용 |

체결 뒤 상태 파일:
- `positions_v3.csv`: `패턴, 진입일(신호일), 진입가(신호일 지수 종가 = A['close']), 계약수(ETF 주수), 손절(signals 의 stop)` + 추가 열 `etf매수가, etf매수일`. 엔진은 앞의 5개 열만 읽는다.
- `positions_B.csv`: `code, name, 매수일, 매수가(체결가), 수량`.
- 매도 체결 뒤 해당 행 삭제.

## 4. CLI (사람이 볼 때, 자녀계좌 수동 운용)

```
python3 engine_v3/daily_v3.py --dep 25000000 [--add 2026-10-13 o h l c]
python3 engine_v3/daily_B.py                                  매일 점검
python3 engine_v3/daily_B.py --rebalance --capital 10000000   월말 교체 계획
```

## 5. 백테스트 재현 (지시서 v2 11절)

```python
import sys; sys.path.insert(0, 'engine_v3')
from lib import build; from vehicle import patterns; import roll, mom_lib
x = build('engine_v3/k200.csv'); P = patterns(x)
P11 = {k: v for k, v in P.items() if k not in ('MHup|ma62up', 'MA21dn|ma200dn+align')}
tr, cv = roll.multi_mtm(x, P11, lev=2, cash_frac=0.2, maxpos=5, stopcap=0.10, entry_at='next_close', exit_at='open', stop_at='close')
# 모듈 A 운용 규격: 2026-10-08 기준 766건, CAGR 16.8%, MDD -30.8%, Sharpe 0.93
W = mom_lib.wide(mom_lib.load_prices()); reg, _ = mom_lib.k200_regime()
trB, cvB, hold = mom_lib.backtest(W, reg, exec_at='prev_judge')
# 모듈 B 운용 규격 (월말 전날 판정, 월말 종가 체결): 2017-10~2026-10 전 종목 CAGR 28.7%, MDD -31.0% (코스피200만 22.0% / -45.1%)
```

## 6. 라이브 데이터 주의

- 종목 일봉은 수정주가 기준. 액면분할·감자 등 이벤트 종목은 과거 전체를 다시 받아 교체.
- 거래정지 종목은 open/high/low 0, volume 0 으로 넣는다. 모듈 B 는 종가·거래량만 있어도 돌아간다(시고저는 없으면 종가로 채움).
- 유니버스에 새로 들어온 종목은 이력 120일이 쌓여야 순위에 들어간다. 유니버스를 좁히려면 `universe_B.csv` (code,name) 를 두면 그 종목만 쓴다.
- 지수(k200.csv)와 종목 데이터의 마지막 날짜가 다르면 판정하지 않는다.
- 판정은 전부 KRX 정규장. NXT 체결가는 넣지 않는다.
