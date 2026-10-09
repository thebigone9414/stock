"""개별주식 일일 매매규칙 v1 - 진입 패턴 15개(6개 카드) + 공통 필터 + 62일선 청산 + 2ATR 손절. 백테스트와 스크리너가 같은 정의를 씀."""
import sys, os; BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
import numpy as np, pandas as pd
CARD={  # 패턴 키 -> (카드, 표시 이름)
 'K1 MACD골든+62상승':('A1','MACD 골든크로스 + 62일선 상승'),
 'K3 RSI50돌파+히스토음':('A2','RSI 50 상향돌파 + 히스토 음수 + 62일선 상승'),
 'K4 5/21골든+62상승':('A3','5일선/21일선 골든크로스 + 5일 수익 양수 + 62일선 상승'),
 'K5 62선돌파+RSI중':('A4','62일선 상향돌파 + RSI 40~60 + 62일선 상승'),
 'C1 CIS 52주신고가+정배열':('B1','52주 신고가 + 정배열'),
 'C2 CIS 52주신고가+거래량':('B1','52주 신고가 + 거래량 1.5배'),
 'T3 터틀20+248위+거래량':('B2','20일 고가 돌파 + 거래량 1.5배'),
 'T1 터틀20 (20고돌파/10저이탈)':('B2','20일 고가 돌파'),
 'T4 터틀55+248위':('B3','55일 고가 돌파'),
 'D3 다바스 넓은박스(<20%)+거래량':('B4','20일 박스(폭 20% 이내) 상단 돌파 + 거래량 1.5배'),
 'G2 감천 F0.309 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)':('C','감천 격자 F0.309 상향돌파 + 62일선 상승'),
 'G2 감천 F0.382 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)':('C','감천 격자 F0.382 상향돌파 + 62일선 상승'),
 'G2 감천 F0.5 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)':('C','감천 격자 F0.5 상향돌파 + 62일선 상승'),
 'Q2 쿨라매기 완화 (60일+20%, 박스<20%)':('D','쿨라매기: 60일 +20% 선행 + 15일 박스 돌파 + 거래량 1.3배'),
 'Q4 쿨라매기, 20일선 이탈 청산':('D','쿨라매기: 60일 +30% 선행 + 15일 박스 15% 이내 돌파 + 거래량 1.5배'),
 'R2 RSI30상향+62상승':('E','RSI 30 상향돌파 + 62일선 상승'),
}
KEYS=list(CARD)
# 귀속 우선순위: 구체적인(드문) 패턴부터
PRIO=['D3 다바스 넓은박스(<20%)+거래량','Q4 쿨라매기, 20일선 이탈 청산','Q2 쿨라매기 완화 (60일+20%, 박스<20%)','C1 CIS 52주신고가+정배열','C2 CIS 52주신고가+거래량','T4 터틀55+248위','T3 터틀20+248위+거래량','T1 터틀20 (20고돌파/10저이탈)','G2 감천 F0.309 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)','G2 감천 F0.382 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)','G2 감천 F0.5 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)','K4 5/21골든+62상승','K5 62선돌파+RSI중','K3 RSI50돌파+히스토음','K1 MACD골든+62상승','R2 RSI30상향+62상승']
MIN_AMT=3e9; STOPK=2.0; M=10
def k200_regime(k200_csv=None):
    k200_csv=k200_csv or os.path.join(BASE,'k200.csv')
    k=pd.read_csv(k200_csv,parse_dates=['date']).set_index('date').sort_index(); ma62=k.close.rolling(62).mean()
    return (ma62.diff(20)>0), ma62, k
def today_signals(P, date=None):
    """패널 P(code,date) -> 해당 날짜에 진입 패턴을 통과한 종목 표 (공통 필터: 종가>248일선, 거래대금, 거래정지 아님)"""
    from stock_lib import patterns_for
    rows=[]
    for code,x in P.groupby(level=0):
        x=x.droplevel(0)
        if len(x)<300: continue
        t=x.index[-1] if date is None else date
        if t not in x.index: continue
        i=x.index.get_loc(t)
        if i<300: continue
        r=x.iloc[i]
        if not (r['C']>r['ma248']) or not (r['amt20']>=MIN_AMT) or not r['trd']: continue
        sp=patterns_for(x); hits=[k for k in KEYS if k in sp and sp[k]['ent'][i]]
        if not hits: continue
        stop=r['C']-STOPK*r['atr']
        rows.append(dict(code=code,name=x['name'].iloc[-1],카드=','.join(sorted({CARD[k][0] for k in hits})),패턴='; '.join(CARD[k][1] for k in hits),종가=r['C'],손절=stop,손절폭=STOPK*r['atr']/r['C'],r120=r['r120'],ma62=r['ma62'],amt=r['amt20']/1e8))
    return pd.DataFrame(rows).sort_values('r120',ascending=False) if rows else pd.DataFrame()

def check_positions(P, posf=None):
    """보유 종목 점검. positions_stock.csv (code,패턴,매수일,매수가,손절가[,수량]) -> DataFrame
    열: code,name,패턴,매수일,매수가,손절가,종가,ma62,저가,pnl_pct,stop_hit(오늘 저가 <= 손절가),exit_signal(종가 < 62일선 → 내일 시가 매도),action"""
    posf=posf or os.path.join(BASE,'positions_stock.csv'); rows=[]
    if not os.path.exists(posf): return pd.DataFrame(columns=['code','name','패턴','매수일','매수가','손절가','종가','ma62','저가','pnl_pct','stop_hit','exit_signal','action'])
    Pos=pd.read_csv(posf,dtype={'code':str})
    codes=set(P.index.get_level_values(0))
    for _,r in Pos.iterrows():
        c=str(r.code).zfill(6)
        if c not in codes: rows.append(dict(code=c,name='',패턴=r.get('패턴',''),매수일=r.get('매수일',''),매수가=float(r.매수가),손절가=float(r.손절가),종가=np.nan,ma62=np.nan,저가=np.nan,pnl_pct=np.nan,stop_hit=False,exit_signal=False,action='데이터 없음')); continue
        x=P.loc[c].iloc[-1]; px,m,lo=float(x['C']),float(x['ma62']),float(x['L']); st=float(r.손절가)
        hit=bool(lo<=st) if not np.isnan(lo) else False; ex=bool(px<m)
        act='손절 발동 (장중)' if hit else ('매도 (62일선 이탈) → 내일 시가' if ex else '보유')
        rows.append(dict(code=c,name=P.loc[c]['name'].iloc[-1],패턴=r.get('패턴',''),매수일=r.get('매수일',''),매수가=float(r.매수가),손절가=st,종가=px,ma62=m,저가=lo,pnl_pct=px/float(r.매수가)-1,stop_hit=hit,exit_signal=ex,action=act))
    return pd.DataFrame(rows)

def judge(P=None, posf=None, k200_csv=None, capital=None):
    """자동매매용 구조화 판정 (패널 마지막 날짜 = 오늘 종가 기준). 출력 없음.
    반환 dict: date, k200_date, k200_close, regime_on(코스피200 62일선 > 20일 전), ma62, ma62_20ago,
               positions(DataFrame, check_positions), signals(DataFrame, today_signals: 6개월 수익률 순),
               buys(DataFrame: 국면 상승이고 빈 자리가 있을 때 실제 매수 대상 = 미보유 신호 상위 free_slots개, capital 주면 amount/qty 포함),
               free_slots, per_slot(종목 1건 금액 = capital x 1/M)"""
    from stock_lib import build_panel
    if P is None: P=build_panel()
    reg,kma62,k=k200_regime(k200_csv); on=bool(reg.iloc[-1])
    pos=check_positions(P,posf); held=set(pos.code) if len(pos) else set()
    npos=int(len(pos)); free=max(0,M-npos)
    S=today_signals(P)
    buys=S[~S.code.isin(held)].head(free).copy() if (on and len(S)) else S.iloc[0:0].copy()
    per_slot=None
    if capital is not None:
        per_slot=capital/M
        if len(buys):
            buys['amount']=per_slot; buys['qty']=np.floor(per_slot/buys['종가']).astype(int)
            buys=buys[buys.qty>=1]
    return dict(date=P.index.get_level_values(1).max(),k200_date=reg.index[-1],k200_close=float(k.close.iloc[-1]),regime_on=on,ma62=float(kma62.iloc[-1]),ma62_20ago=float(kma62.iloc[-21]),
                positions=pos,signals=S,buys=buys,free_slots=free,per_slot=per_slot)
