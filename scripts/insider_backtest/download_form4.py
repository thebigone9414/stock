"""insider_backtest/download_form4.py — SEC Form 4 분기별 zip 다운로드

지시서 §1-1:
  - 2006Q1 ~ 최신 분기 (2026Q4는 제외)
  - URL은 안내 페이지 (https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)
    에서 href 추출
  - User-Agent 필수, 초당 10 이하
  - 이미 받은 zip은 skip
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from common import SECSession, RAW_DIR, get_logger

log = get_logger("download_form4")

INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"


def discover_quarter_urls(sec: SECSession) -> list[tuple[str, str]]:
    """안내 페이지에서 (quarter_tag, zip_url) 목록 추출."""
    r = sec.get(INDEX_URL)
    r.raise_for_status()
    html = r.text
    pairs = []
    # href="...2024q1_form345.zip" 또는 .../2024/qtr1/... 등 다양한 패턴
    for m in re.finditer(r'href=["\']([^"\']+?(\d{4})[q_/\\-]?(?:qtr)?\s*([1-4])[^"\']*?form[^"\']*?\.zip)["\']', html, re.IGNORECASE):
        href = m.group(1); year = int(m.group(2)); q = int(m.group(3))
        url = urljoin(INDEX_URL, href)
        tag = f"{year}q{q}"
        pairs.append((tag, url))
    # dedupe + sort
    seen = {}
    for tag, url in pairs:
        seen[tag] = url
    return sorted(seen.items())


def quarter_in_range(tag: str, min_year: int, max_year: int, max_q: int | None) -> bool:
    y = int(tag[:4]); q = int(tag[-1])
    if y < min_year or y > max_year:
        return False
    if y == max_year and max_q is not None and q > max_q:
        return False
    return True


def already_extracted(tag: str) -> bool:
    d = RAW_DIR / tag
    return (d / "SUBMISSION.tsv").exists() and (d / "NONDERIV_TRANS.tsv").exists()


def extract_zip(zip_path: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(out_dir)
        files = list(out_dir.iterdir())
        return len(files)
    except zipfile.BadZipFile:
        log.error(f"{zip_path} 손상 — 재다운로드 유도 위해 삭제")
        zip_path.unlink(missing_ok=True)
        return -1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-year", type=int, default=2006)
    today = date.today()
    # 지시서 §1-1: 분기 종료 후 수 주 뒤 게시. 2026Q4(10월~)는 제외.
    default_max_year = today.year
    default_max_q = {1:1, 2:1, 3:1, 4:2, 5:2, 6:2, 7:2, 8:2, 9:3, 10:3, 11:3, 12:3}[today.month]
    p.add_argument("--max-year", type=int, default=default_max_year)
    p.add_argument("--max-q", type=int, default=default_max_q)
    args = p.parse_args()

    sec = SECSession()
    log.info(f"안내 페이지 {INDEX_URL} 에서 분기별 zip URL 수집 중...")
    pairs = discover_quarter_urls(sec)
    log.info(f"총 {len(pairs)} 분기 발견")

    # 필터
    target = [(t, u) for t, u in pairs if quarter_in_range(t, args.min_year, args.max_year, args.max_q)]
    log.info(f"대상 분기 {len(target)}  ({target[0][0] if target else '-'} ~ {target[-1][0] if target else '-'})")

    ok = fail = skip = 0
    for tag, url in target:
        if already_extracted(tag):
            skip += 1
            continue
        zip_path = RAW_DIR / f"{tag}_form345.zip"
        if sec.download_to(url, zip_path):
            n = extract_zip(zip_path, RAW_DIR / tag)
            if n > 0:
                ok += 1
                log.info(f"[{tag}] {n}개 파일 추출 완료")
            else:
                fail += 1
        else:
            fail += 1
            log.error(f"[{tag}] 다운로드 실패: {url}")

    log.info(f"완료 — 신규 {ok}  skip {skip}  실패 {fail}  총 {len(target)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
