"""
새 규칙 v3 일일 판정 - 코스피200 11패턴
사용:
  python3 daily_v3.py                                   k200.csv 마지막 행(=오늘 종가) 기준 판정
  python3 daily_v3.py --add 2026-09-28 1130.5 1145.2 1120.1 1140.3
                                                        오늘 행(시가 고가 저가 종가) 추가/덮어쓰기 후 판정
  python3 daily_v3.py --dep 44858790                    예수금 지정 (기본값은 아래 DEP)
보유 포지션은 positions_v3.csv (패턴,진입일,진입가,계약수[,손절]) 에 기록. 진입일=신호일, 진입가=신호일 지수 종가.
운용 수단은 KODEX 레버리지 예수금 20%씩(동시 5건), 숏 #11은 KODEX 인버스 40%. 매수는 신호 다음날 15:20~15:30 동시호가(종가), 매도는 청산 신호 다음날 시가. 손절도 종가 판정(종가 < 손절선 → 다음날 시가 매도), 장중 감시 없음.
패턴·지표 정의는 백테스트와 같은 코드(lib.py, hunt.py)를 그대로 씀.
"""
import sys, os, warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
BASE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BASE)
from lib import build
from hunt import make

DEP=25_000_000; MULT=50_000; RISK=0.03; NAVCAP=1.5; STOPK=5.0; STOPCAP=0.10; MAXPOS=5
POSF=os.path.join(BASE,'positions_v3.csv'); K200=os.path.join(BASE,'k200.csv')

# 번호: (트리거, 상태들, 방향, 이름, 청산 설명)
PAT={
 1:('MACDgc',['ma62up'],1,'MACD 골든크로스 + 62일선 상승','MACD < 시그널 (데드크로스)'),
 2:('LL20dn',['ma62up','ma200up'],1,'20일 신저가 이탈 + 62·248일선 상승','종가 > 전일까지 10일 최고가'),
 3:('RSI50up',['ma62up','mh<0'],1,'RSI 50 상향돌파 + 62일선 상승 + 히스토 음수','RSI < 45 또는 RSI > 75'),
 4:('GC5_21',['ma62up','r5>0'],1,'5일선/21일선 골든크로스 + 62일선 상승 + 5일 수익 양수','5일선 < 21일선'),
 5:('MA62up',['ma62up','rsi40-60'],1,'62일선 상향돌파 + 62일선 상승 + RSI 40~60','종가 < 62일선'),
 6:('up3',['ma62up','mh>0'],1,'3일 연속 상승 + 62일선 상승 + 히스토 양수','종가 < 5일선'),
 7:('dn3',['C>ma200','r60<0'],1,'3일 연속 하락 + 248일선 위 + 60일 수익 음수','종가 > 전일까지 10일 최고가 또는 종가 < 진입일 20일 최저가'),
 8:('F0.764dn',['gap_ma21n'],1,'F0.764 하향이탈 + 21일선 아래 2% 이상','종가 > 0.764 레벨'),
 9:('F0.309dn',['ma62dn'],1,'F0.309 하향이탈 + 62일선 하락','종가 > 0.309 레벨'),
 10:('MA21up',['atrq0.33-0.66','r5<0'],1,'21일선 상향돌파 + 변동성 중간 + 5일 수익 음수','종가 < 21일선'),
 11:('MA21dn',['r5>0','r20>0'],-1,'[숏] 21일선 하향이탈 + 5일·20일 수익 양수','종가 > 21일선 (환매)'),
}
DESC={'ma62up':'62일선 기울기 > 0','ma200up':'248일선 기울기 > 0','ma62dn':'62일선 기울기 < 0','ma200dn':'248일선 기울기 < 0',
      'mh<0':'MACD 히스토 < 0','mh>0':'MACD 히스토 > 0','r5>0':'5일 수익률 > 0','r5<0':'5일 수익률 < 0','r20>0':'20일 수익률 > 0',
      'r60<0':'60일 수익률 < 0','rsi40-60':'RSI 40~60','C>ma200':'종가 > 248일선','gap_ma21n':'종가/21일선-1 < -2%',
      'atrq0.33-0.66':'변동성 분위 0.33~0.66','align':'정배열 5>21>62'}

def add_bar(dt,o,h,l,c,k200=None):
    """k200.csv 에 하루 행 추가/갱신 (코스피200 지수 시가 고가 저가 종가). 같은 날짜가 있으면 덮어씀"""
    k200=k200 or K200
    d=pd.read_csv(k200,parse_dates=['date']).set_index('date')
    d.loc[pd.Timestamp(dt)]=[float(o),float(h),float(l),float(c)]; d=d.sort_index(); d.to_csv(k200)
    return d

def judge(dep=DEP, posf=None, k200=None):
    """자동매매용 구조화 판정 (k200.csv 마지막 행 = 오늘 종가 기준). 출력 없음.
    반환 dict:
      date, close, chg, ma5, ma21, ma62, sl62, ma248, sl248, rsi, atr, stop_dist(손절폭 비율)
      positions: [dict(pat, dir(1 롱/-1 숏), name, entry_date, entry_px, qty, stop, pnl_pct,
                       stop_close(오늘 종가가 손절선 밖 → 내일 시가 매도. 운용 규격의 손절 판정), stop_hit(오늘 저가/고가가 손절선을 스쳤는지. 참고용),
                       exit_signal(종가 청산 조건 성립 → 내일 시가 매도), sell(내일 시가 매도 여부 = stop_close or exit_signal), why('손절'/'청산'/''), exit_desc)]
      signals:   [dict(pat, dir, name, stop(지수 손절선), exit_desc, vehicle('KODEX 레버리지'/'KODEX 인버스'), frac(0.2/0.4), amount(dep x frac), dup(이미 보유 중 → 추가 진입 없음))]
      near:      [dict(pat, name, missing=[조건 설명])]  참고용
      free_slots: 오늘 신호 중 실제로 진입 가능한 건수 (동시 보유 한도 MAXPOS 기준)"""
    posf=posf or POSF; k200=k200 or K200
    x=build(k200); T,S=make(x); idx=x['idx']; C,O,HI,LO,atr=x['C'],x['O'],x['HI'],x['LO'],x['atr']
    i=len(C)-1
    out=dict(date=idx[i],close=float(C[i]),chg=float(C[i]/C[i-1]-1),ma5=float(x['ma5'][i]),ma21=float(x['ma21'][i]),ma62=float(x['ma62'][i]),sl62=float(x['sl62'][i]),
             ma248=float(x['ma200'][i]),sl248=float(x['sl200'][i]),rsi=float(x['rsi'][i]),atr=float(atr[i]),stop_dist=float(min(STOPK*atr[i]/C[i],STOPCAP)),positions=[],signals=[],near=[])
    try: P=pd.read_csv(posf,dtype={'패턴':int})
    except FileNotFoundError: P=pd.DataFrame(columns=['패턴','진입일','진입가','계약수'])
    held=set()
    for _,r in P.iterrows():
        n=int(r.패턴); tk,sts,dr,nm,exd=PAT[n]; held.add(n)
        j=int(idx.searchsorted(pd.Timestamp(r.진입일)))
        st=float(r.진입가)-dr*min(STOPK*atr[j],STOPCAP*C[j])
        if '손절' in P.columns and pd.notna(r.get('손절')) and str(r.get('손절')).strip()!='': st=float(r.손절)
        hit=bool((LO[i]<=st) if dr==1 else (HI[i]>=st)); hc=bool((C[i]<=st) if dr==1 else (C[i]>=st)); ex=bool(T[tk][1](j,i))
        out['positions'].append(dict(pat=n,dir=dr,name=nm,entry_date=str(r.진입일),entry_px=float(r.진입가),qty=int(r.계약수),stop=float(st),
                                     pnl_pct=float((C[i]-float(r.진입가))*dr/float(r.진입가)),stop_close=hc,stop_hit=hit,exit_signal=ex,sell=(hc or ex),why=('손절' if hc else ('청산' if ex else '')),exit_desc=exd))
    for n,(tk,sts,dr,nm,exd) in PAT.items():
        if bool(T[tk][0][i]) and all(bool(S[s][i]) for s in sts):
            fr=0.20 if dr==1 else 0.40
            out['signals'].append(dict(pat=n,dir=dr,name=nm,stop=float(C[i]-dr*min(STOPK*atr[i],STOPCAP*C[i])),exit_desc=exd,
                                       vehicle='KODEX 레버리지' if dr==1 else 'KODEX 인버스',frac=fr,amount=float(dep*fr),dup=(n in held)))
        elif bool(T[tk][0][i]):
            out['near'].append(dict(pat=n,name=nm,missing=[DESC[s] for s in sts if not bool(S[s][i])]))
    out['free_slots']=max(0,MAXPOS-len(held))
    return out

def main():
    args=sys.argv[1:]; dep=DEP
    if '--dep' in args:
        k=args.index('--dep'); dep=int(float(args[k+1])); del args[k:k+2]
    if '--add' in args:
        k=args.index('--add'); dt,o,h,l,c=args[k+1],*map(float,args[k+2:k+6])
        add_bar(dt,o,h,l,c)
        print(f'k200.csv: {dt} 행 기록 (시 {o} 고 {h} 저 {l} 종 {c})')
    x=build(K200); T,S=make(x); idx=x['idx']; C,O,HI,LO,atr=x['C'],x['O'],x['HI'],x['LO'],x['atr']
    i=len(C)-1; dt=idx[i]
    sd=min(STOPK*atr[i]/C[i],STOPCAP); w=min(RISK/sd,NAVCAP); nom=dep*w; q=int(nom//(C[i]*MULT))
    print('='*66); print(f'  코스피200  {dt:%Y-%m-%d(%a)}   종가 {C[i]:,.2f} ({C[i]/C[i-1]-1:+.2%})   고 {HI[i]:,.2f} 저 {LO[i]:,.2f}'); print('='*66)
    print(f"  5일 {x['ma5'][i]:,.1f}  21일 {x['ma21'][i]:,.1f}  62일 {x['ma62'][i]:,.1f} (기울기 {x['sl62'][i]:+.1f})  248일 {x['ma200'][i]:,.1f} (기울기 {x['sl200'][i]:+.1f})")
    print(f"  RSI {x['rsi'][i]:.1f}  MACD {x['macd'][i]:+.2f} 시그널 {x['msig'][i]:+.2f} 히스토 {x['mhist'][i]:+.2f}  수익률 5일 {x['r5'][i]:+.1%} 20일 {x['r20'][i]:+.1%} 60일 {x['r60'][i]:+.1%}")
    print(f"  ATR20 {atr[i]:.2f} ({atr[i]/C[i]:.2%}, 분위 {x['atrq'][i]:.2f})  전일까지 10일 최고 {x['hh10'][i-1]:,.2f}  20일 최저 {x['ll20'][i-1]:,.2f}")
    print(f"  F0.309 {x['LV'][i,2]:,.2f}  F0.764 {x['LV'][i,7]:,.2f}")
    print(f"\n  비중: KODEX 레버리지 1건 = 예수금 20% = {dep*0.20:,.0f}원 (노출 {dep*0.40:,.0f}원, 동시 5건)   손절폭 min(5 ATR, 10%) = {sd:.1%}")
    print(f"       (선물이면 노출 40% = 미니 {dep*0.40/(C[i]*MULT):.2f}계약 - 예수금 {C[i]*MULT/0.40/1e8:.1f}억 이상에서 1계약)")
    # 보유 점검
    print('\n■ 보유 점검'); npos=0; held=set()
    try: P=pd.read_csv(POSF,dtype={'패턴':int})
    except FileNotFoundError: P=pd.DataFrame(columns=['패턴','진입일','진입가','계약수'])
    for _,r in P.iterrows():
        n=int(r.패턴); tk,sts,dr,nm,exd=PAT[n]; held.add(n); npos+=1
        j=int(idx.searchsorted(pd.Timestamp(r.진입일)))
        st=float(r.진입가)-dr*min(STOPK*atr[j],STOPCAP*C[j])
        if '손절' in P.columns and pd.notna(r.get('손절')) and str(r.get('손절')).strip()!='': st=float(r.손절)
        hit=(LO[i]<=st) if dr==1 else (HI[i]>=st); hc=(C[i]<=st) if dr==1 else (C[i]>=st)
        ex=T[tk][1](j,i)
        pnl=(C[i]-float(r.진입가))*dr; R=pnl/(STOPK*atr[j])
        flag='★ 손절 (종가가 손절선 밖) → 내일 시가 매도' if hc else ('★ 청산 신호 → 내일 시가 매도' if ex else ('보유 유지 (장중에 손절선을 스쳤지만 종가 회복)' if hit else '보유 유지'))
        print(f"  #{n} {nm}  {'롱' if dr==1 else '숏'} {r.진입일} {float(r.진입가):,.2f} x{int(r.계약수)}  손절 {st:,.2f}  평가 {pnl/float(r.진입가)*100:+.2f}%  → {flag}")
        print(f"       청산 조건: {exd}")
    if npos==0: print('  없음')
    # 오늘 신호
    print('\n■ 오늘 신호'); fired=[]
    for n,(tk,sts,dr,nm,exd) in PAT.items():
        trig=bool(T[tk][0][i]); conds=[(s,bool(S[s][i])) for s in sts]
        if trig and all(v for _,v in conds):
            fired.append(n); dup=n in held
            print(f"  #{n} {nm}  → {'롱 매수' if dr==1 else '숏 매도'}"+(' (이미 보유 중 → 추가 진입 없음)' if dup else ''))
            print(f"       지수 손절선 {C[i]-dr*min(STOPK*atr[i],STOPCAP*C[i]):,.2f} (종가 판정)   청산: {exd}   → 다음날 15:20~15:30 동시호가 {'KODEX 레버리지' if dr==1 else 'KODEX 인버스'} {dep*(0.20 if dr==1 else 0.40):,.0f}원 시장가")
    if not fired: print('  없음 (11개 패턴 모두 미충족)')
    # 근접 조건 (참고): 트리거는 맞는데 상태가 안 맞는 것
    near=[(n,PAT[n][3],[(DESC[s],v) for s,v in [(s,bool(S[s][i])) for s in PAT[n][1]] if not v]) for n in PAT if bool(T[PAT[n][0]][0][i]) and n not in fired]
    if near:
        print('\n  (참고) 사건은 있었지만 조건 미달:')
        for n,nm,miss in near: print(f"    #{n} {nm} - 미달: {', '.join(m for m,_ in miss)}")
    if len(fired)+npos>MAXPOS: print(f'\n  ※ 동시 보유 한도 {MAXPOS}건 초과 - 앞 번호부터 {MAXPOS-npos}건만')
    print()

if __name__=='__main__': main()
