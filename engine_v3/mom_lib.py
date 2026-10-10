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

def backtest(W,reg,start='2017-10-01',end=None,top=TOP,exec_at='same_close',use_regime=True,ma_exit=True,look=None,need_above62=False,skip=0,replace=True,regime_liquidate=True,freq='M',rb_offset=0,capital0=None):
    """capital0: 시작 자본(원). 주면 1주 가격 > 종목당 금액(자산/top) x 1.5 인 종목은 건너뛰고 다음 순위로 채움 (소액 운용 근사)"""
    """replace=False: 월말에 순위 밖이어도 팔지 않고 빈 자리만 채움 (추세선 이탈로만 매도). regime_liquidate=False: 국면 하락이어도 보유 유지(신규 매수만 중단)"""
    """exec_at: 'same_close'(월말 종가에 판정·체결) / 'next_close'(월말 종가로 판정, 다음 거래일 종가에 체결 - 자동매매 흐름)
       use_regime: 코스피200 국면 필터 사용 여부, ma_exit: 62일선 이탈 매도 사용 여부, look: r120 대신 쓸 수익률 기간(일)"""
    if look is not None:
        W=dict(W); W['r120']=W['C']/W['C'].shift(look)-1
    C,O,L,ma62=W['C'],W['O'],W['L'],W['ma62']; dates=C.index
    t0=int(dates.searchsorted(pd.Timestamp(start))); t1=len(dates) if end is None else int(dates.searchsorted(pd.Timestamp(end)))
    regv=reg.reindex(dates).fillna(False).values
    if not use_regime: regv=np.ones(len(dates),bool)
    if freq=='M': rb=set(i for i in range(t0,t1-1) if dates[i].month!=dates[i+1].month)
    elif freq=='W': rb=set(i for i in range(t0,t1-1) if dates[i].isocalendar()[1]!=dates[i+1].isocalendar()[1])
    else: rb=set(range(t0,t1-1))   # 'D': 매일
    if rb_offset: rb=set(i+rb_offset for i in rb if i+rb_offset<t1)   # 월말 대신 월말+n 거래일에 판정·체결 (달력 민감도 검정용)
    if exec_at=='next_close': rb=set(i+1 for i in rb if i+1<t1)   # 판정은 전날(월말) 종가, 체결은 오늘 종가
    # 'prev_judge': 월말 전날 종가로 판정, 월말 종가에 체결 (20:10 판정 → 다음날 15:21 매수 흐름에 맞춤)
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
            tj=t-1 if exec_at in ('next_close','prev_judge') else t
            if regv[tj]:
                R=rank_today(W,dates[tj])
                if need_above62: R=R[R.close>R.ma62]
                if capital0 is not None: R=R[R.close<=capital0*eq/top*1.5]
                R=R.iloc[skip:skip+top]; want={ci[c] for c in R.code if not np.isnan(Cv[t,ci[c]]) and not np.isnan(Ov[t,ci[c]])}
                for k in list(pos):
                    if replace and k not in want: close_pos(k,t,Cv[t,k],'월말 순위 밖')
                eq=cash+sum(s['sh']*Cv[t,k] for k,s in pos.items() if not np.isnan(Cv[t,k]))
                for k in want:
                    if k in pos or len(pos)>=top: continue
                    amt=min(eq/top,cash)
                    if amt>0: pos[k]=dict(sh=amt*(1-BUY_COST)/Cv[t,k],t=t,px=Cv[t,k],cost=amt); cash-=amt
                holdings.append(dict(날짜=dates[t].date(),국면='상승',종목=', '.join(f"{codes[k]} {W['names'].get(codes[k],'')}" for k in sorted(pos))))
            else:
                if regime_liquidate:
                    for k in list(pos): close_pos(k,t,Cv[t,k],'국면 종료 (전량 현금)')
                holdings.append(dict(날짜=dates[t].date(),국면='하락 (현금)' if regime_liquidate else '하락 (보유 유지)',종목=', '.join(codes[k] for k in sorted(pos))))
        elif ma_exit:
            for k in list(pos):
                if not np.isnan(Cv[t,k]) and Cv[t,k]<Mv[t,k] and k not in pend_sell: pend_sell.append(k)
        eq=cash+sum(s['sh']*Cv[t,k] for k,s in pos.items() if not np.isnan(Cv[t,k]))
        curve.append((dates[t],eq,len(pos)))
    cv=pd.DataFrame(curve,columns=['date','eq','npos'])
    return pd.DataFrame(trades),cv,pd.DataFrame(holdings)

# ---------------- 자동매매용 (B 모듈) ----------------
def load_universe(path=None):
    """운용 유니버스 파일 (code,name). 기본: universe_B.csv 가 있으면 그 종목, 없으면 None = 데이터에 있는 전 종목(코스피200 + 코스닥150).
    코스피200 으로 한정하려면 universe_kospi200.csv 를 universe_B.csv 로 복사"""
    path=path or os.path.join(BASE,'universe_B.csv')
    if not os.path.exists(path): return None
    u=pd.read_csv(path,dtype={'code':str}); return [c.zfill(6) for c in u.code]

def load_positions(posf=None):
    posf=posf or os.path.join(BASE,'positions_B.csv')
    if not os.path.exists(posf): return pd.DataFrame(columns=['code','name','매수일','매수가','수량'])
    p=pd.read_csv(posf,dtype={'code':str}); p['code']=p['code'].str.zfill(6); return p

def judge(W=None, df=None, date=None, posf=None, capital=None, top=TOP, universe=None, rebalance=False, k200_csv=None):
    """B 모듈 구조화 판정. 출력 없음, dict 반환.
    W/df: wide() 결과 또는 load_prices() 결과 (둘 다 None 이면 기본 데이터 로드)
    date: 판정 기준일 (None = 데이터 마지막 날짜)
    posf: positions_B.csv (code,name,매수일,매수가,수량)
    capital: B 몫 평가자산 (현금 + 보유 평가액). 주면 buys 에 amount, qty 를 채움
    universe: code 리스트 (None 이면 universe_kospi200.csv, 그것도 없으면 전 종목)
    rebalance: True 면 월말 교체 계획(sells_rebalance, buys) 을 만든다. False 면 매일 점검(62일선 이탈 매도)만
    반환: date, regime_on, ma62, ma62_20ago, k200_close, rank(DataFrame 상위 후보), positions(DataFrame + exit_signal), sells_daily, sells_rebalance, buys, per_slot, mode"""
    if W is None:
        if df is None: df=load_prices()
        W=wide(df)
    C=W['C']; O=W['O']; amt=W['amt20']; names=W['names']
    univ=universe if universe is not None else load_universe()
    if univ is not None:
        keep=C.columns.isin(univ); amt=amt.copy(); amt.loc[:,~keep]=0
    t=C.index[-1] if date is None else pd.Timestamp(date)
    Cj=C; Oj=O
    ma62=Cj.rolling(62).mean(); r120=Cj/Cj.shift(LOOK)-1
    reg,kma62=k200_regime(k200_csv); on=bool(reg.iloc[-1])
    ok=(amt.loc[t]>=MIN_AMT)&r120.loc[t].notna()&Oj.loc[t].notna()
    s=r120.loc[t][ok].sort_values(ascending=False)
    rank=pd.DataFrame({'code':s.index,'name':[names.get(c,'') for c in s.index],'r120':s.values,'close':Cj.loc[t,s.index].values,'ma62':ma62.loc[t,s.index].values,'amt20억':amt.loc[t,s.index].values/1e8}).reset_index(drop=True)
    rank.index=rank.index+1
    pos=load_positions(posf); rows=[]
    for _,r in pos.iterrows():
        c=r.code
        if c not in Cj.columns: rows.append(dict(code=c,name=r.get('name',''),매수일=r.get('매수일',''),매수가=float(r.매수가),수량=int(r.수량),종가=np.nan,ma62=np.nan,pnl_pct=np.nan,exit_signal=False,rank=None,action='데이터 없음')); continue
        px=float(Cj.loc[t,c]); m=float(ma62.loc[t,c]); ex=bool(px<m)
        rk=int(rank.index[rank.code==c][0]) if (rank.code==c).any() else None
        rows.append(dict(code=c,name=r.get('name',names.get(c,'')),매수일=r.get('매수일',''),매수가=float(r.매수가),수량=int(r.수량),종가=px,ma62=m,pnl_pct=px/float(r.매수가)-1,exit_signal=ex,rank=rk,action='매도 (62일선 이탈) → 내일 시가' if ex else '보유'))
    P=pd.DataFrame(rows)
    held=set(P.code) if len(P) else set()
    sells_daily=P[P.exit_signal].copy() if len(P) else P
    out=dict(date=t,regime_on=on,ma62=float(kma62.iloc[-1]),ma62_20ago=float(kma62.iloc[-21]),k200_close=float(pd.read_csv(k200_csv or os.path.join(BASE,'k200.csv'),parse_dates=['date']).close.iloc[-1]),
             rank=rank,positions=P,sells_daily=sells_daily,sells_rebalance=P.iloc[0:0].copy() if len(P) else P,buys=rank.iloc[0:0].copy(),per_slot=(capital/top if capital else None),mode='daily')
    if rebalance:
        out['mode']='rebalance'
        if not on:
            out['sells_rebalance']=P.copy() if len(P) else P; out['sells_rebalance']['why']='국면 하락 (전량 현금)'
        else:
            want=list(rank.head(top).code)
            drop=P[~P.code.isin(want)].copy() if len(P) else P
            if len(drop): drop['why']='월말 순위 밖'
            out['sells_rebalance']=drop
            buys=rank[rank.code.isin(want)&~rank.code.isin(held)].copy()
            if capital:
                per=capital/top; buys['amount']=per; q=np.floor(per/buys['close']).astype(int)
                q=np.where((q<1)&(buys['close']<=per*1.5),1,q); buys['qty']=q
                buys['skip']=np.where(buys['close']>per*1.5,'1주 가격이 종목당 금액의 150% 초과 → 건너뜀, 다음 순위로 대체','')
                nxt=rank[~rank.code.isin(held)&~rank.code.isin(want)].copy(); nxt=nxt[nxt['close']<=per*1.5]
                need=int((buys['skip']!='').sum())
                if need and len(nxt):
                    add=nxt.head(need).copy(); add['amount']=per; qa=np.floor(per/add['close']).astype(int); add['qty']=np.where((qa<1),1,qa); add['skip']='대체 후보 (상위 10 중 고가 종목 대신)'
                    buys=pd.concat([buys,add])
            out['buys']=buys
    return out
