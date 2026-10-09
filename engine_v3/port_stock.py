"""개별 종목 포트폴리오 시뮬 (일간 평가): 슬롯 M개 균등, 신호 다음날 종가 매수, 청산 신호 다음날 시가 매도, 손절 장중"""
import warnings; warnings.filterwarnings('ignore')
import sys, os, pickle; BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
K200=os.path.join(BASE,'k200.csv')
import numpy as np, pandas as pd
from stock_lib import build_panel, patterns_for
BUY_COST=0.0010; SELL_COST=0.0025   # 수수료+슬리피지 / 수수료+세금+슬리피지
def load_arrays(selected, min_amt=3e9):
    P=build_panel(); codes=list(P.index.get_level_values(0).unique())
    dates=np.array(sorted(P.index.get_level_values(1).unique()))
    D=len(dates); K=len(codes); di={d:i for i,d in enumerate(dates)}
    O=np.full((D,K),np.nan); H=O.copy(); L=O.copy(); C=O.copy(); ATR=O.copy(); AMT=O.copy(); R120=O.copy(); ATRQ=O.copy()
    ENT={p:np.zeros((D,K),bool) for p in selected}; EX={p:np.zeros((D,K),bool) for p in selected}
    ALO={p:np.full((D,K),np.nan) for p in selected}; AHI={p:np.full((D,K),np.nan) for p in selected}; SPEC={}
    names={}
    for k,code in enumerate(codes):
        x=P.loc[code]; names[code]=x['name'].iloc[0]
        idx=np.array([di[d] for d in x.index])
        for arr,col in ((O,'O'),(H,'H'),(L,'L'),(C,'C'),(ATR,'atr'),(AMT,'amt20'),(R120,'r120'),(ATRQ,'atrq')): arr[idx,k]=x[col].values
        sp=patterns_for(x)
        for p in selected:
            if p not in sp: continue
            s=sp[p]; e=s['ent'].copy(); e[:300]=False
            ENT[p][idx,k]=e; EX[p][idx,k]=s['ex']; ALO[p][idx,k]=s['alo']; AHI[p][idx,k]=s['ahi']
            SPEC[p]=dict(stopk=s['stopk'],stopcap=s['stopcap'],maxhold=s['maxhold'])
    return dict(dates=dates,codes=codes,names=names,O=O,H=H,L=L,C=C,ATR=ATR,AMT=AMT,R120=R120,ATRQ=ATRQ,ENT=ENT,EX=EX,ALO=ALO,AHI=AHI,SPEC=SPEC,min_amt=min_amt)

def market_regime(dates):
    k2=pd.read_csv(K200,parse_dates=['date']).set_index('date').sort_index()
    ma62=k2.close.rolling(62).mean(); up=(ma62.diff(20)>0)
    return up.reindex(pd.DatetimeIndex(dates)).fillna(False).values

def simulate(A, M=10, regime=None, rank='r120', start='2017-10-01', end=None, seed=0, prio=None, pyramid=None, frac=None, panic=None, min_amt=None, topn=None, stock_exit=None, trail=None, partial=None, breakeven=None, exit_at='open'):
    """regime: bool array by date (True면 신규 진입 허용). rank: 'r120'|'random'|'atrq'. pyramid: None 또는 (f1,f2,f3,k1,k2) - 초기 f1, +1·k1 ATR에 f2, +k2 ATR에 f3
       exit_at: 'open'(청산 신호 다음날 시가) / 'close'(다음날 종가. 그날 장중 손절은 그대로 적용)"""
    dates,codes=A['dates'],A['codes']; D,K=len(dates),len(codes)
    O,H,L,C,ATR,AMT,R120,ATRQ=A['O'],A['H'],A['L'],A['C'],A['ATR'],A['AMT'],A['R120'],A['ATRQ']
    pats=list(A['ENT']); rng=np.random.default_rng(seed)
    t0=int(np.searchsorted(dates,np.datetime64(pd.Timestamp(start)))); t1=D if end is None else int(np.searchsorted(dates,np.datetime64(pd.Timestamp(end))))
    frac=1.0/M if frac is None else frac   # float 또는 {패턴: 비중}
    cash=1.0; pos={}  # code idx -> dict(p,shares,entry_t,entry_px,stop,cost_basis,stage)
    pend_buy=[]; pend_sell=[]; trades=[]; curve=[]
    anyent=np.zeros((D,K),bool)
    for p in pats: anyent|=A['ENT'][p]
    for t in range(t0,t1):
        # 1) 시가: 청산 대기분 매도
        for k,s in list(pos.items()):
            if s.get('half_pend') and not np.isnan(O[t,k]):
                v=s['shares']*0.5*O[t,k]*(1-SELL_COST); cash+=v; s['shares']*=0.5; s['cost']*=0.5; s['half_pend']=False
                trades.append(dict(code=codes[k],pat=s['p'],pats='|'.join(s['pl']),sig=dates[s['sig_t']],entry=dates[s['entry_t']],entry_px=s['entry_px'],exit=dates[t],exit_px=O[t,k],stop=s['stop'],r=v/s['cost']-1,hold=t-s['entry_t'],why='절반 익절'))
        if exit_at=='open':
            keep=[]
            for k in pend_sell:
                if k not in pos: continue
                if np.isnan(O[t,k]): keep.append(k); continue     # 거래정지: 다음 거래 가능일 시가에
                s=pos.pop(k); v=s['shares']*O[t,k]*(1-SELL_COST); cash+=v
                trades.append(dict(code=codes[k],pat=s['p'],pats='|'.join(s['pl']),sig=dates[s['sig_t']],entry=dates[s['entry_t']],entry_px=s['entry_px'],exit=dates[t],exit_px=O[t,k],stop=s['stop'],r=v/s['cost']-1,hold=t-s['entry_t'],why=s['why']))
            pend_sell=keep
        # 2) 장중 손절
        for k in list(pos):
            s=pos[k]
            if np.isnan(L[t,k]): continue
            if L[t,k]<=s['stop']:
                px=s['stop'] if O[t,k]>s['stop'] else O[t,k]; v=s['shares']*px*(1-SELL_COST); cash+=v; pos.pop(k)
                trades.append(dict(code=codes[k],pat=s['p'],pats='|'.join(s['pl']),sig=dates[s['sig_t']],entry=dates[s['entry_t']],entry_px=s['entry_px'],exit=dates[t],exit_px=px,stop=s['stop'],r=v/s['cost']-1,hold=t-s['entry_t'],why='손절'))
        if exit_at=='close':
            # 2b) 종가: 청산 대기분 매도 (장중 손절에 먼저 걸리면 손절로 끝남)
            keep=[]
            for k in pend_sell:
                if k not in pos: continue
                if np.isnan(C[t,k]) or np.isnan(O[t,k]): keep.append(k); continue
                s=pos.pop(k); v=s['shares']*C[t,k]*(1-SELL_COST); cash+=v
                trades.append(dict(code=codes[k],pat=s['p'],pats='|'.join(s['pl']),sig=dates[s['sig_t']],entry=dates[s['entry_t']],entry_px=s['entry_px'],exit=dates[t],exit_px=C[t,k],stop=s['stop'],r=v/s['cost']-1,hold=t-s['entry_t'],why=s['why']))
            pend_sell=keep
        # 3) 종가: 매수 대기분 체결 (슬롯 기준 자본 = 현재 평가자산)
        eq_now=cash+sum(s['shares']*C[t,k] for k,s in pos.items() if not np.isnan(C[t,k]))
        for (k,p,sig_t,pl_all) in pend_buy:
            if k in pos or np.isnan(C[t,k]) or np.isnan(O[t,k]) or len(pos)>=M: continue
            sp=A['SPEC'][p]; sd=sp['stopk']*ATR[sig_t,k]
            if sp['stopcap'] is not None: sd=min(sd,sp['stopcap']*C[sig_t,k])
            fr=(frac.get(p,1.0/M) if isinstance(frac,dict) else frac)
            amt=eq_now*fr*(pyramid[0] if pyramid else 1.0)
            if amt>cash: amt=cash
            if amt<=eq_now*0.01: continue
            sh=amt*(1-BUY_COST)/C[t,k]; cash-=amt
            pos[k]=dict(p=p,pl=pl_all,shares=sh,entry_t=t,sig_t=sig_t,entry_px=C[t,k],stop=C[sig_t,k]-sd,cost=amt,why='',stage=1,target=eq_now*fr)
        pend_buy=[]
        # 3b) 피라미딩: 보유 중 가격이 올라오면 추가 매수 (종가)
        if pyramid:
            f1,f2,f3,k1,k2=pyramid
            for k,s in pos.items():
                if np.isnan(C[t,k]): continue
                a=ATR[s['sig_t'],k]
                if s['stage']==1 and C[t,k]>=s['entry_px']+k1*a:
                    amt=min(s['target']*f2,cash)
                    if amt>0: s['shares']+=amt*(1-BUY_COST)/C[t,k]; s['cost']+=amt; cash-=amt
                    s['stage']=2
                elif s['stage']==2 and C[t,k]>=s['entry_px']+k2*a:
                    amt=min(s['target']*f3,cash)
                    if amt>0: s['shares']+=amt*(1-BUY_COST)/C[t,k]; s['cost']+=amt; cash-=amt
                    s['stage']=3
        # 4) 종가: 청산 판정
        for k,s in pos.items():
            if np.isnan(C[t,k]): continue
            p=s['p']; st=s['sig_t']; hit=A['EX'][p][t,k]
            if panic is not None and panic[t]: hit=True; s['why']='시장'
            if stock_exit is not None and stock_exit[t,k]: hit=True; s['why']='추세이탈'
            if trail is not None:
                s['hi']=max(s.get('hi',s['entry_px']),C[t,k])
                if C[t,k]<s['hi']*(1-trail): hit=True; s['why']='추적손절'
            if breakeven is not None and not s.get('be') and C[t,k]>=s['entry_px']*(1+breakeven): s['stop']=max(s['stop'],s['entry_px']); s['be']=True
            if partial is not None and not s.get('half') and C[t,k]>=s['entry_px']*(1+partial):
                s['half']=True; s['half_pend']=True
            if not hit and not np.isnan(A['ALO'][p][st,k]) and C[t,k]<A['ALO'][p][st,k]: hit=True
            if not hit and not np.isnan(A['AHI'][p][st,k]) and C[t,k]>A['AHI'][p][st,k]: hit=True
            if not hit and t-s['entry_t']>=A['SPEC'][p]['maxhold']: hit=True; s['why']='시간'
            if hit and k not in pend_sell:
                if not s['why']: s['why']='이유소멸'
                pend_sell.append(k)
        # 5) 종가: 신규 신호 -> 다음날 종가 매수 대기
        free=M-len(pos)+len(pend_sell)-0  # 내일 시가에 나갈 자리는 내일 종가에 쓸 수 있음
        if (regime is None or regime[t]) and free>0:
            cand=np.where(anyent[t]&(AMT[t]>=(A['min_amt'] if min_amt is None else min_amt))&~np.isnan(O[t]))[0]
            cand=[k for k in cand if k not in pos]
            if topn is not None and cand:
                liq=np.where((AMT[t]>=(A['min_amt'] if min_amt is None else min_amt))&~np.isnan(R120[t]))[0]
                top=set(liq[np.argsort(-R120[t,liq])[:topn]]); cand=[k for k in cand if k in top]
            if cand:
                if rank=='r120': sc=np.nan_to_num(R120[t,cand],nan=-9); order=np.argsort(-sc)
                elif rank=='atrq': sc=np.nan_to_num(ATRQ[t,cand],nan=9); order=np.argsort(sc)
                else: order=rng.permutation(len(cand))
                for oi in order[:free]:
                    k=cand[oi]
                    pl=[p for p in pats if A['ENT'][p][t,k]]
                    if prio: pl=sorted(pl,key=lambda p: prio.index(p) if p in prio else 99)
                    pend_buy.append((k,pl[0],t,pl))
        eq=cash+sum(s['shares']*C[t,k] for k,s in pos.items() if not np.isnan(C[t,k]))
        curve.append((dates[t],eq,len(pos)))
    cv=pd.DataFrame(curve,columns=['date','eq','npos']); cv['date']=pd.to_datetime(cv.date)
    return pd.DataFrame(trades),cv

def stats(cv,tr,lab,w=44):
    y=(cv.date.iloc[-1]-cv.date.iloc[0]).days/365.25; e=cv['eq'].values
    cg=(e[-1]/e[0])**(1/y)-1; mdd=(e/np.maximum.accumulate(e)-1).min(); rr=cv['eq'].pct_change().dropna(); sh=rr.mean()/rr.std()*np.sqrt(250) if rr.std()>0 else 0
    yr=cv.assign(r=cv['eq'].pct_change().fillna(0)).groupby(cv.date.dt.year).apply(lambda g:(1+g.r).prod()-1)
    n=len(tr); win=(tr.r>0).mean() if n else 0; hold=tr.hold.mean() if n else 0; stp=(tr.why=='손절').mean() if n else 0
    print(f"{lab:<{w}}{n:>5}{cg:>8.1%}{mdd:>8.1%}{sh:>7.2f}{(cv.npos>0).mean():>6.0%}{cv.npos.mean():>5.1f}{win:>6.0%}{hold:>5.0f}{stp:>5.0%}{yr.min():>8.1%}{(yr>0).sum():>3}/{len(yr)}{e[-1]/e[0]:>7.1f}배")
    return dict(cagr=cg,mdd=mdd,sh=sh,yr=yr,n=n)
HDR=f"{'':<44}{'거래':>5}{'CAGR':>8}{'MDD':>8}{'Sharpe':>7}{'투자중':>6}{'보유수':>5}{'승률':>6}{'보유':>5}{'손절':>5}{'최악연':>8}{'플러스':>6}{'최종':>8}"

def bench(A,start='2017-10-01',end=None):
    """유니버스 동일가중(일별 리밸런싱, 유동성 필터) 과 코스피200"""
    dates=A['dates']; C=A['C']; AMT=A['AMT']
    t0=int(np.searchsorted(dates,np.datetime64(pd.Timestamp(start)))); t1=len(dates) if end is None else int(np.searchsorted(dates,np.datetime64(pd.Timestamp(end))))
    r=C[1:]/C[:-1]-1; r=np.vstack([np.zeros(C.shape[1]),r])
    ok=(AMT>=A['min_amt'])&~np.isnan(C)
    ew=np.array([np.nanmean(np.where(ok[t-1],r[t],np.nan)) if ok[t-1].any() else 0 for t in range(t0,t1)])
    ew=np.nan_to_num(ew); cv=pd.DataFrame({'date':pd.to_datetime(dates[t0:t1]),'eq':np.cumprod(1+ew),'npos':1})
    k2=pd.read_csv(K200,parse_dates=['date']).set_index('date').close.reindex(pd.to_datetime(dates[t0:t1])).ffill()
    ck=pd.DataFrame({'date':k2.index,'eq':(k2/k2.iloc[0]).values,'npos':1})
    return cv,ck
