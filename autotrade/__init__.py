"""autotrade 패키지 — engine_v3 기반 자동매매

구성:
  config.py      — config.yaml 로더, 상수
  common.py      — 공통 유틸 (KST 시각, 거래일, 로깅, 상태 파일 I/O)
  state.py       — positions, orders, equity 파일 I/O (멱등성)
  datafeed.py    — KIS에서 지수·종목 당일 OHLC 수집
  judge.py       — 20:10 판정 (engine_v3 호출 → orders_YYYYMMDD.json)
  broker.py      — KIS 주문 어댑터 (매수/매도/STOP, 멱등성)
  sell.py        — 08:50 매도 집행
  buy.py         — 15:18 매수 집행
  stoploss.py    — STOP 등록 (08:55) + 체크포인트 (09:05~15:15) + 체결 확인 (15:35)
  reconcile.py   — 08:20 잔고 대사
  report.py      — 텔레그램 보고 포맷
  main.py        — CLI 디스패처
"""
__version__ = "0.1.0-dev"
