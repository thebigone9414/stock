"""트리거(진입이유) + 짝지어진 청산(이유 소멸) + 필터 조합 전수탐색"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys
sys.path.insert(0,'.')
from anchor import anchors
FIB=[0.146,0.236,0.309,0.382,0.5,0.618,0.691,0.764,0.854]
LONG_MA=248   # 장기 이동평균 길이 (탐색·검증은 200, 운용은 248: 성적 차이 CAGR 0.5%p 이내)

def build(csv='k200.csv', zz=0.18):
    d=pd.read_csv(csv,parse_dates=['date']).set_index('date').sort_index()
    lo,hi,_=anchors(d,zz); d['alo'],d['ahi']=lo,hi
    d=d.dropna(subset=['alo']).copy()
    C,O,HI,LO=d.close,d.open,d.high,d.low
    x={'d':d,'idx':d.index,'C':C.values,'O':O.values,'HI':HI.values,'LO':LO.values}
    x['LV']=np.column_stack([d.alo.values*(d.ahi.values/d.alo.values)**k for k in FIB])
    for w in (5,10,20,21,60,62,120,200,248,252):
        x[f'ma{w}']=C.rolling(w).mean().values
        x[f'hh{w}']=HI.rolling(w).max().values
        x[f'll{w}']=LO.rolling(w).min().values
    # 장기선: 운용 규격은 248일선 (2026-09-26 변경). 패턴 코드가 참조하는 키 이름은 ma200/sl200 그대로 두고 값만 248일선으로 채운다.
    x['ma200']=C.rolling(LONG_MA).mean().values
    tr=pd.concat([HI-LO,(HI-C.shift()).abs(),(LO-C.shift()).abs()],axis=1).max(axis=1)
    x['atr']=tr.rolling(20).mean().values
    x['atrq']=(tr.rolling(20).mean()/C).rolling(252).rank(pct=True).values
    dl=C.diff(); g=dl.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    l_=(-dl.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x['rsi']=(100-100/(1+g/l_.replace(0,np.nan))).values
    e12=C.ewm(span=12,adjust=False).mean(); e26=C.ewm(span=26,adjust=False).mean()
    macd=e12-e26; sig=macd.ewm(span=9,adjust=False).mean()
    x['macd']=macd.values; x['msig']=sig.values; x['mhist']=(macd-sig).values
    x['sl62']=C.rolling(62).mean().diff(20).values
    x['sl200']=C.rolling(LONG_MA).mean().diff(20).values
    x['pos']=(np.log(C/d.alo)/np.log(d.ahi/d.alo)).values
    x['r5']=C.pct_change(5).values; x['r20']=C.pct_change(20).values
    x['r60']=C.pct_change(60).values
    x['tail']=((C-LO)/(HI-LO).replace(0,np.nan)).values
    return x

def xup(a,b):   # a가 b를 상향돌파
    return (a>b)&(np.roll(a,1)<=np.roll(b,1))
def xdn(a,b):
    return (a<b)&(np.roll(a,1)>=np.roll(b,1))

def triggers(x):
    """이름 -> (진입마스크, 청산함수(i,j)->bool, 설명)"""
    C,LV=x['C'],x['LV']; rsi,macd,msig=x['rsi'],x['macd'],x['msig']
    ma5,ma21,ma62,ma200=x['ma5'],x['ma21'],x['ma62'],x['ma200']
    T={}
    # ① 피보나치 레벨별 돌파 -> 그 레벨 재이탈이 청산
    for j,k in enumerate(FIB):
        if k in (0.146,0.854): continue
        lv=LV[:,j]
        T[f'Fib{k}돌파']=(xup(C,lv), (lambda lv: (lambda i,jj: C[jj]<lv[jj]))(lv),
                          f'{k}선 재이탈')
    # ② MACD 골든크로스 -> 데드크로스
    T['MACD골든']=(xup(macd,msig), lambda i,j: macd[j]<msig[j], 'MACD 데드크로스')
    # ③ RSI 30 상향돌파 -> RSI 60 도달 또는 40 재이탈
    T['RSI30탈출']=(xup(rsi,np.full_like(rsi,30)),
                    lambda i,j: (rsi[j]>60) or (rsi[j]<40 and j>i+3), 'RSI60 도달/40 재이탈')
    # ④ 정배열 성립 -> 정배열 붕괴
    al=(ma5>ma21)&(ma21>ma62)
    T['정배열성립']=(al&~np.roll(al,1), lambda i,j: not ((ma5[j]>ma21[j])and(ma21[j]>ma62[j])), '정배열 붕괴')
    # ⑤ 21/62일선 돌파 -> 재이탈
    T['21일선돌파']=(xup(C,ma21), lambda i,j: C[j]<ma21[j], '21일선 재이탈')
    T['62일선돌파']=(xup(C,ma62), lambda i,j: C[j]<ma62[j], '62일선 재이탈')
    T['200일선돌파']=(xup(C,ma200), lambda i,j: C[j]<ma200[j], '200일선 재이탈')
    # ⑥ 매너: 레벨 2회 터치 후 돌파
    for j,k in [(2,0.309),(3,0.382),(4,0.5)]:
        lv=LV[:,j]
        up=xup(C,lv); cnt=np.zeros(len(C),int); c=0
        for i in range(len(C)):
            if up[i]: c+=1
            cnt[i]=c
        second=up&(np.mod(cnt,1)==0)&(cnt>=2)
        T[f'매너{k}2차']=(second, (lambda lv: (lambda i,jj: C[jj]<lv[jj]))(lv), f'{k}선 재이탈')
    # ⑦ 52주 신고가 -> ATR 추적 (별도 처리)
    T['52주신고가']=((C>=np.roll(x['hh252'],1))&(C>ma200), lambda i,j: False, 'ATR 추적')
    # ⑧ 변동성 수축 돌파 -> ATR 추적
    T['변동성수축돌파']=((x['atrq']<0.20)&(C>np.roll(x['hh20'],1)), lambda i,j: False, 'ATR 추적')
    # ⑨ N자: 1파 상승 후 조정, 조정 고점 재돌파 -> 측정이동 도달 또는 조정 저점 이탈
    hh20p=np.roll(x['hh20'],1); ll10p=np.roll(x['ll10'],1)
    nwave=xup(C,hh20p)&(x['r60']>0)
    def nex(i,j):
        leg=x['hh20'][i]-x['ll20'][i]
        return (C[j]>=C[i]+leg*0.618) or (C[j]<x['ll10'][i])
    T['N자재돌파']=(nwave,nex,'측정이동 도달/조정저점 이탈')
    return {k:(np.nan_to_num(v[0],nan=0).astype(bool),v[1],v[2]) for k,v in T.items()}

def filters(x):
    C=x['C']
    return {
     '위치하위':x['pos']<0.4, '위치중앙':(x['pos']>=0.4)&(x['pos']<=0.7), '위치상위':x['pos']>0.7,
     '200선위':C>x['ma200'], '200선아래':C<x['ma200'],
     '62선상승':x['sl62']>0, '62선하락':x['sl62']<0,
     '정배열':(x['ma5']>x['ma21'])&(x['ma21']>x['ma62']),
     '역배열':(x['ma5']<x['ma21'])&(x['ma21']<x['ma62']),
     'RSI50위':x['rsi']>50, 'RSI50아래':x['rsi']<50, 'RSI과열아님':x['rsi']<70,
     'MACD양':x['mhist']>0, 'MACD음':x['mhist']<0,
     '변동성낮음':x['atrq']<0.5, '변동성높음':x['atrq']>=0.5,
     '60일상승':x['r60']>0, '60일하락':x['r60']<0,
    }

def sim(x, ent, exfn, stopk=3.0, trail=None, maxhold=250, cost=0.0001, start=270):
    C,O,LO,HI,atr=x['C'],x['O'],x['LO'],x['HI'],x['atr']; N=len(C)
    out=[]; i=start
    while i<N-1:
        if not ent[i] or np.isnan(atr[i]): i+=1; continue
        e=C[i]; st=e-stopk*atr[i]; peak=e; j=i+1; px=None; why=None
        while j<N and (j-i)<=maxhold:
            if LO[j]<=st: px=min(st,O[j]); why='손절'; break
            if trail:
                peak=max(peak,HI[j-1]); lv=peak-trail*atr[j-1]
                if not np.isnan(lv) and LO[j]<=lv: px=min(lv,O[j]); why='추적'; break
            if exfn(i,j): px=C[j]; why='이유소멸'; break
            j+=1
        if px is None: j=min(j,N-1); px=C[j]; why='기한'
        out.append((x['idx'][i],(px-e)/e-2*cost,why,j-i)); i=j+1
    return pd.DataFrame(out,columns=['date','r','why','days'])

def stat(t,x,split='2016-01-01'):
    if len(t)<20: return None
    YRS=(x['idx'][-1]-x['idx'][0]).days/365.25
    eq=(1+t.r).cumprod(); c=eq.iloc[-1]
    s=pd.Timestamp(split); a=t[t.date<s].r; b=t[t.date>=s].r
    if len(a)<5 or len(b)<5: return None
    expo=t.days.sum()/(YRS*252)
    cagr=(c**(1/YRS)-1) if c>0 else -1
    return dict(n=len(t),win=(t.r>0).mean(),exp=t.r.mean(),
                t=t.r.mean()/t.r.std()*np.sqrt(len(t)),
                cagr=cagr,mdd=(eq/eq.cummax()-1).min(),expo=expo,
                per=cagr/expo if expo>0 else np.nan,
                is_=a.mean(),oos=b.mean(),days=t.days.mean(),
                t_is=a.mean()/a.std()*np.sqrt(len(a)),
                t_oos=b.mean()/b.std()*np.sqrt(len(b)))
