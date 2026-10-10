"""B 모듈 (월말 6개월 모멘텀 상위 10, 코스피200 + 코스닥150, 국면, 62일선 매도) 일일·월말 점검 CLI
사용:
  python3 daily_B.py                          매일 점검: 보유 종목 62일선 이탈 여부 + 현재 순위표 (참고)
  python3 daily_B.py --rebalance              월말 교체 계획: 매도(순위 밖/국면 하락) + 매수(상위 10 중 미보유, 금액·수량)
  python3 daily_B.py --capital 10000000       B 몫 평가자산 (현금 + 보유 평가액). 수량 계산에 씀. 기본 1,000만원
  python3 daily_B.py --universe 파일.csv       유니버스 (code,name). 기본 universe_B.csv (없으면 데이터 전 종목 = 코스피200 + 코스닥150)
보유는 positions_B.csv (code,name,매수일,매수가,수량). 국면은 k200.csv (daily_v3.add_bar 로 갱신).
"""
import sys, os; BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import mom_lib
args=sys.argv[1:]
def opt(name,default=None):
    if name in args:
        i=args.index(name); return args[i+1]
    return default
cap=float(opt('--capital',10_000_000)); reb='--rebalance' in args
univ=mom_lib.load_universe(opt('--universe')) if opt('--universe') else None
j=mom_lib.judge(capital=cap,rebalance=reb,universe=univ)
print('='*78); print(f"  B 모듈 (월말 모멘텀)   기준일 {j['date']:%Y-%m-%d}   코스피200 {j['k200_close']:,.2f}   모드: {'월말 교체' if reb else '매일 점검'}"); print('='*78)
print(f"  국면: 62일선 {j['ma62']:,.1f} (20일 전 {j['ma62_20ago']:,.1f}) → {'상승 (매수 가능)' if j['regime_on'] else '하락 (월말에 전량 현금, 신규 매수 없음)'}   종목당 금액 {cap/mom_lib.TOP:,.0f}원")
P=j['positions']
print('\n■ 보유 점검 (종가 < 62일선 → 내일 09:00 시가 매도)')
if len(P)==0: print('  없음')
for _,r in P.iterrows():
    print(f"  {r.code} {str(r['name']):<10} 매수 {r.매수가:>10,.0f} x{int(r.수량):<4} 종가 {r.종가:>10,.0f} ({r.pnl_pct:+6.1%})  62일선 {r.ma62:>10,.0f}  순위 {str(r['rank']) if r['rank'] is not None and not (isinstance(r['rank'],float) and np.isnan(r['rank'])) else '밖':>3}  → {r.action}")
print(f"\n■ 6개월 수익률 순위 (유동성 30억 이상, 상위 15)")
for i,r in j['rank'].head(15).iterrows():
    print(f"  {i:>2}. {r.code} {r['name']:<10} 6개월 {r.r120:+7.1%}  종가 {r.close:>10,.0f}  62일선 {r.ma62:>10,.0f}  거래대금 {r['amt20억']:>7,.0f}억")
if reb:
    print('\n■ 월말 교체 계획 (마지막 거래일 15:20~15:30 동시호가 시장가)')
    S=j['sells_rebalance']
    print('  매도:', '없음' if len(S)==0 else '')
    for _,r in S.iterrows(): print(f"    {r.code} {str(r['name']):<10} x{int(r.수량)}  사유 {r.why}")
    Bq=j['buys']
    print('  매수:', '없음' if len(Bq)==0 else '')
    for i,r in Bq.iterrows(): print(f"    {i:>2}. {r.code} {r['name']:<10} {int(r.qty):>3}주 x {r.close:,.0f} = {int(r.qty)*r.close:>10,.0f}원  {r.skip}")
    if len(Bq): print(f"  매수 합계 {int((Bq.qty*Bq.close).sum()):,}원 / 종목 {int((Bq.qty>0).sum())}개")
