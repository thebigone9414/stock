import numpy as np
def pivots(h,l,pct):
    n=len(h); seq=[]; 
    if n<2: return seq
    dirn=0; ei=0; ev=h[0]; si=0; sv=l[0]
    for i in range(1,n):
        if dirn>=0:
            if h[i]>ev: ev=h[i]; ei=i
            if l[i]<=ev*(1-pct):
                if dirn>0 or not seq: seq.append((ei,ev,'H',i))
                dirn=-1; sv=l[i]; si=i
                continue
        if dirn<=0:
            if l[i]<sv: sv=l[i]; si=i
            if h[i]>=sv*(1+pct):
                if dirn<0 or not seq: seq.append((si,sv,'L',i))
                dirn=1; ev=h[i]; ei=i
    return seq
def anchors(d,pct=0.18):
    h,l=d.high.values,d.low.values; n=len(h)
    seq=pivots(h,l,pct)
    lo=np.full(n,np.nan); hi=np.full(n,np.nan)
    P=[]; p=0
    curH=curL=np.nan
    for i in range(n):
        while p<len(seq) and seq[p][3]<=i:
            idx,val,kind,_=seq[p]
            if kind=='H': curH=val
            else: curL=val
            P.append((idx,val,kind)); p+=1
        if not np.isnan(curH) and not np.isnan(curL):
            hh,ll=curH,curL
            if h[i]>hh: hh=h[i]
            if l[i]<ll: ll=l[i]
            lo[i],hi[i]=ll,hh
    return lo,hi,seq
