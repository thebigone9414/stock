"""insider_backtest/verify.py — 검증 리포트 + README

지시서 §3:
  1. 연도별 행 수 (insider_P 전체, 임원·이사만, events, price_available 비율)
  2. JPM 2016-02-11 Dimon 매수 500,000주 재현
  3. ticker_mismatch.csv (yfinance shortName vs issuer_name 첫 단어 다른 경우)
  4. SPY fwd_252 샘플 검증
  5. 각 파일 크기·행 수
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT_DIR, PRICES_DIR, get_logger

log = get_logger("verify")


def _file_summary(name: str) -> str:
    p = OUT_DIR / name
    if not p.exists():
        return f"  - {name}: 없음"
    size_mb = p.stat().st_size / 1024 / 1024
    try:
        if name.endswith(".csv.gz"):
            n = len(pd.read_csv(p, usecols=[0]))
            return f"  - {name}: {n:,}행, {size_mb:.1f}MB"
        if name.endswith(".npz"):
            d = np.load(p, allow_pickle=True)
            paths = d.get("paths")
            return f"  - {name}: paths {paths.shape if paths is not None else '?'}, {size_mb:.1f}MB"
    except Exception as e:
        return f"  - {name}: {size_mb:.1f}MB (파싱 실패: {e})"
    return f"  - {name}: {size_mb:.1f}MB"


def yearly_counts() -> pd.DataFrame:
    P = pd.read_csv(OUT_DIR / "insider_P.csv.gz", dtype=str, parse_dates=["filing_date"])
    P["year"] = P["filing_date"].dt.year
    P["off_dir"] = ((P["is_officer"].astype(int) == 1) | (P["is_director"].astype(int) == 1)).astype(int)

    ev = pd.read_csv(OUT_DIR / "events.csv.gz", parse_dates=["filing_date"])
    ev["year"] = ev["filing_date"].dt.year

    g_P = P.groupby("year").agg(
        p_rows=("accession", "count"),
        off_dir=("off_dir", "sum"),
    )
    g_ev = ev.groupby("year").agg(
        ev_rows=("accession", "count"),
        price_avail=("price_available", "mean"),
    )
    out = g_P.join(g_ev, how="outer").fillna(0)
    out["price_avail"] = (out["price_avail"] * 100).round(1)
    return out


def check_jpm_dimon() -> dict:
    """JPM 2016-02-11 Dimon 매수 재현 검사."""
    P = pd.read_csv(OUT_DIR / "insider_P.csv.gz", dtype=str, parse_dates=["trans_date"])
    hit = P[
        (P["ticker"] == "JPM")
        & (P["trans_date"].dt.strftime("%Y-%m-%d") == "2016-02-11")
        & (P["owner_name"].str.contains("DIMON", case=False, na=False))
    ]
    if hit.empty:
        return {"found": False}
    r = hit.iloc[0]
    return {
        "found":  True,
        "shares": float(r["shares"]),
        "price":  float(r["price"]),
        "value":  float(r["shares"]) * float(r["price"]),
        "role":   r["role"],
    }


def ticker_mismatch(sample: int = 500) -> int:
    """yfinance shortName vs issuer_name 첫 단어 비교. 다른 종목 → ticker_mismatch.csv"""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance 없음 — ticker_mismatch skip")
        return 0
    P = pd.read_csv(OUT_DIR / "insider_P.csv.gz", dtype=str)
    pairs = P.dropna(subset=["ticker", "issuer_name"]).groupby("ticker")["issuer_name"].first()
    rows = []
    for i, (t, name) in enumerate(pairs.items()):
        if i >= sample: break
        try:
            info = yf.Ticker(t).info
            short = (info.get("shortName") or info.get("longName") or "").strip()
            if not short:
                continue
            n1 = str(name).strip().split()[0].upper()
            s1 = short.split()[0].upper()
            if n1[:4] != s1[:4]:
                rows.append({"ticker": t, "issuer_first": n1, "yf_short": short})
        except Exception:
            pass
    if rows:
        pd.DataFrame(rows).to_csv(OUT_DIR / "ticker_mismatch.csv", index=False)
    return len(rows)


def spy_sample_check() -> dict:
    """SPY fwd_252 아무 날짜 샘플 검증."""
    ev = pd.read_csv(OUT_DIR / "events.csv.gz")
    s = ev[(ev["price_available"] == 1) & ev["spy_fwd_252"].notna()].sample(n=1, random_state=42)
    r = s.iloc[0]
    # SPY 실제 fwd_252 계산
    spy = pd.read_parquet(PRICES_DIR / "SPY.parquet")
    spy["Date"] = pd.to_datetime(spy["Date"])
    fd = pd.to_datetime(r["filing_date"])
    i1 = int(spy["Date"].searchsorted(fd, side="right"))
    if i1 >= len(spy):
        return {"ok": False}
    j = min(i1 + 252, len(spy) - 1)
    base = spy["Adj Close"].iloc[i1]
    expected = spy["Adj Close"].iloc[j] / base - 1
    return {
        "accession":  r["accession"],
        "filing":     r["filing_date"],
        "events_fwd_252_spy": float(r["spy_fwd_252"]),
        "expected_fwd_252_spy": float(expected),
        "diff":       abs(float(r["spy_fwd_252"]) - float(expected)),
    }


def generate_readme() -> str:
    lines = ["# Insider Form 4 백테스트용 데이터 추출 v1 — 결과\n"]

    lines.append("## 1. 파일 요약\n")
    for f in ("insider_P.csv.gz", "insider_S_agg.csv.gz", "events.csv.gz",
              "paths.npz", "prices_bench.csv.gz", "ticker_mismatch.csv"):
        lines.append(_file_summary(f))
    lines.append("")

    lines.append("## 2. 연도별 행 수\n")
    try:
        tbl = yearly_counts()
        lines.append("```")
        lines.append(tbl.to_string())
        lines.append("```")
    except Exception as e:
        lines.append(f"(실패: {e})")
    lines.append("")

    lines.append("## 3. JPM 2016-02-11 Dimon 매수 재현\n")
    try:
        r = check_jpm_dimon()
        if r["found"]:
            lines.append(f"- 발견: shares={r['shares']:,.0f}, price=${r['price']:.2f}, value=${r['value']:,.0f}, role={r['role']}")
            lines.append(f"- 기대: 500,000주, ~$53.17, ~$26.6M, CEO")
        else:
            lines.append("- 발견되지 않음 (데이터 확인 필요)")
    except Exception as e:
        lines.append(f"(실패: {e})")
    lines.append("")

    lines.append("## 4. SPY fwd_252 샘플 검증\n")
    try:
        s = spy_sample_check()
        lines.append(f"```")
        for k, v in s.items():
            lines.append(f"  {k}: {v}")
        lines.append(f"```")
    except Exception as e:
        lines.append(f"(실패: {e})")
    lines.append("")

    lines.append("## 5. ticker_mismatch\n")
    try:
        mm = ticker_mismatch(sample=200)
        lines.append(f"- 샘플 200개 중 불일치 {mm}건 (ticker_mismatch.csv)")
    except Exception as e:
        lines.append(f"(실패: {e})")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    args = p.parse_args()
    txt = generate_readme()
    out = OUT_DIR / "README.md"
    out.write_text(txt, encoding="utf-8")
    log.info(f"README 저장: {out}")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
