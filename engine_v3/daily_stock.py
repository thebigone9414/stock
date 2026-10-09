"""개별주식 일일 매매규칙 v1 스크리너
사용:
  python3 daily_stock.py                       stockdata/ohlcv.pkl 기준 (마지막 날짜 = 오늘)
  python3 daily_stock.py --xlsx 파일.xlsx       HTS에서 새로 뽑은 ohlcv 파일 (code,name,date,open,high,low,close,volume) 로 판정
보유 종목은 positions_stock.csv (code,패턴,매수일,매수가,손절가) 에 기록. 코스피200 국면은 k200.csv (daily_v3.py --add 로 매일 갱신).
규칙: 종가 후 15개 진입 패턴 통과 + 종가>248일선 + 거래대금 30억 + 코스피200 62일선 상승 → 다음날 종가 매수 10%.
      종가 < 62일선이면 다음날 시가 매도. 손절 = 신호일 종가 - 2 ATR (장중). 후보가 자리보다 많으면 6개월 수익률 순.
"""
import sys, os; BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from stock_lib import build_panel
from stock_rules import today_signals, k200_regime, M, MIN_AMT
args=sys.argv[1:]; src=None
if '--xlsx' in args: src=args[args.index('--xlsx')+1]
P=build_panel(src=src) if src else build_panel()
reg,kma62,k=k200_regime(); on=bool(reg.iloc[-1]); kt=reg.index[-1]
today=P.index.get_level_values(1).max()
print('='*78); print(f'  개별주식 일일 규칙 v1   종목 데이터 {today:%Y-%m-%d}   코스피200 {kt:%Y-%m-%d} 종가 {k.close.iloc[-1]:,.2f}'); print('='*78)
print(f"  국면: 코스피200 62일선 {kma62.iloc[-1]:,.1f} (20일 전 {kma62.iloc[-21]:,.1f}, 기울기 {kma62.iloc[-1]-kma62.iloc[-21]:+.1f}) → {'상승 (신규 매수 가능)' if on else '하락 (신규 매수 없음, 보유분은 규칙대로 청산)'}")
if today!=kt: print(f'  주의: 종목 데이터({today:%m/%d})와 코스피200({kt:%m/%d}) 날짜가 다름')
# 보유 점검
print('\n■ 보유 점검 (종가 < 62일선 → 내일 시가 매도 / 저가 <= 손절가 → 손절 발동)')
pf=os.path.join(BASE,'positions_stock.csv'); held=set(); npos=0
if os.path.exists(pf):
    Pos=pd.read_csv(pf,dtype={'code':str})
    for _,r in Pos.iterrows():
        c=r.code; held.add(c)
        if c not in P.index.get_level_values(0): print(f'  {c}: 데이터 없음'); continue
        x=P.loc[c].iloc[-1]; px,m,lo=x['C'],x['ma62'],x['L']; name=P.loc[c]['name'].iloc[-1]; st=float(r.손절가)
        flag='★ 손절 발동 (장중)' if lo<=st else ('★ 매도 (62일선 이탈) → 내일 시가' if px<m else '보유')
        print(f"  {c} {name:<12} {str(r.패턴):<6} 종가 {px:>10,.0f}  62일선 {m:>10,.0f} ({px/m-1:+.1%})  손절 {st:>10,.0f} ({st/px-1:+.1%})  매수 {float(r.매수가):,.0f} ({px/float(r.매수가)-1:+.1%})  → {flag}")
        npos+=1
    if npos==0: print('  없음')
else: print('  positions_stock.csv 없음 (보유 없음)')
free=M-npos
# 오늘 신호
S=today_signals(P)
print(f'\n■ 오늘 신호 (종가>248일선, 거래대금 {MIN_AMT/1e8:.0f}억 이상, 6개월 수익률 순)   빈 자리 {free}개')
if len(S)==0: print('  없음')
for i,(_,t) in enumerate(S.iterrows(),1):
    tag='' if t.code in held else ('  ← 매수 (내일 종가, 10%)' if (on and i<=free) else '')
    if t.code in held: tag='  (보유 중)'
    print(f"  {i:>2}. {t.code} {t['name']:<12} [{t.카드}] 종가 {t.종가:>10,.0f}  손절 {t.손절:>10,.0f} (-{t.손절폭:.1%})  6개월 {t.r120:+7.1%}  거래대금 {t.amt:>7,.0f}억{tag}")
    print(f"       {t.패턴}")
if not on: print('\n  국면 하락: 오늘 신호는 참고용. 62일선이 20일 전보다 높아진 날부터 매수.')
