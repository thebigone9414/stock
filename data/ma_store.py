"""
MA 이평선 데이터 영속성 레이어
data/ma_data.json 단일 파일로 MA 테이블 + Strategy 2 포지션 관리
"""
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger

MA_DATA_PATH = Path("data/ma_data.json")

_EMPTY: dict = {"updated_at": "", "stocks": {}, "positions": {}}


def load() -> dict:
    if not MA_DATA_PATH.exists():
        return {k: v for k, v in _EMPTY.items()}
    with open(MA_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data: dict) -> None:
    MA_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MA_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_stock(code: str) -> Optional[dict]:
    return load().get("stocks", {}).get(code)


def get_positions() -> dict:
    return load().get("positions", {})


def add_position(code: str, name: str, entry_date: str, entry_price: int, quantity: int) -> None:
    data = load()
    data.setdefault("positions", {})[code] = {
        "name":        name,
        "entry_date":  entry_date,
        "entry_price": entry_price,
        "quantity":    quantity,
    }
    save(data)
    git_commit_push([str(MA_DATA_PATH)], f"chore: S2 포지션 추가 {code}")


def remove_position(code: str) -> None:
    data = load()
    data.setdefault("positions", {}).pop(code, None)
    save(data)
    git_commit_push([str(MA_DATA_PATH)], f"chore: S2 포지션 제거 {code}")


def git_commit_push(files: list, message: str) -> None:
    """GitHub Actions 환경에서 변경된 파일을 커밋하고 푸시"""
    if not os.environ.get("GITHUB_ACTIONS"):
        logger.info(f"로컬 환경 — git push 생략: {message}")
        return

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "config", "user.name", "github-actions[bot]"])
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
        rc, out = run(["git", "push"])
        if rc == 0:
            logger.info(f"git push 완료: {message}")
            return
        wait = 2 ** attempt
        logger.warning(f"git push 실패 (시도 {attempt+1}/4) {wait}초 후 재시도")
        time.sleep(wait)

    logger.error("git push 최종 실패")
