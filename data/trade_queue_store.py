"""
매매 결정 큐 영속성 레이어
trade_decision.py(저녁 20:00) → morning_trade.py(아침 09:00)

trade_queue.json 구조:
{
  "date": "2026-06-11",
  "updated_at": "2026-06-11 20:05",
  "sell": [
    {
      "code": "005930", "name": "삼성전자", "strategy": "S2",
      "reason": "손절(-7%)", "quantity": 100, "entry_price": 70000,
      "close": 65100, "gain": -0.07
    }
  ],
  "buy": [
    {
      "code": "035720", "name": "카카오", "strategy": "S3",
      "per_slot_budget": 2000000, "score": 5, "ca_tag": "C+A"
    }
  ]
}
"""
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger

QUEUE_PATH = Path("data/trade_queue.json")

_EMPTY: dict = {"date": "", "updated_at": "", "sell": [], "buy": []}


def load_queue() -> dict:
    if not QUEUE_PATH.exists():
        return {k: v for k, v in _EMPTY.items()}
    with open(QUEUE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_queue(data: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_today_queue(today: str) -> Optional[dict]:
    """미실행 큐 반환 — executed=True 또는 2일 이상 경과 시 None (중복/지연 실행 방지)
    trade_decision은 전날 저녁에 실행하므로 큐 날짜가 today와 같거나 하루 전이면 유효.
    2일 이상 된 큐는 stale로 간주해 실행하지 않는다.
    """
    from datetime import datetime, timedelta
    q = load_queue()
    if not q.get("date"):
        return None
    if q.get("executed"):
        return None
    try:
        queue_date = datetime.strptime(q["date"], "%Y-%m-%d").date()
        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        if (today_date - queue_date).days > 1:
            logger.warning(
                f"[매매큐] 큐 날짜 {q['date']}가 오늘({today})보다 2일 이상 오래됨 — stale 큐 실행 방지"
            )
            return None
    except (ValueError, KeyError):
        pass
    return q


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
        rc_pull, out_pull = run(["git", "pull", "--rebase", "--autostash"])
        if rc_pull != 0:
            logger.warning(f"git pull --rebase 실패 (무시 후 push 시도): {out_pull}")
        rc, out = run(["git", "push"])
        if rc == 0:
            logger.info(f"git push 완료: {message}")
            return
        wait = 2 ** attempt
        logger.warning(f"git push 실패 (시도 {attempt+1}/4) {wait}초 후 재시도: {out.strip()}")
        time.sleep(wait)

    logger.error("git push 최종 실패")
