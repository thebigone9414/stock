"""KODEX 레버리지 단일 포지션 100% + 새 신호 나오면 그 전략으로 청산 기준 교체(롤) - 일간 평가(MTM) 기준
   비교용: 고정 비중 다중 포지션도 일간 평가로 재계산"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys
sys.path.insert(0,'.')
from lib import build
from vehicle import patterns

def prep(x,fee_yr=0.0064):
    C,O=x['C'],x['O']; ret=np.append(0,np.diff(C)/C[:-1]); fee=fee_yr/250
    G={}
    for L in (1,2,-1,-2):
        g=np.cumprod(1+L*ret-fee); G[L]=g
    return ret,fee,G

def factor_close(G,L,fee,O,C,e,t):
    """e일 시가 매수 → t일 종가 평가 배율"""
    if t<e: return 1.0
    f0=1+L*(C[e]/O[e]-1)-fee
    return f0*G[L][t]/G[L][e]
def factor_px(G,L,fee,O,C,e,j,px):
    """e일 시가 매수 → j일 가격 px(시가 또는 손절가)에서 매도 배율"""
    if j==e: return 1+L*(px/O[e]-1)-fee
    return factor_close(G,L,fee,O,C,e,j-1)*(1+L*(px/C[j-1]-1)-fee)

def roll_single(x,P,lev=2,stopk=5.0,roll='diff',short='ignore',inv=2,cost=0.0002,fee_yr=0.0064,start=300,end=None,prio=None,stoppct=None,keep_stop=False,stopcap=None):
    C,O,LO,HI,atr,idx=x['C'],x['O'],x['LO'],x['HI'],x['atr'],x['idx']; N=len(C) if end is None else end
    def SD(i):
        d=(stopk*atr[i]) if stoppct is None else stoppct*C[i]
        return min(d,stopcap*C[i]) if stopcap is not None else d
    ret,fee,G=prep(x,fee_yr)
    keys=list(P) if prio is None else prio
    longs=[k for k in keys if P[k]['dr']==1]; shorts=[k for k in keys if P[k]['dr']==-1]
    eq=1.0; pos=None; pend=None; ev=[]; curve=[]; trades=[]
    def val(i,px=None):
        L=pos['L']
        return pos['cash']*(factor_close(G,L,fee,O,C,pos['e'],i) if px is None else factor_px(G,L,fee,O,C,pos['e'],i,px))
    for i in range(start,N):
        # 1) 시가: 전일 종가 결정분 실행
        if pend is not None:
            act,k=pend; pend=None
            if act=='sell' and pos is not None:
                v=val(i,O[i])*(1-cost); trades.append(dict(date=idx[i],pat=pos['g'],dirn=pos['dr'],r=v/pos['cash0']-1,pnl=v-pos['cash0'],why='이유소멸',days=i-pos['e'],rolls=pos['rolls'],chain=pos['chain'],edate=idx[pos['e']],e_px=pos['e_px'],x_px=O[i]))
                ev.append((idx[i],'매도',O[i],pos['g'])); eq=v; pos=None
            if act=='flip' and pos is not None:
                v=val(i,O[i])*(1-cost); trades.append(dict(date=idx[i],pat=pos['g'],dirn=pos['dr'],r=v/pos['cash0']-1,pnl=v-pos['cash0'],why='전환',days=i-pos['e'],rolls=pos['rolls'],chain=pos['chain'],edate=idx[pos['e']],e_px=pos['e_px'],x_px=O[i]))
                ev.append((idx[i],'매도(전환)',O[i],pos['g'])); eq=v; pos=None; act='buy'
            if act=='buy' and pos is None:
                dr=P[k]['dr']; L=lev if dr==1 else -inv
                cash=eq*(1-cost); pos=dict(g=k,a=k_anchor,e=i,dr=dr,L=L,cash=cash,cash0=cash,stop=stop_pend,rolls=0,chain=[(idx[k_anchor].date(),k)],e_px=O[i])
                ev.append((idx[i],'매수' if dr==1 else '인버스 매수',O[i],k))
        # 2) 장중 손절
        if pos is not None:
            hit=(LO[i]<=pos['stop']) if pos['dr']==1 else (HI[i]>=pos['stop'])
            if hit:
                px=min(pos['stop'],O[i]) if pos['dr']==1 else max(pos['stop'],O[i])
                v=val(i,px)*(1-cost); trades.append(dict(date=idx[i],pat=pos['g'],dirn=pos['dr'],r=v/pos['cash0']-1,pnl=v-pos['cash0'],why='손절',days=i-pos['e'],rolls=pos['rolls'],chain=pos['chain'],edate=idx[pos['e']],e_px=pos['e_px'],x_px=px))
                ev.append((idx[i],'손절',px,pos['g'])); eq=v; pos=None
        # 3) 종가: 신호 판정
        fl=[k for k in longs if P[k]['ent'][i]]; fs=[k for k in shorts if P[k]['ent'][i]] if short=='flip' else []
        if pos is not None:
            if pos['dr']==1:
                new=[k for k in fl if (k!=pos['g'] if roll=='diff' else True)] if roll!='none' else []
                if fs: pend=('flip',fs[0]); k_anchor=i; stop_pend=C[i]+SD(i)
                elif new:
                    pos['g']=new[0]; pos['a']=i; ns=C[i]-SD(i); pos['stop']=max(pos['stop'],ns) if keep_stop else ns; pos['rolls']+=1; pos['chain'].append((idx[i].date(),new[0])); ev.append((idx[i],'롤',C[i],new[0]))
                elif i>pos['a'] and P[pos['g']]['ex'](pos['a'],i): pend=('sell',None)
            else:
                if fl: pend=('flip',fl[0]); k_anchor=i; stop_pend=C[i]-SD(i)
                elif i>pos['a'] and P[pos['g']]['ex'](pos['a'],i): pend=('sell',None)
        else:
            if fl: pend=('buy',fl[0]); k_anchor=i; stop_pend=C[i]-SD(i)
            elif fs: pend=('buy',fs[0]); k_anchor=i; stop_pend=C[i]+SD(i)
        curve.append((idx[i],eq if pos is None else val(i),pos is not None))
    return pd.DataFrame(trades),pd.DataFrame(curve,columns=['date','eq','inpos']),ev

def multi_mtm(x,P,lev=2,cash_frac=0.2,maxpos=5,stopk=5.0,inv=1,cost=0.0002,fee_yr=0.0064,start=300,end=None,risk=None,navcap=1.5,stoppct=None,stopcap=None,close_entry=(),entry_at='open',exit_at='open',stop_at='intraday'):
    """entry_at: 'open'(신호 다음날 시가) / 'next_close'(다음날 종가) / 'next_close_if_up'(다음날 종가가 신호일 종가보다 높을 때만)
       exit_at : 'open'(청산신호 다음날 시가) / 'next_close'(다음날 종가)
       stop_at : 'intraday'(장중 손절선 터치 시 손절선 체결, 갭이면 시가) / 'close'(종가가 손절선을 넘으면 다음날 시가 매도 - 장중 감시 없음)"""
    """고정 비중 다중 포지션, 일간 평가"""
    C,O,LO,HI,atr,idx=x['C'],x['O'],x['LO'],x['HI'],x['atr'],x['idx']; N=len(C) if end is None else end
    ret,fee,G=prep(x,fee_yr)
    free=1.0; open_=[]; pend=[]; pend_ex=[]; trades=[]; curve=[]
    def v(s,i,px=None):
        if s.get('fc'):   # 신호일 종가 진입: e일 종가 기준
            e=s['e']; L=s['L']
            if px is None: return s['cash']*(G[L][i]/G[L][e] if i>e else 1.0)
            base=(G[L][i-1]/G[L][e]) if i-1>e else 1.0
            return s['cash']*base*(1+L*(px/C[i-1]-1)-fee)
        return s['cash']*(factor_close(G,s['L'],fee,O,C,s['e'],i) if px is None else factor_px(G,s['L'],fee,O,C,s['e'],i,px))
    pend_ex_c=[]; pend_c=[]
    for i in range(start,N):
        if exit_at=='open':
            for s in pend_ex:
                if s in open_:
                    val=v(s,i,O[i])*(1-cost); free+=val; trades.append(dict(date=idx[i],pat=s['k'],r=val/s['cash']-1,pnl=val-s['cash'],why=s.get('why','이유소멸'),days=i-s['e'])); open_.remove(s)
            pend_ex=[]
        else:
            pend_ex_c=list(pend_ex); pend_ex=[]
        eq_prev=free+sum(v(s,i-1) for s in open_)
        if entry_at!='open': pend_c=list(pend); pend=[]
        for k,a in pend:
            if len(open_)>=maxpos or k in {s['k'] for s in open_}: continue
            dr=P[k]['dr']
            if dr==-1 and inv==0: continue
            if risk is not None:
                sd=stopk*atr[a]/C[a]; expo=min(risk/sd,navcap)
                if sum(s_['cash']*abs(s_['L']) for s_ in open_)/eq_prev+expo>navcap: continue
                cash=expo/(lev if dr==1 else inv)*eq_prev
            else:
                cf=cash_frac(k,i) if callable(cash_frac) else (cash_frac.get(k,0) if isinstance(cash_frac,dict) else cash_frac)
                if cf<=0: continue
                cash=cf*eq_prev*(1 if dr==1 else (lev/inv))
            if cash>free:
                if free>=0.5*cash: cash=free          # 남은 현금이 절반 이상이면 남은 만큼만
                else: continue
            L=lev if dr==1 else -inv
            sdist=(stopk*atr[a]) if stoppct is None else stoppct*C[a]
            if stopcap is not None: sdist=min(sdist,stopcap*C[a])
            free-=cash; open_.append(dict(k=k,a=a,e=i,dr=dr,L=L,cash=cash*(1-cost),stop=(C[a]-dr*sdist)))
        pend=[]
        keep=[]
        for s in open_:
            if stop_at=='close':
                if ((C[i]<=s['stop']) if s['dr']==1 else (C[i]>=s['stop'])) and s not in pend_ex: s['why']='손절'; pend_ex.append(s)
                elif i>s['a'] and P[s['k']]['ex'](s['a'],i) and s not in pend_ex: pend_ex.append(s)
                keep.append(s); continue
            hit=(LO[i]<=s['stop']) if s['dr']==1 else (HI[i]>=s['stop'])
            if hit:
                px=min(s['stop'],O[i]) if s['dr']==1 else max(s['stop'],O[i])
                val=v(s,i,px)*(1-cost); free+=val; trades.append(dict(date=idx[i],pat=s['k'],r=val/s['cash']-1,pnl=val-s['cash'],why='손절',days=i-s['e'])); continue
            if i>s['a'] and P[s['k']]['ex'](s['a'],i): pend_ex.append(s)
            keep.append(s)
        open_=keep
        held={s['k'] for s in open_}
        for k,p in P.items():
            if p['ent'][i] and k not in held:
                if k in close_entry:
                    if len(open_)>=maxpos: continue
                    dr=p['dr']
                    if dr==-1 and inv==0: continue
                    eq_now=free+sum(v(s_,i) for s_ in open_)
                    cf=cash_frac(k,i) if callable(cash_frac) else (cash_frac.get(k,0) if isinstance(cash_frac,dict) else cash_frac)
                    cash=cf*eq_now*(1 if dr==1 else (lev/inv))
                    if cash>free:
                        if free>=0.5*cash: cash=free
                        else: continue
                    sdist=(stopk*atr[i]) if stoppct is None else stoppct*C[i]
                    if stopcap is not None: sdist=min(sdist,stopcap*C[i])
                    free-=cash; open_.append(dict(k=k,a=i,e=i,dr=dr,L=(lev if dr==1 else -inv),cash=cash*(1-cost),stop=C[i]-dr*sdist,fc=True))
                else: pend.append((k,i))
        if exit_at!='open':
            for s in pend_ex_c:
                if s in open_:
                    val=v(s,i)*(1-cost); free+=val; trades.append(dict(date=idx[i],pat=s['k'],r=val/s['cash']-1,pnl=val-s['cash'],why=s.get('why','이유소멸'),days=i-s['e'])); open_.remove(s)
            pend_ex_c=[]
        if entry_at!='open':
            eq_now=free+sum(v(s,i) for s in open_)
            for k,a in pend_c:
                if len(open_)>=maxpos or k in {s_['k'] for s_ in open_}: continue
                dr=P[k]['dr']
                if dr==-1 and inv==0: continue
                if entry_at=='next_close_if_up' and not ((C[i]>C[a]) if dr==1 else (C[i]<C[a])): continue
                cf=cash_frac(k,i) if callable(cash_frac) else (cash_frac.get(k,0) if isinstance(cash_frac,dict) else cash_frac)
                cash=cf*eq_now*(1 if dr==1 else (lev/inv))
                if cash>free:
                    if free>=0.5*cash: cash=free
                    else: continue
                sdist=(stopk*atr[a]) if stoppct is None else stoppct*C[a]
                if stopcap is not None: sdist=min(sdist,stopcap*C[a])
                free-=cash; open_.append(dict(k=k,a=a,e=i,dr=dr,L=(lev if dr==1 else -inv),cash=cash*(1-cost),stop=C[a]-dr*sdist,fc=True))
            pend_c=[]
        curve.append((idx[i],free+sum(v(s,i) for s in open_),len(open_)))
    return pd.DataFrame(trades),pd.DataFrame(curve,columns=['date','eq','npos'])

def stats(cv,tr,lab,w=40):
    y=(cv['date'].iloc[-1]-cv['date'].iloc[0]).days/365.25
    e=cv['eq'].values; cg=e[-1]**(1/y)-1 if e[-1]>0 else -1
    mdd=(e/np.maximum.accumulate(e)-1).min()
    rr=cv['eq'].pct_change().dropna(); sh=rr.mean()/rr.std()*np.sqrt(250) if rr.std()>0 else 0
    yr=cv.assign(r=cv['eq'].pct_change().fillna(0)).groupby(cv['date'].dt.year).apply(lambda g:(1+g.r).prod()-1)
    tim=cv.iloc[:,2].astype(float); tim=(tim>0).mean()
    wt=(tr.r.min()) if len(tr) else 0; stp=(tr.why=='손절').mean() if len(tr) else 0
    print(f"{lab:<{w}}{len(tr):>5}{cg:>8.1%}{mdd:>8.1%}{sh:>7.2f}{tim:>6.0%}{(tr.r>0).mean() if len(tr) else 0:>6.0%}{stp:>6.0%}{wt:>8.1%}{(yr>0).sum():>3}/{len(yr)}{yr.min():>8.1%}{e[-1]:>8.0f}배")
    return yr
HDR=f"{'':<40}{'거래':>5}{'CAGR':>8}{'MDD':>8}{'Sharpe':>7}{'투자중':>6}{'승률':>6}{'손절':>6}{'최악1건':>8}{'플러스':>6}{'최악연':>8}{'최종':>9}"

def fut_mtm(x,P,risk=0.03,navcap=1.5,stopk=5.0,maxpos=5,cost=0.0001,start=300,end=None,wfix=None,stoppct=None):
    """선물(지수 1배, 종가 진입·종가 청산, 갭 시가 체결) - 일간 평가. 비중=노출(명목/자본)"""
    C,O,LO,HI,atr,idx=x['C'],x['O'],x['LO'],x['HI'],x['atr'],x['idx']; N=len(C) if end is None else end
    eq=1.0; open_=[]; trades=[]; curve=[]
    def pnl(s,px): return s['expo']*s['eq0']*((px-s['e'])/s['e'])*s['dr']
    for i in range(start,N):
        keep=[]
        for s in open_:
            px=why=None
            if s['dr']==1 and LO[i]<=s['stop']: px=min(s['stop'],O[i]); why='손절'
            elif s['dr']==-1 and HI[i]>=s['stop']: px=max(s['stop'],O[i]); why='손절'
            elif i>s['a'] and P[s['k']]['ex'](s['a'],i): px=C[i]; why='이유소멸'
            if px is None: keep.append(s); continue
            g=pnl(s,px)-2*cost*s['expo']*s['eq0']; eq+=g
            trades.append(dict(date=idx[i],pat=s['k'],r=(px-s['e'])/s['e']*s['dr'],pnl=g/s['eq0'],why=why,days=i-s['a']))
        open_=keep
        # 일간 평가 (보유분 종가 평가)
        mtm=eq+sum(pnl(s,C[i]) for s in open_)
        held={s['k'] for s in open_}
        for k,p in P.items():
            if not p['ent'][i] or k in held or len(open_)>=maxpos: continue
            sd=(stopk*atr[i]/C[i]) if stoppct is None else stoppct; expo=min(risk/sd,navcap) if wfix is None else wfix
            if sum(s['expo'] for s in open_)+expo>navcap: continue
            open_.append(dict(k=k,a=i,e=C[i],dr=p['dr'],expo=expo,eq0=mtm,stop=C[i]*(1-p['dr']*sd)))
        curve.append((idx[i],mtm,len(open_)))
    return pd.DataFrame(trades),pd.DataFrame(curve,columns=['date','eq','npos'])
