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
    """안내 페이지에서 (quarter_tag, zip_url) 목록 추출. 실패하면 정적 URL 패턴 fallback."""
    pairs: list[tuple[str, str]] = []
    try:
        r = sec.get(INDEX_URL)
        r.raise_for_status()
        html = r.text
        # 다양한 href 패턴 매칭 (대소문자 무시)
        for m in re.finditer(
            r'href=["\']([^"\']*?(?:form345|insider)[^"\']*?\.zip)["\']',
            html, re.IGNORECASE,
        ):
            href = m.group(1)
            # 파일명에서 YYYYqN 또는 YYYY/qN 추출
            fname = href.rsplit("/", 1)[-1]
            qm = re.search(r"(\d{4})\s*[qQ_\-]?\s*([1-4])", fname)
            if not qm:
                qm = re.search(r"(\d{4})/\s*(?:qtr)?\s*([1-4])", href, re.I)
            if not qm:
                continue
            year, q = int(qm.group(1)), int(qm.group(2))
            tag = f"{year}q{q}"
            url = urljoin(INDEX_URL, href)
            pairs.append((tag, url))
        log.info(f"안내 페이지에서 href {len(pairs)}개 추출")
    except Exception as e:
        log.warning(f"안내 페이지 추출 실패: {e} — 정적 URL 패턴으로 fallback")

    # Fallback: 2006Q1 ~ 현재 분기까지 정적 URL 패턴 시도
    if len(pairs) < 10:
        log.warning(f"href 추출 결과 {len(pairs)}개 (너무 적음) → 정적 URL 패턴 추가")
        from datetime import date
        today = date.today()
        current_y, current_q = today.year, (today.month - 1) // 3 + 1
        # SEC 공식 패턴 (2024년 기준 검증): https://www.sec.gov/files/dera/data/insider-transactions-data-sets/YYYYqQ_form345.zip
        patterns = [
            "https://www.sec.gov/files/dera/data/insider-transactions-data-sets/{year}q{q}_form345.zip",
            "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{year}q{q}_form345.zip",
            "https://www.sec.gov/dera/data/insider-transactions-data-sets/{year}q{q}_form345.zip",
        ]
        for year in range(2006, current_y + 1):
            max_q = 4 if year < current_y else current_q
            for q in range(1, max_q + 1):
                tag = f"{year}q{q}"
                for pat in patterns:
                    url = pat.format(year=year, q=q)
                    pairs.append((tag, url))

    # dedupe (tag별 첫 URL — fallback은 여러 URL 후보일 수 있음)
    by_tag: dict[str, list[str]] = {}
    for tag, url in pairs:
        by_tag.setdefault(tag, []).append(url)
    # tag별 모든 후보 URL 반환 — 다운로드 시 순차 시도
    out = [(tag, url) for tag, urls in sorted(by_tag.items()) for url in urls]
    return out


def try_download(sec: SECSession, tag: str, url_candidates: list[str], zip_path: Path) -> bool:
    """후보 URL 들을 순차 시도. 첫 성공 시 반환."""
    for url in url_candidates:
        if sec.download_to(url, zip_path):
            log.info(f"[{tag}] 다운로드 성공: {url}")
            return True
    log.error(f"[{tag}] 모든 URL 후보 실패")
    return False


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
    # tag별로 URL 후보들 묶기
    by_tag: dict[str, list[str]] = {}
    for tag, url in pairs:
        if quarter_in_range(tag, args.min_year, args.max_year, args.max_q):
            by_tag.setdefault(tag, []).append(url)
    target_tags = sorted(by_tag.keys())
    log.info(f"대상 분기 {len(target_tags)}  ({target_tags[0] if target_tags else '-'} ~ {target_tags[-1] if target_tags else '-'})")

    if not target_tags:
        log.error("대상 분기 0개 — SEC 접근 실패 또는 URL 패턴 오류")
        return 1

    ok = fail = skip = 0
    for tag in target_tags:
        if already_extracted(tag):
            skip += 1
            continue
        zip_path = RAW_DIR / f"{tag}_form345.zip"
        if try_download(sec, tag, by_tag[tag], zip_path):
            n = extract_zip(zip_path, RAW_DIR / tag)
            if n > 0:
                ok += 1
                log.info(f"[{tag}] {n}개 파일 추출 완료")
            else:
                fail += 1
        else:
            fail += 1

    log.info(f"완료 — 신규 {ok}  skip {skip}  실패 {fail}  총 {len(target_tags)}")
    # 실패가 전체의 50% 넘으면 pipeline 중단 (파일 하나 둘 빠진 건 OK)
    if (ok + skip) == 0:
        log.error("단 하나도 다운로드/압축해제 성공 못함")
        return 1
    if fail > len(target_tags) * 0.5:
        log.error(f"실패율 {fail/len(target_tags)*100:.0f}% — 중단")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
