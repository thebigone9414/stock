"""insider_backtest/run_all.py — 전체 파이프라인

download → parse → fetch → build → verify → zip 패키징.
각 단계가 자체 __main__ 을 가지지만 여기선 순차 호출.
각 단계 실패 시 조기 중단 + 로그.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

from common import HERE, OUT_DIR, get_logger

log = get_logger("run_all")


MAX_FILE_MB = 25


def _run(step: str, *extra: str) -> int:
    cmd = [sys.executable, str(HERE / f"{step}.py"), *extra]
    log.info(f"[step {step}] {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(HERE))
    log.info(f"[step {step}] rc={r.returncode}")
    return r.returncode


def _split_by_year(src: Path) -> list[Path]:
    """CSV.gz 파일을 연도 (filing_date 열 기준)로 분할해 반환."""
    import pandas as pd
    try:
        df = pd.read_csv(src, dtype=str)
    except Exception as e:
        log.error(f"{src.name} 로드 실패: {e}")
        return [src]
    date_col = None
    for c in ("filing_date", "trans_date"):
        if c in df.columns:
            date_col = c
            break
    if date_col is None:
        return [src]
    df["_year"] = pd.to_datetime(df[date_col], errors="coerce").dt.year
    out = []
    base = src.name.replace(".csv.gz", "")
    for y, g in df.groupby("_year"):
        if pd.isna(y):
            continue
        y = int(y)
        sub = g.drop(columns=["_year"])
        p = OUT_DIR / f"{base}_{y}.csv.gz"
        sub.to_csv(p, index=False, compression="gzip")
        out.append(p)
    # 원본 삭제
    src.unlink(missing_ok=True)
    return out


def package_zip() -> Path:
    """산출물을 하나의 zip 에 넣어 반환. 25MB 초과 CSV는 연도 분할."""
    # 큰 파일 체크 → 분할
    for f in ("insider_P.csv.gz", "events.csv.gz", "insider_S_agg.csv.gz"):
        p = OUT_DIR / f
        if p.exists() and p.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            log.info(f"{f} > {MAX_FILE_MB}MB → 연도 분할")
            _split_by_year(p)

    today = date.today().strftime("%Y%m%d")
    zip_path = OUT_DIR / f"insider_backtest_data_{today}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        # 데이터
        for f in OUT_DIR.iterdir():
            if f.name == zip_path.name:
                continue
            if f.is_file():
                z.write(f, arcname=f.name)
        # 코드
        for f in HERE.glob("*.py"):
            z.write(f, arcname=f"code/{f.name}")
    log.info(f"zip 패키징 완료: {zip_path}  ({zip_path.stat().st_size/1024/1024:.1f}MB)")
    return zip_path


def main() -> int:
    steps = [
        ("download_form4", []),
        ("parse_form4",    []),
        ("fetch_prices",   []),
        ("build_events",   []),
        ("verify",         []),
    ]
    for name, extra in steps:
        rc = _run(name, *extra)
        if rc != 0:
            log.error(f"[{name}] 실패 (rc={rc}) — 중단")
            return rc

    package_zip()
    log.info("전체 파이프라인 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
