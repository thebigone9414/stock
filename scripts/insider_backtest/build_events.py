"""insider_backtest/build_events.py — events.csv.gz + paths.npz

지시서 §2-2 / §2-3:
  insider_P (임원·이사만) → accession × issuer 집계 → events
  각 사건에 대해 가격 관련 열 계산 (t0, t1, open_t1_adj, adv20_usd,
    ret_21/126/252, hi252_ratio, vol60, fwd_h, spy_fwd_h, iwm_fwd_h, mdd_126)
  paths.npz: price_available=1 사건별 130거래일 수정종가 경로 (float16)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT_DIR, PRICES_DIR, get_logger

log = get_logger("build_events")

FWD_HORIZONS = [5, 10, 21, 42, 63, 126, 189, 252]
PATH_LEN = 130


# ── 가격 캐시 로더 ────────────────────────────────────────
def load_price(ticker: str) -> pd.DataFrame | None:
    fp = PRICES_DIR / f"{ticker}.parquet"
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    # 숫자 변환
    for c in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def nearest_prev_or_eq_idx(dates: pd.Series, target: pd.Timestamp) -> int:
    """dates (오름차순) 에서 target 이하의 마지막 index. 없으면 -1."""
    pos = dates.searchsorted(target, side="right") - 1
    return int(pos) if pos >= 0 else -1


# ── 사건 집계 ──────────────────────────────────────────
def build_events_frame(P: pd.DataFrame) -> pd.DataFrame:
    """임원·이사(10% 주주만은 제외) accession × issuer_cik 집계."""
    sub = P[(P["is_officer"].astype(int) == 1) | (P["is_director"].astype(int) == 1)].copy()
    log.info(f"임원·이사만 필터: {len(P)} → {len(sub)}")

    # 금액 가중 평균 단가 함수
    def _weighted_avg(group):
        w = group["value"].sum()
        return (group["price"] * group["value"]).sum() / w if w > 0 else group["price"].mean()

    grouped = sub.groupby(["accession", "issuer_cik"], dropna=False)
    agg_dict = {
        "filing_date":   ("filing_date", "first"),
        "issuer_name":   ("issuer_name", "first"),
        "ticker":        ("ticker", "first"),
        "n_owners":      ("owner_cik", "nunique"),
        "value_total":   ("value", "sum"),
        "shares_total":  ("shares", "sum"),
        "trans_date_min": ("trans_date", "min"),
        "trans_date_max": ("trans_date", "max"),
        "doc_type":      ("doc_type", "first"),
    }
    ev = grouped.agg(**agg_dict).reset_index()

    # roles / has_* / fn_flag / aff10b5one
    ev_roles = sub.groupby(["accession", "issuer_cik"]).apply(
        lambda g: pd.Series({
            "roles":      ";".join(sorted(set(g["role"].astype(str)))),
            "has_ceo":    int((g["role"] == "CEO").any()),
            "has_cfo":    int((g["role"] == "CFO").any()),
            "has_chair":  int((g["role"] == "CHAIR").any()),
            "fn_flag":    ";".join(sorted(set(";".join(g["fn_flag"].astype(str).fillna("")).split(";")) - {""})),
            "aff10b5one": int(((g["aff10b5one"].astype(str) == "1") | (g["aff10b5one"].astype(str).str.lower() == "true")).any()),
        }),
        include_groups=False,
    ).reset_index()
    ev = ev.merge(ev_roles, on=["accession", "issuer_cik"], how="left")

    # price_avg (금액 가중)
    ev["price_avg"] = ev.apply(
        lambda r: (
            sub[(sub["accession"] == r["accession"]) & (sub["issuer_cik"] == r["issuer_cik"])]
            .pipe(lambda g: (g["price"] * g["value"]).sum() / g["value"].sum() if g["value"].sum() > 0 else np.nan)
        ),
        axis=1,
    )
    ev["filing_date"] = pd.to_datetime(ev["filing_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return ev


# ── 가격 열 계산 (단일 사건) ────────────────────────────
def compute_price_cols(filing_date: pd.Timestamp, px: pd.DataFrame,
                        spy: pd.DataFrame, iwm: pd.DataFrame) -> dict:
    """px: 티커의 Date-sorted OHLCV. spy/iwm: 벤치마크."""
    out: dict = {"price_available": 0}
    dates = px["Date"]

    i0 = nearest_prev_or_eq_idx(dates, filing_date)
    if i0 < 0:
        return out
    # t1 = filing_date 이후 첫 거래일
    i1 = int(dates.searchsorted(filing_date, side="right"))
    if i1 >= len(dates):
        # 상장폐지된 뒤 공시 등 — t1 데이터 없음
        return out

    close = px["Close"].values
    adj   = px["Adj Close"].values
    vol   = px["Volume"].values
    openp = px["Open"].values

    out["price_available"] = 1
    out["t1"]        = dates.iloc[i1].strftime("%Y-%m-%d")
    out["close_t0"]  = float(close[i0]) if not np.isnan(close[i0]) else np.nan
    # 수정시가 = Open * AdjClose/Close
    if not np.isnan(close[i1]) and close[i1] > 0:
        out["open_t1_adj"] = float(openp[i1] * adj[i1] / close[i1])
    else:
        out["open_t1_adj"] = np.nan

    # adv20_usd (t0 포함 과거 20일 Close×Volume 평균)
    lo = max(0, i0 - 19)
    out["adv20_usd"] = float(np.nanmean(close[lo : i0 + 1] * vol[lo : i0 + 1])) if (i0 - lo) >= 0 else np.nan

    # 과거 수익률 (t0 종가 기준, 수정종가)
    for k, name in [(21, "ret_21"), (126, "ret_126"), (252, "ret_252")]:
        j = i0 - k
        out[name] = float(adj[i0] / adj[j] - 1) if j >= 0 and adj[j] > 0 and not np.isnan(adj[j]) else np.nan

    # hi252_ratio = close_t0 / 252일 최고 종가 (t0 포함)
    hi_lo = max(0, i0 - 251)
    hi = np.nanmax(close[hi_lo : i0 + 1])
    out["hi252_ratio"] = float(out["close_t0"] / hi) if hi and not np.isnan(hi) and hi > 0 else np.nan

    # vol60 = 60일 일간수익률 표준편차 × sqrt(252)
    r = np.diff(np.log(adj[max(0, i0 - 60) : i0 + 1]))
    out["vol60"] = float(np.nanstd(r) * np.sqrt(252)) if len(r) >= 20 else np.nan

    # fwd_h — t1 수정시가 대비 t1+h 수정종가 수익률
    base = out["open_t1_adj"]
    for h in FWD_HORIZONS:
        j = i1 + h
        if base is None or np.isnan(base) or base <= 0:
            out[f"fwd_{h}"] = np.nan
            continue
        if j < len(dates):
            fv = adj[j]
            out[f"fwd_{h}"] = float(fv / base - 1) if not np.isnan(fv) else np.nan
        else:
            # 데이터가 그 전에 끝나면 마지막 수정종가로
            fv = adj[-1]
            out[f"fwd_{h}"] = float(fv / base - 1) if not np.isnan(fv) else np.nan
    # fwd_end: 마지막 거래일
    out["fwd_end"] = dates.iloc[-1].strftime("%Y-%m-%d")

    # mdd_126
    j1 = min(len(dates), i1 + 127)
    seg = adj[i1 : j1]
    if len(seg) >= 2 and base and base > 0:
        peak = np.maximum.accumulate(seg)
        dd = seg / peak - 1
        out["mdd_126"] = float(np.nanmin(dd))
    else:
        out["mdd_126"] = np.nan

    # 벤치마크 (SPY, IWM) 같은 h
    for bench_name, bench_df in [("spy", spy), ("iwm", iwm)]:
        if bench_df is None or bench_df.empty:
            for h in FWD_HORIZONS:
                out[f"{bench_name}_fwd_{h}"] = np.nan
            continue
        b_dates = bench_df["Date"]
        b_adj = bench_df["Adj Close"].values
        bi1 = int(b_dates.searchsorted(filing_date, side="right"))
        if bi1 >= len(b_dates):
            for h in FWD_HORIZONS:
                out[f"{bench_name}_fwd_{h}"] = np.nan
            continue
        b_base = b_adj[bi1]
        for h in FWD_HORIZONS:
            bj = bi1 + h
            if bj >= len(b_dates):
                bj = len(b_dates) - 1
            if b_base > 0 and not np.isnan(b_adj[bj]):
                out[f"{bench_name}_fwd_{h}"] = float(b_adj[bj] / b_base - 1)
            else:
                out[f"{bench_name}_fwd_{h}"] = np.nan
    return out


# ── 경로 추출 (paths.npz) ──────────────────────────────
def extract_path(filing_date: pd.Timestamp, px: pd.DataFrame) -> np.ndarray:
    """t1 수정시가=1.0 으로 정규화한 PATH_LEN 거래일 adj_close 경로."""
    dates = px["Date"]
    i1 = int(dates.searchsorted(filing_date, side="right"))
    if i1 >= len(dates):
        return np.full(PATH_LEN, np.nan, dtype=np.float16)
    open_t1 = px["Open"].iloc[i1]
    close_t1 = px["Close"].iloc[i1]
    adj_t1 = px["Adj Close"].iloc[i1]
    if np.isnan(close_t1) or close_t1 <= 0:
        return np.full(PATH_LEN, np.nan, dtype=np.float16)
    base = open_t1 * adj_t1 / close_t1
    seg = px["Adj Close"].iloc[i1 : i1 + PATH_LEN].values / base
    out = np.full(PATH_LEN, np.nan, dtype=np.float16)
    out[: len(seg)] = seg.astype(np.float16)
    return out


# ── 메인 ──────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser()
    args = p.parse_args()

    P_path = OUT_DIR / "insider_P.csv.gz"
    if not P_path.exists():
        log.error("insider_P.csv.gz 없음 — parse_form4 먼저 실행 필요")
        return 1
    log.info(f"insider_P 로드: {P_path}")
    P = pd.read_csv(P_path, dtype=str)
    for c in ("shares", "price", "value"):
        P[c] = pd.to_numeric(P[c], errors="coerce")
    for c in ("is_director", "is_officer", "is_tenpct"):
        P[c] = pd.to_numeric(P[c], errors="coerce").fillna(0).astype(int)
    log.info(f"P 총 {len(P):,}건")

    ev = build_events_frame(P)
    log.info(f"events 집계: {len(ev):,}건")

    # 벤치마크
    spy = load_price("SPY")
    iwm = load_price("IWM")
    if spy is None:
        log.warning("SPY 가격 없음 — spy_fwd_h 전부 NaN")
    if iwm is None:
        log.warning("IWM 가격 없음 — iwm_fwd_h 전부 NaN")

    # 가격 열 + 경로
    rows = []
    paths_list = []
    event_ids = []
    for idx, row in ev.iterrows():
        tk = row["ticker"]
        if not tk or not isinstance(tk, str):
            rows.append({"price_available": 0})
            continue
        px = load_price(tk)
        if px is None or px.empty:
            rows.append({"price_available": 0})
            continue
        fd = pd.to_datetime(row["filing_date"])
        cols = compute_price_cols(fd, px, spy, iwm)
        rows.append(cols)
        if cols.get("price_available") == 1:
            paths_list.append(extract_path(fd, px))
            event_ids.append(row["accession"])
        if (idx + 1) % 5000 == 0:
            log.info(f"가격 열 계산 {idx+1:,} / {len(ev):,}")

    price_df = pd.DataFrame(rows)
    ev_full = pd.concat([ev.reset_index(drop=True), price_df], axis=1)

    out = OUT_DIR / "events.csv.gz"
    ev_full.to_csv(out, index=False, compression="gzip")
    log.info(f"저장: {out}  ({len(ev_full):,}건, {out.stat().st_size/1024/1024:.1f}MB)")

    # paths.npz
    if paths_list:
        paths_arr = np.vstack(paths_list)
        # SPY, IWM 경로도 같은 기준으로
        spy_paths = np.vstack([extract_path(pd.to_datetime(ev[ev["accession"] == eid]["filing_date"].iloc[0]), spy) if spy is not None else np.full(PATH_LEN, np.nan, dtype=np.float16) for eid in event_ids]) if spy is not None else np.full((len(event_ids), PATH_LEN), np.nan, dtype=np.float16)
        iwm_paths = np.vstack([extract_path(pd.to_datetime(ev[ev["accession"] == eid]["filing_date"].iloc[0]), iwm) if iwm is not None else np.full(PATH_LEN, np.nan, dtype=np.float16) for eid in event_ids]) if iwm is not None else np.full((len(event_ids), PATH_LEN), np.nan, dtype=np.float16)
        np.savez_compressed(
            OUT_DIR / "paths.npz",
            event_ids=np.array(event_ids, dtype=object),
            paths=paths_arr,
            spy_paths=spy_paths,
            iwm_paths=iwm_paths,
        )
        log.info(f"저장: paths.npz  ({paths_arr.shape[0]} × {paths_arr.shape[1]})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
