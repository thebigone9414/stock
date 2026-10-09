"""대규모 조합 탐색 — 트리거 x 상태(0~2개)"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys, itertools
sys.path.insert(0,'.')
from lib import build, xup, xdn, FIB

def make(x):
    C,O,HI,LO,LV=x['C'],x['O'],x['HI'],x['LO'],x['LV']
    rsi,macd,msig,mh=x['rsi'],x['macd'],x['msig'],x['mhist']
    ma5,ma21,ma62,ma120,ma200=x['ma5'],x['ma21'],x['ma62'],x['ma120'],x['ma200']
    atr,atrq,pos=x['atr'],x['atrq'],x['pos']
    r5,r20,r60=x['r5'],x['r20'],x['r60']
    Z=np.zeros_like(C); K=lambda v: np.full_like(C,v)

    # ── 트리거: (마스크, 청산함수, 방향) ──
    T={}
    for j,k in enumerate(FIB):
        lv=LV[:,j]
        T[f'F{k}up']=(xup(C,lv),(lambda lv:(lambda i,jj:C[jj]<lv[jj]))(lv))
        T[f'F{k}dn']=(xdn(C,lv),(lambda lv:(lambda i,jj:C[jj]>lv[jj]))(lv))
    for nm,ma in [('21',ma21),('62',ma62),('120',ma120),('200',ma200)]:
        T[f'MA{nm}up']=(xup(C,ma),(lambda ma:(lambda i,j:C[j]<ma[j]))(ma))
        T[f'MA{nm}dn']=(xdn(C,ma),(lambda ma:(lambda i,j:C[j]>ma[j]))(ma))
    T['GC5_21']=(xup(ma5,ma21),lambda i,j: ma5[j]<ma21[j])
    T['GC21_62']=(xup(ma21,ma62),lambda i,j: ma21[j]<ma62[j])
    T['GC62_200']=(xup(ma62,ma200),lambda i,j: ma62[j]<ma200[j])
    T['MACDgc']=(xup(macd,msig),lambda i,j: macd[j]<msig[j])
    T['MACD0up']=(xup(macd,Z),lambda i,j: macd[j]<0)
    T['MHup']=(xup(mh,Z),lambda i,j: mh[j]<0)
    for v in [20,30,40,50]:
        T[f'RSI{v}up']=(xup(rsi,K(v)),(lambda v:(lambda i,j: rsi[j]<v-5 or rsi[j]>v+25))(v))
    T['HH20up']=(xup(C,np.roll(x['hh20'],1)),lambda i,j: C[j]<x['ll10'][j-1])
    T['HH60up']=(xup(C,np.roll(x['hh60'],1)),lambda i,j: C[j]<x['ll20'][j-1])
    T['HH252up']=(xup(C,np.roll(x['hh252'],1)),lambda i,j: C[j]<x['ll20'][j-1])
    T['LL20dn']=(xdn(C,np.roll(x['ll20'],1)),lambda i,j: C[j]>x['hh10'][j-1])
    T['ATRsq_br']=((atrq<0.20)&(C>np.roll(x['hh20'],1)),lambda i,j: C[j]<x['ll10'][j-1])
    T['hammer']=((x['tail']>0.70)&(LO<=np.roll(x['ll20'],1)),lambda i,j:(C[j]>x['hh20'][j-1])or(C[j]<LO[i]))
    T['gapdn_rec']=((O<np.roll(C,1)*0.98)&(C>O),lambda i,j:(C[j]>np.roll(C,1)[i])or(C[j]<LO[i]))
    T['bigup']=(((C-O)/O>0.03),lambda i,j: C[j]<ma5[j])
    T['dn3']=((np.diff(C,prepend=C[0])<0)&(np.roll(np.diff(C,prepend=C[0])<0,1))&(np.roll(np.diff(C,prepend=C[0])<0,2)),
              lambda i,j:(C[j]>x['hh10'][j-1])or(C[j]<x['ll20'][i]))
    T['up3']=((np.diff(C,prepend=C[0])>0)&(np.roll(np.diff(C,prepend=C[0])>0,1))&(np.roll(np.diff(C,prepend=C[0])>0,2)),
              lambda i,j: C[j]<ma5[j])

    # ── 상태 (맥락 필터) ──
    S={
     'pos<0.3':pos<0.3,'pos0.3-0.6':(pos>=0.3)&(pos<0.6),'pos>0.6':pos>=0.6,
     'C>ma200':C>ma200,'C<ma200':C<ma200,'C>ma62':C>ma62,'C<ma62':C<ma62,
     'ma62up':x['sl62']>0,'ma62dn':x['sl62']<0,'ma200up':x['sl200']>0,'ma200dn':x['sl200']<0,
     'align':(ma5>ma21)&(ma21>ma62),'dalign':(ma5<ma21)&(ma21<ma62),
     'rsi<40':rsi<40,'rsi40-60':(rsi>=40)&(rsi<60),'rsi>60':rsi>=60,
     'mh>0':mh>0,'mh<0':mh<0,'macd>0':macd>0,'macd<0':macd<0,
     'atrq<0.33':atrq<0.33,'atrq0.33-0.66':(atrq>=0.33)&(atrq<0.66),'atrq>0.66':atrq>=0.66,
     'r5>0':r5>0,'r5<0':r5<0,'r5>5%':r5>0.05,'r5<-5%':r5<-0.05,
     'r20>0':r20>0,'r20<0':r20<0,'r60>0':r60>0,'r60<0':r60<0,
     'gap_ma21':(C/ma21-1)>0.02,'gap_ma21n':(C/ma21-1)<-0.02,
    }
    T={k:(np.nan_to_num(v[0],nan=0).astype(bool),v[1]) for k,v in T.items()}
    S={k:np.nan_to_num(v,nan=0).astype(bool) for k,v in S.items()}
    return T,S

def fastsim(C,O,LO,HI,atr,ent,exfn,stopk=5.0,maxhold=250,cost=0.0001,start=300,dirn=1):
    N=len(C); rs=[]; ds=[]; i=start
    while i<N-1:
        if not ent[i] or np.isnan(atr[i]): i+=1; continue
        e=C[i]; st=e-dirn*stopk*atr[i]; j=i+1; px=None
        while j<N and (j-i)<=maxhold:
            if (dirn==1 and LO[j]<=st) or (dirn==-1 and HI[j]>=st):
                px=(min(st,O[j]) if dirn==1 else max(st,O[j])); break
            if exfn(i,j): px=C[j]; break
            j+=1
        if px is None: j=min(j,N-1); px=C[j]
        rs.append((px-e)/e*dirn-2*cost); ds.append(i); i=j+1
    return np.array(rs),np.array(ds)
