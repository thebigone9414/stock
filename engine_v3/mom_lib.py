"""개별주식 매매기법 v1 - 강한 종목 보유 규칙 (월말 6개월 모멘텀 상위 10 + 코스피200 국면 + 종목 62일선 이탈 매도)
백테스트와 일일 스크리너가 같은 함수를 씀."""
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, sys, os
BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
TOP=10; MIN_AMT=3e9; LOOK=120; BUY_COST=0.0010; SELL_COST=0.0025

def load_prices(src=None):
    """ohlcv.pkl (code,name,date,open,high,low,close,volume) 또는 같은 열의 xlsx/csv(.gz)"""
    if src is None:
        p=os.path.join(BASE,'stockdata','ohlcv.pkl'); src=p if os.path.exists(p) else os.path.join(BASE,'stockdata','ohlcv.csv.gz')
    if src.endswith('.pkl'): df=pd.read_pickle(src)
    elif src.endswith('.xlsx'): df=pd.read_excel(src,sheet_name=0,dtype={'code':str})
    else: df=pd.read_csv(src,dtype={'code':str})
    df['date']=pd.to_datetime(df['date']); df=df.sort_values(['code','date'])
    return df

def wide(df):
    """종목별 지표 -> 날짜 x 종목 2D 표"""
    g=df.copy(); halted=(g.volume<=0)|(g.open<=0)|(g.low<=0)
    g.loc[halted,['open','high','low']]=np.nan
    C=g.pivot(index='date',columns='code',values='close').sort_index(); O=g.pivot(index='date',columns='code',values='open').sort_index()
    L=g.pivot(index='date',columns='code',values='low').sort_index(); V=g.pivot(index='date',columns='code',values='volume').sort_index()
    C=C.ffill()   # 거래정지일 종가는 전일 복사 (데이터 자체가 그렇게 옴)
    amt20=(C*V).rolling(20,min_periods=20).mean(); ma62=C.rolling(62).mean(); r120=C/C.shift(LOOK)-1
    names=g.drop_duplicates('code').set_index('code')['name'].to_dict()
    return dict(C=C,O=O,L=L,amt20=amt20,ma62=ma62,r120=r120,names=names)

def k200_regime(k200_csv=None):
    k=pd.read_csv(k200_csv or os.path.join(BASE,'k200.csv'),parse_dates=['date']).set_index('date').sort_index()
    ma62=k.close.rolling(62).mean(); return (ma62.diff(20)>0), ma62

def rank_today(W,t):
    """t일 종가 기준 후보 순위 (유동성 필터, r120 내림차순). 반환 DataFrame"""
    C,O,amt,r120=W['C'],W['O'],W['amt20'],W['r120']
    ok=(amt.loc[t]>=MIN_AMT)&r120.loc[t].notna()&O.loc[t].notna()
    s=r120.loc[t][ok].sort_values(ascending=False)
    return pd.DataFrame({'code':s.index,'name':[W['names'].get(c,'') for c in s.index],'r120':s.values,'close':C.loc[t,s.index].values,'ma62':W['ma62'].loc[t,s.index].values,'amt20억':amt.loc[t,s.index].values/1e8})

def backtest(W,reg,start='2017-10-01',end=None,top=TOP):
    C,O,L,ma62=W['C'],W['O'],W['L'],W['ma62']; dates=C.index
    t0=int(dates.searchsorted(pd.Timestamp(start))); t1=len(dates) if end is None else int(dates.searchsorted(pd.Timestamp(end)))
    regv=reg.reindex(dates).fillna(False).values
    rb=set(i for i in range(t0,t1-1) if dates[i].month!=dates[i+1].month)
    Cv,Ov,Lv,Mv=C.values,O.values,L.values,ma62.values; codes=list(C.columns); ci={c:i for i,c in enumerate(codes)}
    cash=1.0; pos={}; pend_sell=[]; trades=[]; curve=[]; holdings=[]
    def close_pos(k,t,px,why):
        nonlocal cash
        s=pos.pop(k); v=s['sh']*px*(1-SELL_COST); cash+=v
        trades.append(dict(code=codes[k],name=W['names'].get(codes[k],''),매수일=dates[s['t']].date(),매수가=s['px'],매도일=dates[t].date(),매도가=px,수익률=v/s['cost']-1,보유일=t-s['t'],사유=why))
    for t in range(t0,t1):
        for k in list(pend_sell):
            if k in pos and not np.isnan(Ov[t,k]): close_pos(k,t,Ov[t,k],'62일선 이탈'); pend_sell.remove(k)
        eq=cash+sum(s['sh']*Cv[t,k] for k,s in pos.items() if not np.isnan(Cv[t,k]))
        if t in rb:
            if regv[t]:
                R=rank_today(W,dates[t]).head(top); want={ci[c] for c in R.code}
                for k in list(pos):
                    if k not in want: close_pos(k,t,Cv[t,k],'월말 순위 밖')
                eq=cash+sum(s['sh']*Cv[t,k] for k,s in pos.items() if not np.isnan(Cv[t,k]))
                for k in want:
                    if k in pos: continue
                    amt=min(eq/top,cash)
                    if amt>0: pos[k]=dict(sh=amt*(1-BUY_COST)/Cv[t,k],t=t,px=Cv[t,k],cost=amt); cash-=amt
                holdings.append(dict(날짜=dates[t].date(),국면='상승',종목=', '.join(f"{codes[k]} {W['names'].get(codes[k],'')}" for k in sorted(pos))))
            else:
                for k in list(pos): close_pos(k,t,Cv[t,k],'국면 종료 (전량 현금)')
                holdings.append(dict(날짜=dates[t].date(),국면='하락 (현금)',종목=''))
        else:
            for k in list(pos):
                if not np.isnan(Cv[t,k]) and Cv[t,k]<Mv[t,k] and k not in pend_sell: pend_sell.append(k)
        eq=cash+sum(s['sh']*Cv[t,k] for k,s in pos.items() if not np.isnan(Cv[t,k]))
        curve.append((dates[t],eq,len(pos)))
    cv=pd.DataFrame(curve,columns=['date','eq','npos'])
    return pd.DataFrame(trades),cv,pd.DataFrame(holdings)
