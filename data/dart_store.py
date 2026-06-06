"""
DART 재무 데이터 영속성 레이어
data/dart_data.json — 종목별 분기·연간 EPS·매출 데이터

구조:
{
  "updated_at": "2025-06-01",
  "corps": {
    "005930": {
      "corp_code": "00126380",
      "name": "삼성전자",
      "quarterly_eps": [
        {"year": 2024, "quarter": 1, "eps": 1234},
        ...
      ],
      "annual_eps": [
        {"year": 2024, "eps": 5678},
        ...
      ],
      "annual_rev": [
        {"year": 2024, "rev": 300_000_000_000},
        ...
      ]
    }
  }
}
"""
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger

DART_DATA_PATH = Path("data/dart_data.json")


def load() -> dict:
    if not DART_DATA_PATH.exists():
        return {"updated_at": "", "corps": {}}
    with open(DART_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data: dict) -> None:
    DART_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DART_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_corp(code: str) -> Optional[dict]:
    return load().get("corps", {}).get(code)


def git_commit_push(files: list, message: str) -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        logger.info(f"로컬 환경 — git push 생략: {message}")
        return

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "config", "user.name",  "github-actions[bot]"])
    run(["git", "add"] + files)

    rc, _ = run(["git", "diff", "--cached", "--quiet"])
    if rc == 0:
        logger.info("git: 변경 없음 — commit 생략")
        return

    rc, out = run(["git", "commit", "-m", message])
    if rc != 0:
        logger.error(f"git commit 실패: {out}")
        return

    for attempt in range(4):
        rc_pull, out_pull = run(["git", "pull", "--rebase", "--autostash"])
        if rc_pull != 0:
            logger.warning(f"git pull --rebase 실패: {out_pull}")
        rc, out = run(["git", "push"])
        if rc == 0:
            logger.info(f"git push 완료: {message}")
            return
        wait = 2 ** attempt
        logger.warning(f"git push 실패 (시도 {attempt+1}/4) {wait}s 후 재시도: {out.strip()}")
        time.sleep(wait)

    logger.error("git push 최종 실패")
