"""
수동 포지션 영속성 레이어

data/manual_positions.json — 수동 매수 종목 (자동 매수 신호 없음)

청산 조건: S2~S4와 동일 (손절-7% / 트레일링스탑 / 익절+20% / 조기익절)
포지션 구조: positions[code][entry_date] = {tranche}
"""
import json
import os
import subprocess
import time
from pathlib import Path

from loguru import logger

MANUAL_POS_PATH = Path("data/manual_positions.json")

# 잔고동기화 시 자동으로 수동 포지션으로 분류할 종목 코드
MANUAL_CODES = {"034020", "0190C0", "305720"}


def _migrate_positions(positions: dict) -> tuple:
    """구 포맷 {code: tranche} → 신 포맷 {code: {date: tranche}} 변환"""
    migrated = {}
    changed = False
    for code, val in positions.items():
        if isinstance(val, dict) and "entry_price" in val:
            entry_date = val.get("entry_date", "2026-01-01")
            migrated[code] = {entry_date: val}
            changed = True
        else:
            migrated[code] = val
    return migrated, changed


def load_positions() -> dict:
    """Returns {code: {entry_date: tranche_dict}}"""
    if not MANUAL_POS_PATH.exists():
        return {}
    with open(MANUAL_POS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f).get("positions", {})
    migrated, changed = _migrate_positions(raw)
    if changed:
        save_positions(migrated)
    return migrated


def save_positions(positions: dict) -> None:
    MANUAL_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if MANUAL_POS_PATH.exists():
        with open(MANUAL_POS_PATH, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = {}
    existing["positions"] = positions
    with open(MANUAL_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def add_position(code: str, name: str, entry_date: str, entry_price: int, quantity: int) -> None:
    positions = load_positions()
    if code not in positions:
        positions[code] = {}
    if entry_date in positions[code]:
        t = positions[code][entry_date]
        old_qty, old_price = t.get("quantity", 0), t.get("entry_price", 0)
        new_qty = old_qty + quantity
        new_avg = (old_price * old_qty + entry_price * quantity) / new_qty if new_qty else entry_price
        positions[code][entry_date] = {**t, "entry_price": int(round(new_avg)), "quantity": new_qty}
        msg = f"chore: 수동 트랜치합산 {code} [{entry_date}]"
    else:
        positions[code][entry_date] = {
            "name":                 name,
            "entry_date":           entry_date,
            "entry_price":          entry_price,
            "quantity":             quantity,
            "peak_price":           entry_price,
            "peak_gain_pct":        0.0,
            "early_gain_triggered": False,
        }
        msg = f"chore: 수동 포지션 추가 {code} {name} @{entry_price:,} [{entry_date}]"
    save_positions(positions)
    git_commit_push([str(MANUAL_POS_PATH)], msg)


def remove_position(code: str, entry_date: str) -> None:
    positions = load_positions()
    if code in positions:
        positions[code].pop(entry_date, None)
        if not positions[code]:
            positions.pop(code)
    save_positions(positions)
    git_commit_push([str(MANUAL_POS_PATH)], f"chore: 수동 포지션 제거 {code} [{entry_date}]")


def update_position_peak(code: str, entry_date: str, current_price: int, current_date: str) -> None:
    """특정 트랜치 고점 갱신 + 조기익절 트리거(21일 이내 +15%) 체크"""
    positions = load_positions()
    tranche = positions.get(code, {}).get(entry_date)
    if not tranche:
        return

    ep   = tranche.get("entry_price", 0)
    gain = (current_price - ep) / ep if ep > 0 else 0.0

    changed = False
    if current_price > tranche.get("peak_price", 0):
        tranche["peak_price"]    = current_price
        tranche["peak_gain_pct"] = round(gain, 6)
        changed = True

    if not tranche.get("early_gain_triggered") and gain >= 0.15:
        from datetime import datetime as _dt
        try:
            days_held = (
                _dt.strptime(current_date, "%Y-%m-%d")
                - _dt.strptime(entry_date, "%Y-%m-%d")
            ).days
            if days_held <= 21:
                tranche["early_gain_triggered"] = True
                changed = True
                logger.info(
                    f"[수동 조기익절트리거] [{code}] {tranche.get('name', '')}  "
                    f"+{gain:.1%} ({days_held}일) → 목표 +25%로 상향"
                )
        except (ValueError, KeyError):
            pass

    if changed:
        positions[code][entry_date] = tranche
        save_positions(positions)


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
