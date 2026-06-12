"""
S5 모멘텀 전략 데이터 영속성 레이어

data/momentum_positions.json — S5 포지션 + 매수대기 목록
"""
import json
import os
import subprocess
import time
from pathlib import Path

from loguru import logger

MOMENTUM_POS_PATH = Path("data/momentum_positions.json")

_EMPTY_POS = {"positions": {}}


# ── 포지션 ───────────────────────────────────────────────────────────

def load_positions() -> dict:
    if not MOMENTUM_POS_PATH.exists():
        return {}
    with open(MOMENTUM_POS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("positions", {})


def save_positions(positions: dict) -> None:
    MOMENTUM_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if MOMENTUM_POS_PATH.exists():
        with open(MOMENTUM_POS_PATH, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = {}
    existing["positions"] = positions
    with open(MOMENTUM_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def add_position(
    code: str, name: str,
    entry_date: str, entry_price: int, quantity: int,
    buyer_type: str = "foreign",
) -> None:
    positions = load_positions()
    existing = positions.get(code)
    if existing and existing.get("quantity", 0) > 0 and existing.get("entry_price", 0) > 0:
        old_qty   = existing["quantity"]
        old_price = existing["entry_price"]
        new_qty   = old_qty + quantity
        new_avg   = (old_price * old_qty + entry_price * quantity) / new_qty
        positions[code] = {
            **existing,
            "entry_price": int(round(new_avg)),
            "quantity":    new_qty,
        }
        msg = f"chore: S5 추가매수 {code} (수량 {old_qty}→{new_qty}, 평단 {old_price:,}→{int(round(new_avg)):,})"
    else:
        positions[code] = {
            "name":                 name,
            "entry_date":           entry_date,
            "entry_price":          entry_price,
            "quantity":             quantity,
            "peak_price":           entry_price,
            "peak_gain_pct":        0.0,
            "early_gain_triggered": False,
            "buyer_type":           buyer_type,
        }
        msg = f"chore: S5 포지션 추가 {code} {name} @{entry_price:,}"

    save_positions(positions)
    git_commit_push([str(MOMENTUM_POS_PATH)], msg)


def remove_position(code: str) -> None:
    positions = load_positions()
    positions.pop(code, None)
    save_positions(positions)
    git_commit_push([str(MOMENTUM_POS_PATH)], f"chore: S5 포지션 제거 {code}")


def update_position_peak(code: str, current_price: int, current_date: str) -> None:
    """고점 가격·수익률 갱신"""
    positions = load_positions()
    pos = positions.get(code)
    if not pos:
        return

    ep   = pos.get("entry_price", 0)
    gain = (current_price - ep) / ep if ep > 0 else 0.0

    changed = False
    if current_price > pos.get("peak_price", 0):
        pos["peak_price"]    = current_price
        pos["peak_gain_pct"] = round(gain, 6)
        changed = True

    if not pos.get("early_gain_triggered") and gain >= 0.15:
        from datetime import datetime as _dt
        try:
            days_held = (
                _dt.strptime(current_date, "%Y-%m-%d")
                - _dt.strptime(pos["entry_date"], "%Y-%m-%d")
            ).days
            if days_held <= 21:
                pos["early_gain_triggered"] = True
                changed = True
                logger.info(
                    f"[S5 조기익절트리거] [{code}] {pos.get('name', '')}  "
                    f"+{gain:.1%} ({days_held}일) → 목표 +25%로 상향"
                )
        except (ValueError, KeyError):
            pass

    if changed:
        positions[code] = pos
        save_positions(positions)


# ── 매수 대기 목록 ───────────────────────────────────────────────────

def get_entry_pending() -> list:
    if not MOMENTUM_POS_PATH.exists():
        return []
    with open(MOMENTUM_POS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f).get("entry_pending", [])
        except json.JSONDecodeError:
            return []


def set_entry_pending(entries: list) -> None:
    MOMENTUM_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if MOMENTUM_POS_PATH.exists():
        with open(MOMENTUM_POS_PATH, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = {}
    existing["entry_pending"] = entries
    with open(MOMENTUM_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    n     = len(entries)
    codes = " ".join(e["code"] for e in entries[:3]) + ("..." if n > 3 else "")
    git_commit_push(
        [str(MOMENTUM_POS_PATH)],
        f"chore: S5 매수대기 {n}종목" + (f" {codes}" if n else ""),
    )


# ── git 커밋·푸시 ────────────────────────────────────────────────────

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
