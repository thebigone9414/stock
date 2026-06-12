"""
MA 이평선 데이터 영속성 레이어
data/ma_data.json 단일 파일로 MA 테이블 + Strategy 2 포지션 관리

포지션 구조: positions[code][entry_date] = {tranche}
  - 같은 종목 다른 날짜 = 독립 트랜치
  - 구 포맷 {code: tranche} → 신 포맷 자동 마이그레이션
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


def get_positions() -> dict:
    """Returns {code: {entry_date: tranche_dict}}"""
    data = load()
    positions = data.get("positions", {})
    migrated, changed = _migrate_positions(positions)
    if changed:
        data["positions"] = migrated
        save(data)
        logger.info("[ma_store] 포지션 포맷 마이그레이션 완료")
    return migrated


def add_position(code: str, name: str, entry_date: str, entry_price: int, quantity: int) -> None:
    """트랜치 추가. 동일 종목·동일 날짜면 가중평균 병합, 다른 날짜면 독립 트랜치."""
    data = load()
    positions = data.setdefault("positions", {})
    migrated, _ = _migrate_positions(positions)
    data["positions"] = migrated
    positions = migrated

    if code not in positions:
        positions[code] = {}

    if entry_date in positions[code]:
        t = positions[code][entry_date]
        old_qty, old_price = t.get("quantity", 0), t.get("entry_price", 0)
        new_qty = old_qty + quantity
        new_avg = (old_price * old_qty + entry_price * quantity) / new_qty if new_qty else entry_price
        positions[code][entry_date] = {**t, "entry_price": int(round(new_avg)), "quantity": new_qty}
        msg = (f"chore: S2 트랜치합산 {code} [{entry_date}] "
               f"수량{old_qty}→{new_qty} 평단{old_price:,}→{int(round(new_avg)):,}")
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
        n = len(positions[code])
        msg = (f"chore: S2 포지션 추가 {code} {name} @{entry_price:,} [{entry_date}]"
               + (f" (트랜치{n})" if n > 1 else ""))

    save(data)
    git_commit_push([str(MA_DATA_PATH)], msg)


def remove_position(code: str, entry_date: str) -> None:
    data = load()
    positions = data.setdefault("positions", {})
    migrated, _ = _migrate_positions(positions)
    data["positions"] = migrated
    if code in migrated:
        migrated[code].pop(entry_date, None)
        if not migrated[code]:
            migrated.pop(code)
    save(data)
    git_commit_push([str(MA_DATA_PATH)], f"chore: S2 포지션 제거 {code} [{entry_date}]")


def get_base_capital() -> int:
    return load().get("base_capital", 0)


def set_base_capital(amount: int) -> None:
    data = load()
    data["base_capital"] = amount
    save(data)


def extra_slots(base_capital: int, total_eval: int, slot_ratio: float = 0.20) -> int:
    if base_capital <= 0:
        return 0
    profit = total_eval - base_capital
    if profit <= 0:
        return 0
    return int(profit / (base_capital * slot_ratio))


def get_entry_pending() -> list:
    return load().get("entry_pending", [])


def set_entry_pending(entries: list) -> None:
    data = load()
    data["entry_pending"] = entries
    save(data)
    n     = len(entries)
    codes = " ".join(e["code"] for e in entries[:3]) + ("..." if n > 3 else "")
    git_commit_push(
        [str(MA_DATA_PATH)],
        f"chore: S2 매수대기 {n}종목" + (f" {codes}" if n else ""),
    )


def update_position_peak(code: str, entry_date: str, current_price: int, current_date: str) -> None:
    """특정 트랜치 고점 갱신 + 조기익절 트리거(21일 이내 +15%) 체크"""
    data = load()
    positions = data.get("positions", {})
    migrated, changed = _migrate_positions(positions)
    if changed:
        data["positions"] = migrated
    tranche = migrated.get(code, {}).get(entry_date)
    if not tranche:
        return

    ep   = tranche.get("entry_price", 0)
    gain = (current_price - ep) / ep if ep > 0 else 0.0

    changed_data = False
    if current_price > tranche.get("peak_price", 0):
        tranche["peak_price"]    = current_price
        tranche["peak_gain_pct"] = round(gain, 6)
        changed_data = True

    if not tranche.get("early_gain_triggered") and gain >= 0.15:
        from datetime import datetime as _dt
        try:
            days_held = (
                _dt.strptime(current_date, "%Y-%m-%d")
                - _dt.strptime(entry_date, "%Y-%m-%d")
            ).days
            if days_held <= 21:
                tranche["early_gain_triggered"] = True
                changed_data = True
                logger.info(
                    f"[S2 조기익절트리거] [{code}] {tranche.get('name', '')}  "
                    f"+{gain:.1%} ({days_held}일) → 목표 +25%로 상향"
                )
        except (ValueError, KeyError):
            pass

    if changed_data:
        data["positions"][code][entry_date] = tranche
        save(data)


def git_commit_push(files: list, message: str) -> None:
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
