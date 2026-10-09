"""운용 수단 비교: 미니 1계약(손절 좁힘) vs ETF 1배 vs ETF 2배 - 13패턴 동일, 갭 시가 체결, 세금·비용 반영"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys
sys.path.insert(0,'.')
from lib import build
from hunt import make

SEL=[('F0.309dn','ma62dn'),('F0.764dn','gap_ma21n'),('GC5_21','ma62up+r5>0'),('LL20dn','ma62up+ma200up'),
     ('MA21dn','ma200dn+align'),('MA21dn_S','r5>0+r20>0'),('MA21up','atrq0.33-0.66+r5<0'),
     ('MA62up','ma62up+rsi40-60'),('MACDgc','ma62up'),('MHup','ma62up'),('RSI50up','ma62up+mh<0'),
     ('dn3','C>ma200+r60<0'),('up3','ma62up+mh>0')]

def patterns(x):
    T,S=make(x); P={}
    for vn,st in SEL:
        dr=-1 if vn.endswith('_S') else 1; tk=vn[:-2] if vn.endswith('_S') else vn
        tm,exfn=T[tk]; m=tm.copy(); m[:300]=False
        for a in st.split('+'): m=m&S[a]
        P[f'{vn}|{st}']=dict(ent=m,ex=exfn,dr=dr)
    return P

def sim(x,P,mode,stop='atr',stopk=5.0,stoppct=None,wfix=None,risk=0.03,navcap=1.5,lev=1,
        tax=None,cost=0.0001,fee_yr=0.0,start=300,end=None,maxpos=5,inv=0):
    """mode 'fut': 종가 진입·종가 청산, 롱/숏.   mode 'etf': 다음날 시가 진입·다음날 시가 청산, 롱만, 일간 lev배 복리
       wfix: 노출 고정(1계약 ≈ 1.25)   tax: ('annual',0.10) 연 순익 과세 / ('trade',0.154) 건별 이익 과세(손실 상계 없음)"""
    C,O,LO,HI,atr,idx=x['C'],x['O'],x['LO'],x['HI'],x['atr'],x['idx']; N=len(C) if end is None else end
    ret=np.append(0,np.diff(C)/C[:-1]); fee_d=fee_yr/250
    def etf_factor(i0,j,px,L=None):
        L=lev if L is None else L
        if j==i0: return 1+L*(px/O[i0]-1)
        f=1+L*(C[i0]/O[i0]-1)-fee_d
        for t in range(i0+1,j): f*=1+L*ret[t]-fee_d
        return f*(1+L*(px/C[j-1]-1)-fee_d)
    def book(s,i,px,why):
        nonlocal eq
        if mode=='etf': r=etf_factor(s['i'],i,px,(-inv if s['dr']==-1 else lev))-1
        else: r=(px-s['e'])/s['e']*s['dr']
        r-=2*cost; g=r*s['w']/((inv if s['dr']==-1 else lev) if mode=='etf' else 1)
        if tax and tax[0]=='trade' and g>0: g*=1-tax[1]
        eq*=1+g; tr.append(dict(date=idx[i],pat=s['k'],r=r,w=s['w'],pnl=g,why=why,days=i-s['i']))
    open_=[]; eq=1.0; tr=[]; cv=[]; pend=[]; yr_start=1.0; last_year=idx[start].year
    for i in range(start,N):
        if idx[i].year!=last_year:
            if tax and tax[0]=='annual':
                g=eq/yr_start-1
                if g>0: eq*=1-tax[1]*g
            yr_start=eq; last_year=idx[i].year
        if mode=='etf':
            for s in [s for s in open_ if s.get('exit_pending')]:
                book(s,i,O[i],'이유소멸'); open_.remove(s)
            for k in pend:
                p=P[k]; dr=p['dr']
                if dr==-1 and inv==0: continue
                L=inv if dr==-1 else lev
                sd=(stopk*atr[i-1]/O[i]) if stop=='atr' else stoppct
                w=min(risk/sd,navcap) if wfix is None else wfix
                cash=sum(s['w']/(inv if s['dr']==-1 else lev) for s in open_)
                if cash+w/L>1.0 or sum(s['w'] for s in open_)+w>navcap or len(open_)>=maxpos: continue
                open_.append(dict(k=k,i=i,e=O[i],st=O[i]*(1-dr*sd),w=w,dr=dr))
            pend=[]
        keep=[]
        for s in open_:
            p=P[s['k']]; dr=s['dr']; px=why=None
            if dr==1 and LO[i]<=s['st']: px=min(s['st'],O[i]); why='손절'
            elif dr==-1 and HI[i]>=s['st']: px=max(s['st'],O[i]); why='손절'
            elif p['ex'](s['i'],i) and i>s['i']:
                if mode=='etf': s['exit_pending']=True; keep.append(s); continue
                px=C[i]; why='이유소멸'
            if px is None: keep.append(s); continue
            book(s,i,px,why)
        open_=keep
        held={s['k'] for s in open_}
        for k,p in P.items():
            if not p['ent'][i] or k in held: continue
            if mode=='etf': pend.append(k); continue
            dr=p['dr']; sd=(stopk*atr[i]/C[i]) if stop=='atr' else stoppct
            w=min(risk/sd,navcap) if wfix is None else wfix
            if sum(s['w'] for s in open_)+w>navcap or len(open_)>=maxpos: continue
            open_.append(dict(k=k,i=i,e=C[i],st=C[i]*(1-dr*sd),w=w,dr=dr))
        cv.append((idx[i],eq,sum(s['w'] for s in open_)))
    return pd.DataFrame(tr),pd.DataFrame(cv,columns=['date','eq','nav'])

def rep(cv,tr,lab,w=36):
    y=(cv['date'].iloc[-1]-cv['date'].iloc[0]).days/365.25
    e=cv['eq'].values; cg=e[-1]**(1/y)-1 if e[-1]>0 else -1
    mdd=(e/np.maximum.accumulate(e)-1).min()
    rr=cv['eq'].pct_change().dropna(); sh=rr.mean()/rr.std()*np.sqrt(250) if rr.std()>0 else 0
    yr=cv.assign(r=cv['eq'].pct_change().fillna(0)).groupby(cv['date'].dt.year).apply(lambda g:(1+g.r).prod()-1)
    stp=(tr.why=='손절').mean() if len(tr) else 0
    print(f"{lab:<{w}}{len(tr):>5}{cg:>8.1%}{mdd:>8.1%}{sh:>7.2f}{cv['nav'].mean():>7.0%}{(tr.r>0).mean():>6.0%}{stp:>6.0%}{tr.r.mean():>8.2%}{(yr>0).sum():>3}/{len(yr)}{yr.min():>8.1%}")
    return dict(cagr=cg,mdd=mdd,sh=sh,worst=yr.min())
HDR=f"{'':<36}{'거래':>5}{'CAGR':>8}{'MDD':>8}{'Sharpe':>7}{'노출':>7}{'승률':>6}{'손절':>6}{'기대값':>8}{'플러스':>6}{'최악연':>8}"
