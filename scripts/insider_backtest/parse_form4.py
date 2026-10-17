"""insider_backtest/parse_form4.py — Form 4 데이터 조립

지시서 §2-1 / §2-4:
  - SUBMISSION ⨝ REPORTINGOWNER ⨝ NONDERIV_TRANS ⨝ FOOTNOTES
  - 공개시장 매수 (TRANS_CODE=P, TRANS_ACQUIRED_DISP_CD=A, value>=5000, 임원·이사·10%주주)
    → insider_P.csv.gz
  - 매도 (S, 임원·이사, value>=10000) → insider_S_agg.csv.gz
  - 티커 정규화, 역할 분류, fn_flag 태깅
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import RAW_DIR, OUT_DIR, get_logger

log = get_logger("parse_form4")

# ── 티커 정규화 ─────────────────────────────────────────
def normalize_ticker(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip().upper().replace(" ", "")
    if s in ("N/A", "NONE", "NULL", ""):
        return ""
    s = s.replace(".", "-")
    # 과도한 특수문자 제거 (알파벳·숫자·'-' 만 허용)
    s = re.sub(r"[^A-Z0-9\-]", "", s)
    return s


# ── 역할 분류 ───────────────────────────────────────────
_CEO_RE   = re.compile(r"(chief\s+executive|president\s+and\s+ceo|\bceo\b|principal\s+executive)", re.I)
_CFO_RE   = re.compile(r"(chief\s+financial|\bcfo\b|principal\s+financial)", re.I)
_CHAIR_RE = re.compile(r"(chair(man|person|woman)?)", re.I)
_COO_RE   = re.compile(r"(chief\s+operating|\bcoo\b)", re.I)


def role_from(title: str, is_officer: int, is_director: int, is_tenpct: int) -> str:
    t = (title or "")
    if is_officer:
        if _CEO_RE.search(t):   return "CEO"
        if _CFO_RE.search(t):   return "CFO"
        if _CHAIR_RE.search(t): return "CHAIR"
        if _COO_RE.search(t):   return "COO"
        return "OTHER_OFFICER"
    if is_director:
        return "DIRECTOR"
    if is_tenpct:
        return "TENPCT"
    return "OTHER"


# ── fn_flag 태깅 ────────────────────────────────────────
_FN_TAGS = [
    ("10b5-1",                r"10b5[-\s]?1"),
    ("subscription",          r"subscription"),
    ("private placement",     r"private\s+placement"),
    ("registered direct",     r"registered\s+direct"),
    ("offering",              r"\boffering\b"),
    ("initial public offering", r"initial\s+public\s+offering"),
    ("underwrit",             r"underwrit"),
    ("dividend reinvestment", r"dividend\s+reinvestment"),
    ("employee stock purchase", r"employee\s+stock\s+purchase"),
    ("401(k)",                r"401\(k\)"),
    ("409A",                  r"409A"),
    ("deferred compensation", r"deferred\s+compensation"),
    ("cashless",              r"cashless"),
    ("exercise",              r"exercis"),
    ("gift",                  r"\bgift"),
    ("rights offering",       r"rights\s+offering"),
    ("warrant",               r"warrant"),
    ("conversion",            r"conversion"),
]
_FN_PATTERNS = [(tag, re.compile(pat, re.I)) for tag, pat in _FN_TAGS]


def tag_fn_flags(text: str) -> str:
    if not text:
        return ""
    tags = [tag for tag, pat in _FN_PATTERNS if pat.search(text)]
    return ";".join(tags)


# ── 파일 로더 (quarter 기준 TSV) ──────────────────────────
def _read_tsv(quarter_dir: Path, name: str) -> pd.DataFrame | None:
    for cand in (name, name.upper(), name.lower()):
        p = quarter_dir / cand
        if p.exists():
            try:
                return pd.read_csv(p, sep="\t", dtype=str, low_memory=False, encoding="latin1")
            except Exception as e:
                log.warning(f"[{quarter_dir.name}] {cand} 읽기 실패: {e}")
                return None
    return None


# ── 분기별 처리 ─────────────────────────────────────────
def process_quarter(quarter_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(insider_P 분기 조각, insider_S 분기 조각) 반환."""
    sub = _read_tsv(quarter_dir, "SUBMISSION.tsv")
    own = _read_tsv(quarter_dir, "REPORTINGOWNER.tsv")
    non = _read_tsv(quarter_dir, "NONDERIV_TRANS.tsv")
    fn  = _read_tsv(quarter_dir, "FOOTNOTES.tsv")
    if sub is None or own is None or non is None:
        log.warning(f"[{quarter_dir.name}] 필수 테이블 누락 — skip")
        return pd.DataFrame(), pd.DataFrame()

    # 컬럼명 upper
    for df in (sub, own, non, fn if fn is not None else None):
        if df is None: continue
        df.columns = [c.upper() for c in df.columns]

    # SUBMISSION 핵심
    sub = sub.rename(columns={
        "ACCESSION_NUMBER":       "accession",
        "DOCUMENT_TYPE":          "doc_type",
        "FILING_DATE":            "filing_date",
        "ISSUERCIK":              "issuer_cik",
        "ISSUERNAME":             "issuer_name",
        "ISSUERTRADINGSYMBOL":    "ticker_raw",
    })
    sub = sub[["accession", "doc_type", "filing_date", "issuer_cik", "issuer_name", "ticker_raw"]]
    sub["ticker"] = sub["ticker_raw"].map(normalize_ticker)
    sub["filing_date"] = pd.to_datetime(sub["filing_date"], errors="coerce").dt.strftime("%Y-%m-%d")

    # 10b5-1 체크박스 열 (2023-04 이후 양식)
    if "AFF_10B5ONE" in sub.columns:
        sub["aff10b5one"] = sub["AFF_10B5ONE"]
    else:
        sub["aff10b5one"] = ""

    # REPORTINGOWNER
    rel_cols = {
        "ISDIRECTOR":  "is_director",
        "ISOFFICER":   "is_officer",
        "ISTENPERCENTOWNER": "is_tenpct",
        "ISOTHER":     "is_other",
    }
    for src, dst in rel_cols.items():
        if src in own.columns:
            own[dst] = (own[src].astype(str).str.strip() == "1").astype(int)
        else:
            own[dst] = 0
    own = own.rename(columns={
        "ACCESSION_NUMBER":  "accession",
        "RPTOWNERCIK":       "owner_cik",
        "RPTOWNERNAME":      "owner_name",
        "RPTOWNER_TITLE":    "officer_title",
    })
    keep_own = ["accession", "owner_cik", "owner_name", "officer_title",
                "is_director", "is_officer", "is_tenpct", "is_other"]
    for c in keep_own:
        if c not in own.columns:
            own[c] = ""
    own = own[keep_own]

    # NONDERIV_TRANS
    non = non.rename(columns={
        "ACCESSION_NUMBER":           "accession",
        "TRANS_DATE":                 "trans_date",
        "TRANS_CODE":                 "trans_code",
        "TRANS_ACQUIRED_DISP_CD":     "ad_code",
        "TRANS_SHARES":               "shares",
        "TRANS_PRICEPERSHARE":        "price",
        "SHRS_OWND_FOLWNG_TRANS":     "shares_after",
        "TRANS_TIMELINESS":           "timeliness",
        "DIRECT_INDIRECT_OWNERSHIP":  "direct_indirect",
        "NATURE_OF_OWNERSHIP":        "nature_of_ownership",
    })
    for c in ["shares", "price", "shares_after"]:
        if c in non.columns:
            non[c] = pd.to_numeric(non[c], errors="coerce")
    non["trans_date"] = pd.to_datetime(non["trans_date"], errors="coerce").dt.strftime("%Y-%m-%d")

    # FOOTNOTES 매핑: accession 안에서 인덱스 묶음
    if fn is not None:
        fn = fn.rename(columns={
            "ACCESSION_NUMBER": "accession",
            "FOOTNOTE_TXT":     "ftxt",
        })
        if "accession" in fn.columns and "ftxt" in fn.columns:
            fn_grp = fn.groupby("accession")["ftxt"].apply(lambda s: " | ".join(str(x) for x in s if pd.notna(x) and str(x).strip()))
        else:
            fn_grp = pd.Series(dtype=str)
    else:
        fn_grp = pd.Series(dtype=str)

    # 조인
    #   SUBMISSION ⨝ NONDERIV_TRANS (accession)
    #   그 결과 ⨝ REPORTINGOWNER (accession) — 보고자가 여럿이면 각 보고자별로 복제
    sn = sub.merge(non, on="accession", how="inner", suffixes=("", "_n"))
    df = sn.merge(own, on="accession", how="inner", suffixes=("", "_o"))

    # ──── insider_P (매수) ────
    P = df[
        (df["trans_code"] == "P")
        & (df["ad_code"].astype(str).str.strip() == "A")
        & (df["shares"].fillna(0) > 0)
        & (df["price"].fillna(0) > 0)
    ].copy()
    P["value"] = P["shares"] * P["price"]
    P = P[P["value"] >= 5000]
    # 보고자 관계 필터 (Other 만인 것은 제외)
    P = P[(P["is_director"] + P["is_officer"] + P["is_tenpct"]) >= 1]

    # role 분류
    P["role"] = P.apply(
        lambda r: role_from(r["officer_title"], int(r["is_officer"]), int(r["is_director"]), int(r["is_tenpct"])),
        axis=1,
    )

    # footnotes
    P["footnotes"] = P["accession"].map(fn_grp).fillna("").astype(str).str.slice(0, 500)
    P["fn_flag"]   = (P["footnotes"].fillna("") + " | " + P["nature_of_ownership"].fillna("")).map(tag_fn_flags)

    P_out_cols = [
        "accession", "doc_type", "filing_date",
        "issuer_cik", "issuer_name", "ticker",
        "owner_cik", "owner_name",
        "is_director", "is_officer", "is_tenpct", "is_other",
        "officer_title", "role",
        "trans_date", "shares", "price", "value",
        "direct_indirect", "nature_of_ownership",
        "shares_after", "timeliness", "aff10b5one",
        "footnotes", "fn_flag",
    ]
    for c in P_out_cols:
        if c not in P.columns:
            P[c] = ""
    P_df = P[P_out_cols].reset_index(drop=True)

    # ──── insider_S (매도) ────
    S = df[
        (df["trans_code"] == "S")
        & (df["shares"].fillna(0) > 0)
        & (df["price"].fillna(0) > 0)
    ].copy()
    S["value"] = S["shares"] * S["price"]
    S = S[S["value"] >= 10000]
    S = S[(S["is_officer"] + S["is_director"]) >= 1]
    if len(S):
        S["role"] = S.apply(
            lambda r: role_from(r["officer_title"], int(r["is_officer"]), int(r["is_director"]), 0),
            axis=1,
        )
        S["has_ceo"] = (S["role"] == "CEO").astype(int)
        S["has_cfo"] = (S["role"] == "CFO").astype(int)
        S_agg = S.groupby(["issuer_cik", "trans_date"], dropna=False).agg(
            n_sellers=("owner_cik", "nunique"),
            value_total=("value", "sum"),
            has_ceo=("has_ceo", "max"),
            has_cfo=("has_cfo", "max"),
        ).reset_index()
    else:
        S_agg = pd.DataFrame(columns=["issuer_cik", "trans_date", "n_sellers", "value_total", "has_ceo", "has_cfo"])

    return P_df, S_agg


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="처리할 특정 분기 (예: 2024q1). 지정 안 하면 raw/ 아래 전부")
    args = p.parse_args()

    quarters = sorted(d for d in RAW_DIR.iterdir() if d.is_dir() and re.match(r"\d{4}q[1-4]$", d.name))
    if args.only:
        quarters = [d for d in quarters if d.name == args.only]

    if not quarters:
        log.error(f"raw/ 아래 분기 폴더 없음 — download_form4 먼저 실행 필요")
        return 1

    log.info(f"처리 대상 분기 {len(quarters)}")

    P_frames = []
    S_frames = []
    for d in quarters:
        try:
            P, S = process_quarter(d)
            P_frames.append(P)
            S_frames.append(S)
            log.info(f"[{d.name}] P {len(P):>7,}건  S {len(S):>7,}건")
        except Exception as e:
            log.exception(f"[{d.name}] 처리 실패: {e}")

    P_all = pd.concat(P_frames, ignore_index=True) if P_frames else pd.DataFrame()
    S_all = pd.concat(S_frames, ignore_index=True) if S_frames else pd.DataFrame()

    if len(P_all) == 0:
        log.error("insider_P 결과 0건 — 파싱 로직·데이터 확인 필요")
        return 1

    out_P = OUT_DIR / "insider_P.csv.gz"
    out_S = OUT_DIR / "insider_S_agg.csv.gz"
    P_all.to_csv(out_P, index=False, compression="gzip")
    S_all.to_csv(out_S, index=False, compression="gzip")

    log.info(f"저장: {out_P}  ({len(P_all):,}건, {out_P.stat().st_size/1024/1024:.1f}MB)")
    log.info(f"저장: {out_S}  ({len(S_all):,}건, {out_S.stat().st_size/1024/1024:.1f}MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
