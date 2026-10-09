#!/usr/bin/env python3
"""scripts/register_cronjobs.py — cron-job.org에 14개 자동매매 cron 등록

환경변수:
  CRONJOB_API_KEY   — cron-job.org API Key
  GH_WORKFLOW_PAT   — GitHub Personal Access Token (workflow:write 권한)
  GH_OWNER          — GitHub owner (기본 thebigone9414)
  GH_REPO           — GitHub repo  (기본 stock)
  GH_WORKFLOW       — workflow 파일명 (기본 autotrade.yml)
  GH_REF            — 실행할 branch (기본 dev)

동작:
  - cron-job.org 기존 jobs 조회 → 제목이 'autotrade {cmd} {HH:MM}' 로 시작하면 업데이트, 없으면 생성
  - --dry-run: API 호출 없이 등록할 내용만 출력

실행:
  python scripts/register_cronjobs.py                # 실제 등록
  python scripts/register_cronjobs.py --dry-run      # 미리보기
  python scripts/register_cronjobs.py --delete-only  # 자동매매 cron 전체 삭제 (복구용)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

import requests


API = "https://api.cron-job.org"

# 평일 자동매매 하루 일정 (KST). cmd 는 autotrade.yml의 workflow_dispatch input 값과 일치.
SCHEDULE: list[tuple[str, str]] = [
    ("08:20", "reconcile"),
    ("08:50", "sell"),
    ("08:55", "stop-register"),
    ("09:05", "stop-check"),
    ("10:00", "stop-check"),
    ("11:00", "stop-check"),
    ("12:00", "stop-check"),
    ("13:00", "stop-check"),
    ("14:00", "stop-check"),
    ("15:00", "stop-check"),
    ("15:15", "stop-check"),
    ("15:18", "buy"),
    ("15:35", "stop-confirm"),
    ("20:10", "nightly"),
]


def title_for(hhmm: str, cmd: str) -> str:
    """cron-job.org 제목. 중복 식별용 — 바꾸지 말 것."""
    return f"autotrade {cmd} {hhmm}"


def build_job_body(hhmm: str, cmd: str, gh_owner: str, gh_repo: str,
                    gh_workflow: str, gh_ref: str, gh_pat: str) -> dict:
    h, m = map(int, hhmm.split(":"))
    url = f"https://api.github.com/repos/{gh_owner}/{gh_repo}/actions/workflows/{gh_workflow}/dispatches"
    gh_body = json.dumps({"ref": gh_ref, "inputs": {"cmd": cmd}})
    return {
        "job": {
            "url":            url,
            "enabled":        True,
            "saveResponses":  True,
            "title":          title_for(hhmm, cmd),
            "requestMethod":  1,  # 0=GET, 1=POST
            "schedule": {
                "timezone": "Asia/Seoul",
                "hours":    [h],
                "minutes":  [m],
                "mdays":    [-1],       # 모든 날짜
                "months":   [-1],       # 모든 월
                "wdays":    [1, 2, 3, 4, 5],  # 월~금 (0=일 ... 6=토)
            },
            "extendedData": {
                "headers": {
                    "Authorization": f"Bearer {gh_pat}",
                    "Accept":        "application/vnd.github+json",
                    "Content-Type":  "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                "body": gh_body,
            },
            "requestTimeout": 30,
            "notification": {
                "onFailure":              True,
                "onSuccess":              False,
                "onDisable":              True,
            },
        }
    }


def _request_with_retry(method, url, session: requests.Session, retries: int = 4, **kwargs):
    """429/503 등 일시적 오류 재시도 + 지수 백오프."""
    delay = 5.0
    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = session.request(method, url, timeout=30, **kwargs)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < retries:
                    print(f"    [retry] {method} {url.split('/')[-1]} status={r.status_code} → {delay:.0f}s 후 재시도 ({attempt+1}/{retries})")
                    time.sleep(delay)
                    delay *= 2
                    continue
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            if attempt < retries and e.response is not None and (e.response.status_code == 429 or e.response.status_code >= 500):
                print(f"    [retry] {e} → {delay:.0f}s 후 ({attempt+1}/{retries})")
                time.sleep(delay)
                delay *= 2
                last_exc = e
                continue
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retries:
                print(f"    [retry] network: {e} → {delay:.0f}s 후 ({attempt+1}/{retries})")
                time.sleep(delay)
                delay *= 2
                last_exc = e
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("retry exhausted")


def list_jobs(session: requests.Session) -> list[dict]:
    r = _request_with_retry("GET", f"{API}/jobs", session)
    return r.json().get("jobs", [])


def create_job(session: requests.Session, body: dict) -> int:
    r = _request_with_retry("PUT", f"{API}/jobs", session, json=body)
    return int(r.json().get("jobId"))


def update_job(session: requests.Session, job_id: int, body: dict) -> None:
    _request_with_retry("PATCH", f"{API}/jobs/{job_id}", session, json=body)


def delete_job(session: requests.Session, job_id: int) -> None:
    _request_with_retry("DELETE", f"{API}/jobs/{job_id}", session)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="API 호출 없이 미리보기")
    p.add_argument("--delete-only", action="store_true", help="자동매매 cron 전체 삭제 (복구용)")
    args = p.parse_args()

    api_key = os.environ.get("CRONJOB_API_KEY")
    gh_pat  = os.environ.get("GH_WORKFLOW_PAT")
    gh_owner    = os.environ.get("GH_OWNER", "thebigone9414")
    gh_repo     = os.environ.get("GH_REPO", "stock")
    gh_workflow = os.environ.get("GH_WORKFLOW", "autotrade.yml")
    gh_ref      = os.environ.get("GH_REF", "dev")

    if not api_key:
        print("ERROR: CRONJOB_API_KEY 환경변수 필요", file=sys.stderr)
        return 1
    if not gh_pat and not args.delete_only:
        print("ERROR: GH_WORKFLOW_PAT 환경변수 필요", file=sys.stderr)
        return 1

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})

    print(f"[cron-reg] cron-job.org 기존 jobs 조회 중...")
    try:
        existing = list_jobs(session)
    except Exception as e:
        print(f"ERROR: jobs 조회 실패 (API Key 확인 필요): {e}", file=sys.stderr)
        return 2

    # 기존 자동매매 cron 매핑: {title: job_id}
    autotrade_titles = {title_for(hhmm, cmd) for hhmm, cmd in SCHEDULE}
    existing_at: dict[str, int] = {}
    for j in existing:
        t = j.get("title", "")
        if t.startswith("autotrade "):
            existing_at[t] = int(j["jobId"])
    print(f"[cron-reg] 기존 'autotrade *' job: {len(existing_at)}개")

    # 삭제 모드
    if args.delete_only:
        print(f"[cron-reg] 삭제 모드 — {len(existing_at)}개 job 삭제")
        for title, jid in existing_at.items():
            if args.dry_run:
                print(f"  [DRY-RUN] delete {jid} {title}")
            else:
                try:
                    delete_job(session, jid)
                    print(f"  deleted {jid} {title}")
                except Exception as e:
                    print(f"  FAIL delete {jid} {title}: {e}")
        return 0

    # 정규 등록 모드
    created, updated, failed = 0, 0, 0
    for hhmm, cmd in SCHEDULE:
        title = title_for(hhmm, cmd)
        body = build_job_body(hhmm, cmd, gh_owner, gh_repo, gh_workflow, gh_ref, gh_pat)
        existing_id = existing_at.get(title)
        action = "update" if existing_id else "create"
        if args.dry_run:
            print(f"  [DRY-RUN] {action:7} {title}  →  POST {gh_workflow} cmd={cmd}")
            continue
        try:
            if existing_id:
                update_job(session, existing_id, body)
                updated += 1
                print(f"  updated  jobId={existing_id:>6}  {title}")
            else:
                new_id = create_job(session, body)
                created += 1
                print(f"  created  jobId={new_id:>6}  {title}")
            time.sleep(3.0)  # rate limit 완화 (14건 × 3초 = 42초)
        except Exception as e:
            failed += 1
            print(f"  FAIL     {title}: {e}", file=sys.stderr)

    # 자동매매 schedule에 더 이상 없는 과거 job (고아) 식별
    orphans = [(t, i) for t, i in existing_at.items() if t not in autotrade_titles]
    if orphans:
        print(f"\n[cron-reg] 고아 job (현재 schedule에 없음) {len(orphans)}개:")
        for t, i in orphans:
            print(f"  (keep) jobId={i:>6}  {t}  — 수동으로 cron-job.org 에서 확인 후 삭제")

    print(f"\n[cron-reg] 완료 — 생성 {created}  업데이트 {updated}  실패 {failed}  총 {len(SCHEDULE)}개 schedule")
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
