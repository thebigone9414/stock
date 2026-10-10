"""개별 종목 패널 (344종목 10년) - 지표, 패턴, numba 트레이드 시뮬, 순환이동 귀무검정"""
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, sys, os
BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
from anchor import anchors
from numba import njit
FIB=[0.146,0.236,0.309,0.382,0.5,0.618,0.691,0.764,0.854]
DATA=os.path.join(BASE,'stockdata','ohlcv.pkl')          # 없으면 ohlcv.csv.gz 를 읽음 (code,name,date,open,high,low,close,volume)
DATA_CSV=os.path.join(BASE,'stockdata','ohlcv.csv.gz')
PANEL=os.path.join(BASE,'stockdata','panel.pkl')

def ind_one(g):
    """한 종목 DataFrame(date index, open high low close volume) -> 지표 DataFrame"""
    C,O,H,L,V=g.close.astype(float),g.open.astype(float),g.high.astype(float),g.low.astype(float),g.volume.astype(float)
    halted=(V<=0)|(O<=0)|(L<=0)          # 거래정지일: 시고저 0, 종가 전일 복사
    O=O.where(~halted); H=H.where(~halted); L=L.where(~halted)
    x=pd.DataFrame(index=g.index)
    x['O'],x['H'],x['L'],x['C'],x['V']=O,H,L,C,V; x['trd']=~halted
    for w in (5,10,20,21,62,248):
        x[f'ma{w}']=C.rolling(w).mean()
    for w in (10,15,20,55,252):
        x[f'hh{w}']=H.rolling(w).max(); x[f'll{w}']=L.rolling(w).min()
    x['hh20c']=C.rolling(20).max(); x['ll20c']=C.rolling(20).min()
    tr=pd.concat([H-L,(H-C.shift()).abs(),(L-C.shift()).abs()],axis=1).max(axis=1)
    x['atr']=tr.rolling(20).mean(); x['atrq']=(x['atr']/C).rolling(252).rank(pct=True)
    dl=C.diff(); gn=dl.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); ls=(-dl.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x['rsi']=100-100/(1+gn/ls.replace(0,np.nan))
    e12=C.ewm(span=12,adjust=False).mean(); e26=C.ewm(span=26,adjust=False).mean(); macd=e12-e26; sig=macd.ewm(span=9,adjust=False).mean()
    x['macd'],x['msig'],x['mh']=macd,sig,macd-sig
    x['sl62']=x['ma62'].diff(20); x['sl248']=x['ma248'].diff(20)
    x['r5'],x['r20'],x['r60'],x['r120']=C.pct_change(5),C.pct_change(20),C.pct_change(60),C.pct_change(120)
    x['vma20']=V.rolling(20).mean(); x['vma5']=V.rolling(5).mean(); x['vr']=V/x['vma20']
    x['amt20']=(C*V).rolling(20).mean()   # 20일 평균 거래대금
    x['tail']=(C-L)/(H-L).replace(0,np.nan)
    # 감천 격자
    try:
        lo,hi,_=anchors(g.rename(columns=str.lower),0.18)
        x['alo'],x['ahi']=lo,hi
        for k in FIB: x[f'F{k}']=lo*(hi/lo)**k
        x['pos']=np.log(C/lo)/np.log(hi/lo)
    except Exception:
        for k in FIB: x[f'F{k}']=np.nan
        x['pos']=np.nan
    return x

def build_panel(force=False, src=None):
    import os
    if src is None and os.path.exists(PANEL) and not force: return pd.read_pickle(PANEL)
    if src is None:
        if os.path.exists(DATA): df=pd.read_pickle(DATA)
        else: df=pd.read_csv(DATA_CSV,dtype={'code':str}); df['date']=pd.to_datetime(df['date'])
    elif src.endswith('.pkl'): df=pd.read_pickle(src)
    elif src.endswith('.xlsx'): df=pd.read_excel(src,sheet_name=0,dtype={'code':str}); df['date']=pd.to_datetime(df['date'])
    else: df=pd.read_csv(src,dtype={'code':str}); df['date']=pd.to_datetime(df['date'])
    out=[]
    for code,g in df.groupby('code'):
        g=g.set_index('date').sort_index()
        if len(g)<300: continue
        x=ind_one(g); x['code']=code; x['name']=g.name.iloc[0]; out.append(x)
    P=pd.concat(out); P.index.name='date'; P=P.reset_index().set_index(['code','date']).sort_index()
    if src is None: P.to_pickle(PANEL)
    return P

def xup(a,b):
    a=np.asarray(a,float); b=np.asarray(b,float); pa=np.roll(a,1); pb=np.roll(b,1)
    m=(a>b)&(pa<=pb); m[0]=False; return np.nan_to_num(m,nan=0).astype(bool)
def xdn(a,b):
    a=np.asarray(a,float); b=np.asarray(b,float); pa=np.roll(a,1); pb=np.roll(b,1)
    m=(a<b)&(pa>=pb); m[0]=False; return np.nan_to_num(m,nan=0).astype(bool)
def prev(a): a=np.asarray(a,float); p=np.roll(a,1); p[0]=np.nan; return p
def b(a): return np.nan_to_num(np.asarray(a,float),nan=0).astype(bool) if np.asarray(a).dtype!=bool else np.asarray(a)

# ---------- 패턴 정의: (진입마스크, 청산마스크(상태), 앵커청산 하단배열, 앵커청산 상단배열, 손절 ATR 배수, 손절 상한, 최대보유) ----------
def patterns_for(x, mkt=None):
    """x: 한 종목 지표 DataFrame. mkt: 같은 날짜로 정렬된 시장 상태 DataFrame(k200 sl62up 등). 반환 dict name -> spec"""
    O,H,L,C,V=x['O'].values,x['H'].values,x['L'].values,x['C'].values,x['V'].values
    g=lambda k: x[k].values
    ma5,ma10,ma21,ma62,ma248=g('ma5'),g('ma10'),g('ma21'),g('ma62'),g('ma248')
    rsi,macd,msig,mh=g('rsi'),g('macd'),g('msig'),g('mh'); atr,atrq=g('atr'),g('atrq')
    r5,r20,r60=g('r5'),g('r20'),g('r60'); sl62,sl248=g('sl62'),g('sl248'); vr=g('vr')
    hh10,hh15,hh20,hh55,hh252=g('hh10'),g('hh15'),g('hh20'),g('hh55'),g('hh252'); ll10,ll15,ll20,ll55=g('ll10'),g('ll15'),g('ll20'),g('ll55')
    N=len(C); nan=np.full(N,np.nan)
    up=lambda a: b(a)
    ma62up=up(sl62>0); ma248up=up(sl248>0); above248=up(C>ma248); align=up((ma5>ma21)&(ma21>ma62))
    dn1=np.diff(C,prepend=C[0])<0; up1=np.diff(C,prepend=C[0])>0
    dn3=up(dn1&np.roll(dn1,1)&np.roll(dn1,2)); up3=up(up1&np.roll(up1,1)&np.roll(up1,2))
    P={}
    def add(name,ent,ex,alo=None,ahi=None,stopk=5.0,stopcap=0.10,maxhold=250,fam=''):
        P[name]=dict(ent=up(ent),ex=up(ex),alo=nan if alo is None else np.asarray(alo,float),ahi=nan if ahi is None else np.asarray(ahi,float),stopk=stopk,stopcap=stopcap,maxhold=maxhold,fam=fam)
    # ── 코스피200 11패턴 이식 (롱 10개) ──
    add('K1 MACD골든+62상승', xup(macd,msig)&ma62up, macd<msig, fam='K')
    add('K2 20일신저가이탈+추세', xdn(C,prev(ll20))&ma62up&ma248up, C>prev(hh10), fam='K')
    add('K3 RSI50돌파+히스토음', xup(rsi,np.full(N,50.0))&ma62up&up(mh<0), (rsi<45)|(rsi>75), fam='K')
    add('K4 5/21골든+62상승', xup(ma5,ma21)&ma62up&up(r5>0), ma5<ma21, fam='K')
    add('K5 62선돌파+RSI중', xup(C,ma62)&ma62up&up((rsi>=40)&(rsi<60)), C<ma62, fam='K')
    add('K6 3일상승+히스토양', up3&ma62up&up(mh>0), C<ma5, fam='K')
    add('K7 3일하락+248위', dn3&above248&up(r60<0), C>prev(hh10), alo=g('ll20'), fam='K')
    if 'F0.764' in x and np.isfinite(x['F0.764'].values).any():
        f764,f309=g('F0.764'),g('F0.309')
        add('K8 F0.764이탈+이격', xdn(C,f764)&up((C/ma21-1)<-0.02), C>f764, fam='K')
        add('K9 F0.309이탈+62하락', xdn(C,f309)&up(sl62<0), C>f309, fam='K')
    add('K10 21선돌파+변동성중', xup(C,ma21)&up((atrq>=0.33)&(atrq<0.66))&up(r5<0), C<ma21, fam='K')
    # ── 터틀 ──
    add('T1 터틀20 (20고돌파/10저이탈)', xup(C,prev(hh20)), C<prev(ll10), stopk=2.0,stopcap=None, fam='T')
    add('T2 터틀55 (55고돌파/20저이탈)', xup(C,prev(hh55)), C<prev(ll20), stopk=2.0,stopcap=None, fam='T')
    add('T3 터틀20+248위+거래량', xup(C,prev(hh20))&above248&up(vr>1.5), C<prev(ll10), stopk=2.0,stopcap=None, fam='T')
    add('T4 터틀55+248위', xup(C,prev(hh55))&above248, C<prev(ll20), stopk=2.0,stopcap=None, fam='T')
    # ── 다바스 박스 ──
    boxw=(prev(hh20)-prev(ll20))/prev(ll20)
    add('D1 다바스 (좁은박스20 상단돌파)', xup(C,prev(hh20))&up(boxw<0.12), C<prev(ll10), alo=prev(ll20), stopk=2.0,stopcap=None, fam='D')
    add('D2 다바스+거래량+248위', xup(C,prev(hh20))&up(boxw<0.12)&up(vr>1.5)&above248, C<prev(ll10), alo=prev(ll20), stopk=2.0,stopcap=None, fam='D')
    add('D3 다바스 넓은박스(<20%)+거래량', xup(C,prev(hh20))&up(boxw<0.20)&up(vr>1.5), C<prev(ll10), alo=prev(ll20), stopk=2.0,stopcap=None, fam='D')
    # ── 와이코프 스프링: 최근 3일 내 20일 저가 하향 이탈 후 오늘 종가가 그 저가 위로 회복 ──
    sup=prev(ll20)  # 전일까지 20일 최저
    sup3=np.minimum.reduce([np.roll(sup,k) if k else sup for k in range(0,3)])  # 최근 3일의 지지선 중 최소
    dipped=np.zeros(N,bool)
    for k in range(0,3): dipped|=np.roll(L,k)<np.roll(sup,k)
    spring=up(dipped)&up(C>sup)&up(C>O)
    sl=np.minimum.reduce([np.roll(L,k) for k in range(0,3)])  # 스프링 저점
    add('W1 스프링 (3일내 20저이탈 후 회복)', spring, C>prev(hh10), alo=sl, stopk=2.0,stopcap=None, fam='W')
    add('W2 스프링+248위', spring&above248, C>prev(hh10), alo=sl, stopk=2.0,stopcap=None, fam='W')
    add('W3 스프링+62상승 (=K2 변형)', spring&ma62up, C>prev(hh10), alo=sl, stopk=2.0,stopcap=None, fam='W')
    add('W4 스프링+거래량감소', spring&up(vr<1.0), C>prev(hh10), alo=sl, stopk=2.0,stopcap=None, fam='W')
    # ── 쿨라매기: 선행 상승 + 질서있는 조정(좁은 15일 박스, 거래량 감소) + 거래량 돌파, 10일선 이탈 청산 ──
    tight=(prev(hh15)-prev(ll15))/prev(ll15)
    lead=up(r60>0.30); quiet=up(g('vma5')<g('vma20'))
    brk=xup(C,prev(hh15))
    add('Q1 쿨라매기 (60일+30%, 15일박스<15%, 거래량돌파)', brk&lead&up(tight<0.15)&up(vr>1.5), C<ma10, alo=L, stopk=2.0,stopcap=None, fam='Q')
    add('Q2 쿨라매기 완화 (60일+20%, 박스<20%)', brk&up(r60>0.20)&up(tight<0.20)&up(vr>1.3), C<ma10, alo=L, stopk=2.0,stopcap=None, fam='Q')
    add('Q3 쿨라매기 + 조정기 거래량감소', brk&lead&up(tight<0.15)&up(vr>1.5)&np.roll(quiet,1), C<ma10, alo=L, stopk=2.0,stopcap=None, fam='Q')
    add('Q4 쿨라매기, 20일선 이탈 청산', brk&lead&up(tight<0.15)&up(vr>1.5), C<ma21, alo=L, stopk=2.0,stopcap=None, fam='Q')
    # ── CIS: 오르는 주식(52주 신고가, 정배열)을 사고 꺾이면(21일선 이탈) 판다 ──
    add('C1 CIS 52주신고가+정배열', xup(C,prev(hh252))&align, C<ma21, stopk=2.0,stopcap=None, fam='C')
    add('C2 CIS 52주신고가+거래량', xup(C,prev(hh252))&up(vr>1.5), C<ma21, stopk=2.0,stopcap=None, fam='C')
    add('C3 CIS 55일신고가+정배열, 10일선청산', xup(C,prev(hh55))&align, C<ma10, stopk=2.0,stopcap=None, fam='C')
    # ── 후지모토: RSI 과매도 반등 (추세 종목만) ──
    add('R1 RSI30상향+248위', xup(rsi,np.full(N,30.0))&above248, (rsi>60)|(C<ma248), stopk=3.0,stopcap=0.10, fam='R')
    add('R2 RSI30상향+62상승', xup(rsi,np.full(N,30.0))&ma62up, (rsi>60), stopk=3.0,stopcap=0.10, fam='R')
    add('R3 RSI<30 종가 (248위), RSI50회복 청산', up(rsi<30)&~np.roll(up(rsi<30),1)&above248, (rsi>50), stopk=3.0,stopcap=0.10, fam='R')
    # ── 폴 튜더 존스: 200선 위 + 20일 고점 돌파, 5:1 브래킷 (손절 1ATR, 목표 5ATR) ──
    add('P1 PTJ 20고돌파+248위, 5:1 브래킷', xup(C,prev(hh20))&above248, np.zeros(N,bool), ahi=C+5*atr, stopk=1.0,stopcap=None, maxhold=60, fam='P')
    add('P2 PTJ 20고돌파+248위, 손절2ATR/목표10ATR', xup(C,prev(hh20))&above248, np.zeros(N,bool), ahi=C+10*atr, stopk=2.0,stopcap=None, maxhold=120, fam='P')
    # ── 와이코프 장기 지지(55일 저가) 스프링 ──
    sup55=prev(ll55); dipped55=np.zeros(N,bool)
    for k in range(0,3): dipped55|=np.roll(L,k)<np.roll(sup55,k)
    spring55=up(dipped55)&up(C>sup55)&up(C>O)
    add('W5 스프링55 (55일 저가 이탈 후 회복)+248위', spring55&above248, C>prev(hh10), alo=sl, stopk=2.0,stopcap=None, fam='W')
    add('W6 스프링55 + 거래량 돌파일<이탈일', spring55&up(vr<np.roll(vr,1)), C>prev(hh10), alo=sl, stopk=2.0,stopcap=None, fam='W')
    # ── 감천 격자: 레벨 상향돌파 (하단 구간), 20일 고정 보유 / 레벨 재이탈 청산 ──
    if 'F0.309' in x and np.isfinite(x['F0.309'].values).any():
        pos=g('pos')
        for lvname in ('F0.309','F0.382','F0.5'):
            lv=g(lvname)
            add(f'G1 감천 {lvname} 상향돌파 + 하단(pos<0.6), 20일 보유', xup(C,lv)&up(pos<0.6), np.zeros(N,bool), stopk=3.0,stopcap=0.10, maxhold=20, fam='G')
            add(f'G2 감천 {lvname} 상향돌파 + 62상승, 레벨 재이탈 청산(최대60일)', xup(C,lv)&ma62up, C<lv, stopk=3.0,stopcap=0.10, maxhold=60, fam='G')
        add('G3 감천 F0.764 이탈반등 + 248위', xdn(C,g('F0.764'))&above248, C>g('F0.764'), fam='G')
        add('G4 감천 F0.618 이탈반등 + 62상승', xdn(C,g('F0.618'))&ma62up, C>g('F0.618'), fam='G')
    # ── K2 변형: 거래량 조건 ──
    add('K2v 20일신저가이탈+추세+거래량급증', xdn(C,prev(ll20))&ma62up&ma248up&up(vr>1.5), C>prev(hh10), fam='K')
    return P

@njit(cache=True)
def sim_trades(O,H,L,C,atr,ent,ex,alo,ahi,stopk,stopcap,maxhold,start,end,cost):
    """진입: 신호일 i 다음날 종가. 청산: 상태 ex[j] 또는 C[j]<alo[i] 또는 C[j]>ahi[i] (종가 판정) -> j+1 시가. 손절: 장중 LO<=stop -> min(stop,O).
       반환: (수익률, 신호일, 청산일, 보유일, 사유코드 0=이유소멸 1=손절 2=시간 3=데이터끝)"""
    n=end; rs=np.empty(2000); si=np.empty(2000,np.int64); xi=np.empty(2000,np.int64); hd=np.empty(2000,np.int64); wy=np.empty(2000,np.int64); k=0
    i=start
    while i<n-2 and k<2000:
        if not ent[i] or np.isnan(atr[i]) or np.isnan(C[i+1]) or np.isnan(O[i+1]): i+=1; continue
        e=i+1; px0=C[e]
        sd=stopk*atr[i]
        if stopcap>0 and sd>stopcap*C[i]: sd=stopcap*C[i]
        st=C[i]-sd
        j=e+1; px=-1.0; why=3
        while j<n:
            if L[j]<=st:
                px=st if O[j]>st else O[j]; why=1; break
            hit=ex[j]
            if not hit and not np.isnan(alo[i]) and C[j]<alo[i]: hit=True
            if not hit and not np.isnan(ahi[i]) and C[j]>ahi[i]: hit=True
            if hit or j-e>=maxhold:
                why=0 if hit else 2
                jj=j+1
                while jj<n and np.isnan(O[jj]): jj+=1
                if jj<n: px=O[jj]; j=jj
                else: px=C[n-1]; j=n-1
                break
            j+=1
        if px<0: j=n-1; px=C[j]; why=3
        rs[k]=px/px0-1-cost; si[k]=i; xi[k]=j; hd[k]=j-e; wy[k]=why; k+=1
        i=j+1
    return rs[:k],si[:k],xi[:k],hd[:k],wy[:k]

@njit(cache=True)
def perm_mean(O,H,L,C,atr,ent,ex,alo,ahi,stopk,stopcap,maxhold,start,end,cost,nrep,seed):
    """진입 마스크를 순환이동(종목 내)한 무작위 진입의 평균 수익률 분포"""
    np.random.seed(seed); n=len(C); out=np.empty(nrep); tot=np.empty(nrep)
    for r in range(nrep):
        k=np.random.randint(300,n-300)
        m=np.empty(n,np.bool_)
        for t in range(n): m[t]=ent[(t-k)%n]
        # 앵커 배열도 같이 이동해야 하나? 앵커는 신호일 기준 레벨이므로 이동한 날의 레벨을 쓰는 게 맞음(그 날 기준 재계산) -> 그대로 사용
        rs,si,xi,hd,wy=sim_trades(O,H,L,C,atr,m,ex,alo,ahi,stopk,stopcap,maxhold,start,end,cost)
        out[r]=rs.mean() if len(rs)>0 else np.nan; tot[r]=len(rs)
    return out,tot

def append_bars(rows, rebuild=True):
    """자동매매용: 당일 종목 일봉을 기본 데이터에 추가하고 패널을 다시 만든다.
    rows: DataFrame 또는 dict 리스트, 열 code,name,date,open,high,low,close,volume (KRX 정규장, 수정주가 기준. 거래정지일은 open/high/low=0, volume=0)
    같은 (code,date) 가 이미 있으면 새 값으로 덮어씀. ohlcv.pkl 이 있으면 pkl, 없으면 ohlcv.csv.gz 에 저장."""
    import os
    new=pd.DataFrame(rows); new['code']=new['code'].astype(str).str.zfill(6); new['date']=pd.to_datetime(new['date'])
    for c in ('open','high','low'):   # 시고저가 없으면 종가로 채움 (모듈 B 는 종가·거래량만 필요)
        if c not in new.columns: new[c]=new['close']
        new[c]=new[c].where(new[c].notna(),new['close'])
    if 'name' not in new.columns: new['name']=''
    new=new[['code','name','date','open','high','low','close','volume']]
    if os.path.exists(DATA): df=pd.read_pickle(DATA)
    else: df=pd.read_csv(DATA_CSV,dtype={'code':str}); df['date']=pd.to_datetime(df['date'])
    key=set(zip(new.code,new.date)); df=df[~pd.Series(list(zip(df.code,df.date)),index=df.index).isin(key)]
    df=pd.concat([df,new],ignore_index=True).sort_values(['code','date']).reset_index(drop=True)
    if os.path.exists(DATA): df.to_pickle(DATA)
    else: df.to_csv(DATA_CSV,index=False,compression='gzip')
    return build_panel(force=True) if rebuild else None
