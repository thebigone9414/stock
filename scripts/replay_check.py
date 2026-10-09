#!/usr/bin/env python3
"""scripts/replay_check.py — 지시서 §10-2 ETF 11패턴 백테스트 리플레이 검수

engine_v3 를 과거 날짜(k200.csv 를 그 날까지 자른 상태)로 호출해서
지시서가 명시한 신호·청산·손절이 그대로 재현되는지 확인한다.

지시서 §10-2 체크리스트 (ETF 운용 포트폴리오 maxpos=5, lev=2):
  2026-07-28  #2 신호     → 7/29 매수 → 7/29 손절
  2026-07-30  #7 신호     → 7/31 매수 → 8/13 청산
  2026-08-05  #1 신호     → 8/6  매수 → 9/17 매도
  2026-09-04  #10 신호    → 9/7  매수 → 9/15 매도
  2026-09-14  #7 신호     → 9/15 매수 → 보유 중

실행:
  python scripts/replay_check.py              # 핵심 체크리스트
  python scripts/replay_check.py --full       # roll.multi_mtm 전체 백테스트 거래
  python scripts/replay_check.py --date 2026-07-28  # 특정일만
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent.parent
ENGINE = BASE / "engine_v3"
sys.path.insert(0, str(ENGINE))

# 지시서 §10-2 핵심 체크리스트
EXPECTED_SIGNALS: dict[str, dict] = {
    "2026-07-28": {"pat": 2,  "name": "20일 신저가 이탈 + 62·248일선 상승",       "followup": "7/29 매수 → 7/29 손절"},
    "2026-07-30": {"pat": 7,  "name": "3일 연속 하락 + 248일선 위 + 60일 수익 음수", "followup": "7/31 매수 → 8/13 청산"},
    "2026-08-05": {"pat": 1,  "name": "MACD 골든크로스 + 62일선 상승",             "followup": "8/6 매수 → 9/17 매도"},
    "2026-09-04": {"pat": 10, "name": "21일선 상향돌파 + 변동성 중간 + 5일 수익 음수", "followup": "9/7 매수 → 9/15 매도"},
    "2026-09-14": {"pat": 7,  "name": "3일 연속 하락 + 248일선 위 + 60일 수익 음수", "followup": "9/15 매수 → 보유 중"},
    # 월요일 가동 시점의 신호 확인
    "2026-10-08": {"pat": 7,  "name": "3일 연속 하락 + 248일선 위 + 60일 수익 음수", "followup": "10/13 매수 예정"},
}


def _trimmed_k200(end_date: str) -> str:
    """k200.csv 를 end_date 까지 자른 임시 파일 경로 반환."""
    k = pd.read_csv(ENGINE / "k200.csv", parse_dates=["date"])
    cutoff = pd.Timestamp(end_date)
    trimmed = k[k["date"] <= cutoff].copy()
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="k200_replay_")
    os.close(fd)
    trimmed.to_csv(path, index=False)
    return path


def _empty_posf() -> str:
    """빈 positions_v3.csv (헤더만) 임시 파일."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="pos_replay_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write("패턴,진입일,진입가,계약수,손절\n")
    return path


def check_signal(date: str, expected_pat: int, expected_name: str, followup: str) -> bool:
    """해당 날짜까지 데이터로 judge 돌려서 expected_pat 신호가 뜨는지 확인."""
    import daily_v3

    k_path = _trimmed_k200(date)
    pos_path = _empty_posf()
    try:
        result = daily_v3.judge(dep=28_000_000, posf=pos_path, k200=k_path)
    finally:
        os.unlink(k_path)
        os.unlink(pos_path)

    actual_pats = [s["pat"] for s in result.get("signals", []) if not s.get("dup")]
    close = result.get("close", 0)
    ok = expected_pat in actual_pats

    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {date}  pat#{expected_pat} 기대")
    print(f"         K200 종가 {close:,.2f}")
    print(f"         신호 (전체): {actual_pats or '없음'}")
    print(f"         기대 패턴 설명: {expected_name}")
    print(f"         후속: {followup}")

    # 상세: expected_pat의 신호 내용
    sig_match = next((s for s in result.get("signals", []) if s["pat"] == expected_pat), None)
    if sig_match:
        print(f"         ✓ 매칭 신호: vehicle={sig_match['vehicle']}  amount={sig_match['amount']:,.0f}  "
              f"stop={sig_match['stop']:.2f}  dup={sig_match['dup']}")
    print()
    return ok


def check_all() -> int:
    print("=" * 70)
    print("[리플레이 검수] 지시서 §10-2 ETF 핵심 체크리스트")
    print("=" * 70)
    print()
    results = []
    for date, exp in EXPECTED_SIGNALS.items():
        ok = check_signal(date, exp["pat"], exp["name"], exp["followup"])
        results.append((date, exp["pat"], ok))

    print("=" * 70)
    passes = sum(1 for _, _, ok in results if ok)
    total = len(results)
    print(f"[리플레이 검수] 종합: {passes}/{total} 통과")
    for d, p, ok in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {d}  pat#{p}")
    print("=" * 70)
    return 0 if passes == total else 1


def check_date(date: str) -> int:
    exp = EXPECTED_SIGNALS.get(date)
    if not exp:
        print(f"ERROR: {date} 는 체크리스트에 없음 (등록된 날짜: {list(EXPECTED_SIGNALS)})")
        return 1
    ok = check_signal(date, exp["pat"], exp["name"], exp["followup"])
    return 0 if ok else 1


def check_full() -> int:
    """roll.multi_mtm 전체 백테스트 돌려 2026년 7월 이후 거래 전부 출력."""
    import pandas as pd
    import numpy as np
    from lib import build
    from vehicle import patterns
    import roll

    print("=" * 70)
    print("[전체 백테스트] roll.multi_mtm (2026-07 ~ 2026-10-08)")
    print("=" * 70)

    x = build(str(ENGINE / "k200.csv"))
    P = patterns(x)
    P11 = {k: v for k, v in P.items() if k not in ("MHup|ma62up", "MA21dn|ma200dn+align")}
    tr, cv = roll.multi_mtm(
        x, P11,
        lev=2, cash_frac=0.2, maxpos=5, stopcap=0.10,
        entry_at="next_close", exit_at="open", stop_at="close",
    )

    df = pd.DataFrame(tr)
    if df.empty:
        print("거래 없음")
        return 0

    # 2026-07 이후만
    for date_col in ("ent_dt", "entry_date", "진입일", "entry"):
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.sort_values(date_col)
            recent = df[df[date_col] >= pd.Timestamp("2026-07-01")]
            print(f"총 거래 {len(df)}건  2026-07 이후 {len(recent)}건\n")
            print(recent.to_string(index=False, max_colwidth=30))
            break
    else:
        print(df.tail(30).to_string(index=False, max_colwidth=30))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="특정일만 검수 (YYYY-MM-DD)")
    p.add_argument("--full", action="store_true", help="roll.multi_mtm 전체 백테스트 거래 출력")
    args = p.parse_args()

    if args.full:
        return check_full()
    if args.date:
        return check_date(args.date)
    return check_all()


if __name__ == "__main__":
    sys.exit(main())
