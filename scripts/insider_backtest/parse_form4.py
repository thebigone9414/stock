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
        # 재시도: 디렉토리에 어떤 파일들이 있는지 확인
        actual = sorted(f.name for f in quarter_dir.iterdir() if f.is_file())
        log.warning(f"[{quarter_dir.name}] 필수 테이블 누락 — skip. 실제 파일 {actual[:10]}")
        return pd.DataFrame(), pd.DataFrame()

    # 로드 직후 상태 로그
    log.info(f"[{quarter_dir.name}] 로드: SUB={len(sub):,} OWN={len(own):,} NON={len(non):,}")

    # 컬럼명 정규화: upper + strip + 공백→underscore
    for df in (sub, own, non, fn if fn is not None else None):
        if df is None: continue
        df.columns = [str(c).strip().upper().replace(" ", "_") for c in df.columns]

    # 첫 분기 처리 시 컬럼명 한 번 로그
    if quarter_dir.name.endswith("q1") or len(sub) < 100:
        log.info(f"[{quarter_dir.name}] SUB cols: {list(sub.columns)[:20]}")
        log.info(f"[{quarter_dir.name}] NON cols: {list(non.columns)[:20]}")
        log.info(f"[{quarter_dir.name}] OWN cols: {list(own.columns)[:20]}")

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
    # SEC 날짜 포맷: "31-MAR-2006" (DD-MMM-YYYY). pandas 자동 추론은 느리고 에러 많음.
    sub["filing_date"] = pd.to_datetime(sub["filing_date"], format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d")

    # 10b5-1 체크박스 열 (2023-04 이후 양식)
    if "AFF_10B5ONE" in sub.columns:
        sub["aff10b5one"] = sub["AFF_10B5ONE"]
    else:
        sub["aff10b5one"] = ""

    # REPORTINGOWNER
    # 핵심 수정: 실제 SEC TSV 는 IS_DIRECTOR 플래그 컬럼이 없고
    #           RPTOWNER_RELATIONSHIP (쉼표 구분 문자열: "Director,Officer" 등)
    #           만 있음.
    if "RPTOWNER_RELATIONSHIP" in own.columns:
        rel = own["RPTOWNER_RELATIONSHIP"].fillna("").astype(str)
        own["is_director"] = rel.str.contains("Director", case=False, regex=False, na=False).astype(int)
        own["is_officer"]  = rel.str.contains("Officer",  case=False, regex=False, na=False).astype(int)
        own["is_tenpct"]   = rel.str.contains("TenPercent|TenPct|10[%\\s]Owner", case=False, regex=True,  na=False).astype(int)
        own["is_other"]    = rel.str.contains("Other",    case=False, regex=False, na=False).astype(int)
    else:
        # 폴백: 혹시 신 포맷에 플래그 컬럼이 있는 경우 (SEC 포맷 변경 대비)
        def _flag(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
            for c in candidates:
                if c in df.columns:
                    raw = df[c].astype(str).str.strip().str.upper()
                    return raw.isin(["1", "Y", "TRUE", "T"]).astype(int)
            return pd.Series(0, index=df.index)
        own["is_director"] = _flag(own, ["IS_DIRECTOR", "ISDIRECTOR"])
        own["is_officer"]  = _flag(own, ["IS_OFFICER", "ISOFFICER"])
        own["is_tenpct"]   = _flag(own, ["IS_TEN_PERCENT_OWNER", "ISTENPERCENTOWNER"])
        own["is_other"]    = _flag(own, ["IS_OTHER", "ISOTHER"])

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
    log.info(f"[{quarter_dir.name}] OWN 관계 분포: dir={own['is_director'].sum():,} off={own['is_officer'].sum():,} ten={own['is_tenpct'].sum():,}")

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
    non["trans_date"] = pd.to_datetime(non["trans_date"], format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d")

    # 거래 코드 분포 로그
    if "trans_code" in non.columns:
        tc_dist = non["trans_code"].value_counts().head(10).to_dict()
        log.info(f"[{quarter_dir.name}] trans_code 분포 top10: {tc_dist}")
    if "ad_code" in non.columns:
        ad_dist = non["ad_code"].value_counts().head(5).to_dict()
        log.info(f"[{quarter_dir.name}] ad_code 분포: {ad_dist}")

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
    log.info(f"[{quarter_dir.name}] sn merge: {len(sn):,}  df merge: {len(df):,}")
    _p1 = df[df["trans_code"] == "P"]
    log.info(f"[{quarter_dir.name}] P 필터: trans_code=='P' → {len(_p1):,}")
    _p2 = _p1[_p1["ad_code"].astype(str).str.strip() == "A"]
    log.info(f"[{quarter_dir.name}] P 필터: + ad_code=='A' → {len(_p2):,}")
    _p3 = _p2[(_p2["shares"].fillna(0) > 0) & (_p2["price"].fillna(0) > 0)]
    log.info(f"[{quarter_dir.name}] P 필터: + shares>0 price>0 → {len(_p3):,}")

    P = _p3.copy()
    P["value"] = P["shares"] * P["price"]
    _p4 = P[P["value"] >= 5000]
    log.info(f"[{quarter_dir.name}] P 필터: + value>=5000 → {len(_p4):,}")
    P = _p4
    _p5 = P[(P["is_director"] + P["is_officer"] + P["is_tenpct"]) >= 1]
    log.info(f"[{quarter_dir.name}] P 필터: + 임원·이사·10% → {len(_p5):,}")
    P = _p5

    # 중복 컬럼 방지 (merge 과정에서 생겼을 수 있음)
    P = P.loc[:, ~P.columns.duplicated()].copy()

    # role 분류 — vectorized (apply(lambda) 금지, pandas 2.x corner case 회피)
    title = P["officer_title"].fillna("").astype(str)
    is_off = P["is_officer"].astype(int) == 1
    is_dir = P["is_director"].astype(int) == 1
    is_ten = P["is_tenpct"].astype(int) == 1
    P["role"] = np.select(
        [
            is_off & title.str.contains(_CEO_RE, regex=True, na=False),
            is_off & title.str.contains(_CFO_RE, regex=True, na=False),
            is_off & title.str.contains(_CHAIR_RE, regex=True, na=False),
            is_off & title.str.contains(_COO_RE, regex=True, na=False),
            is_off,
            is_dir,
            is_ten,
        ],
        ["CEO", "CFO", "CHAIR", "COO", "OTHER_OFFICER", "DIRECTOR", "TENPCT"],
        default="OTHER",
    )

    # footnotes
    P["footnotes"] = P["accession"].map(fn_grp).fillna("").astype(str).str.slice(0, 500)
    # fn_flag — vectorized
    fn_src = (P["footnotes"].fillna("") + " | " + P["nature_of_ownership"].fillna("")).astype(str)
    P["fn_flag"] = fn_src.map(tag_fn_flags)

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
        S = S.loc[:, ~S.columns.duplicated()].copy()
        # role — vectorized (10%만은 매도 집계에서 제외했으므로 OFFICER/DIRECTOR 만 처리)
        title_s = S["officer_title"].fillna("").astype(str)
        is_off_s = S["is_officer"].astype(int) == 1
        is_dir_s = S["is_director"].astype(int) == 1
        S["role"] = np.select(
            [
                is_off_s & title_s.str.contains(_CEO_RE, regex=True, na=False),
                is_off_s & title_s.str.contains(_CFO_RE, regex=True, na=False),
                is_off_s & title_s.str.contains(_CHAIR_RE, regex=True, na=False),
                is_off_s & title_s.str.contains(_COO_RE, regex=True, na=False),
                is_off_s,
                is_dir_s,
            ],
            ["CEO", "CFO", "CHAIR", "COO", "OTHER_OFFICER", "DIRECTOR"],
            default="OTHER",
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
