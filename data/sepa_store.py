"""
SEPA 전략4 데이터 영속성 레이어 (Mark Minervini)

data/sepa_data.json      — 일일 스크리닝 결과 (update_sepa.py 기록)
data/sepa_positions.json — S4 포지션 (strategies/sepa.py 기록)
"""
import json
import os
import subprocess
import time
from pathlib import Path

from loguru import logger

SEPA_DATA_PATH = Path("data/sepa_data.json")
SEPA_POS_PATH  = Path("data/sepa_positions.json")

_EMPTY_DATA = {"updated_at": "", "market_uptrend": False, "stocks": {}}
_EMPTY_POS  = {"positions": {}}


# ── sepa_data (스크리닝 결과) ────────────────────────────────────────────

def load_data() -> dict:
    if not SEPA_DATA_PATH.exists():
        return {k: v for k, v in _EMPTY_DATA.items()}
    with open(SEPA_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    SEPA_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEPA_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_buy_candidates() -> list:
    """breakout_confirmed=True AND trend_template=True 종목 [(code, info)] 반환 (rs_score 내림차순)"""
    data   = load_data()
    stocks = data.get("stocks", {})
    result = [
        (code, info)
        for code, info in stocks.items()
        if info.get("breakout_confirmed") and info.get("trend_template")
    ]
    return sorted(result, key=lambda x: x[1].get("rs_score", 0), reverse=True)


# ── sepa_positions (포지션) ──────────────────────────────────────────────

def load_positions() -> dict:
    if not SEPA_POS_PATH.exists():
        return {}
    with open(SEPA_POS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("positions", {})


def save_positions(positions: dict) -> None:
    SEPA_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if SEPA_POS_PATH.exists():
        with open(SEPA_POS_PATH, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = {}
    existing["positions"] = positions
    with open(SEPA_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def add_position(
    code: str, name: str,
    entry_date: str, entry_price: int, quantity: int,
) -> None:
    positions = load_positions()
    existing  = positions.get(code)
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
        msg = f"chore: S4 추가매수 {code} (수량 {old_qty}→{new_qty}, 평단 {old_price:,}→{int(round(new_avg)):,})"
    else:
        positions[code] = {
            "name":                 name,
            "entry_date":           entry_date,
            "entry_price":          entry_price,
            "quantity":             quantity,
            "peak_price":           entry_price,
            "peak_gain_pct":        0.0,
            "early_gain_triggered": False,
        }
        msg = f"chore: S4 포지션 추가 {code} {name} @{entry_price:,}"

    save_positions(positions)
    git_commit_push([str(SEPA_POS_PATH)], msg)


def remove_position(code: str) -> None:
    positions = load_positions()
    positions.pop(code, None)
    save_positions(positions)
    git_commit_push([str(SEPA_POS_PATH)], f"chore: S4 포지션 제거 {code}")


def _set_pos_flag(code: str, key: str, flag: bool, msg: str) -> None:
    """포지션 단일 플래그 설정 공통 헬퍼"""
    if not SEPA_POS_PATH.exists():
        return
    with open(SEPA_POS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    pos = data.get("positions", {}).get(code)
    if pos is None:
        return
    pos[key] = flag
    with open(SEPA_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    git_commit_push([str(SEPA_POS_PATH)], msg)


def set_stop_loss_pending(code: str, flag: bool = True) -> None:
    """손절 매도 플래그 설정 (다음날 아침 09:00 시초가 매도 예약)"""
    _set_pos_flag(code, "stop_loss_pending", flag,
                  f"chore: S4 손절플래그 {code}={'ON' if flag else 'OFF'}")


def set_trail_stop_pending(code: str, flag: bool = True) -> None:
    """트레일링스탑 매도 플래그 설정 (다음날 아침 09:00 시초가 매도 예약)"""
    _set_pos_flag(code, "trail_stop_pending", flag,
                  f"chore: S4 트레일링스탑플래그 {code}={'ON' if flag else 'OFF'}")


def set_take_profit_pending(code: str, flag: bool = True) -> None:
    """익절 매도 플래그 설정 (다음날 아침 09:00 시초가 매도 예약)"""
    _set_pos_flag(code, "take_profit_pending", flag,
                  f"chore: S4 익절플래그 {code}={'ON' if flag else 'OFF'}")


def set_ma_exit_pending(code: str, flag: bool = True) -> None:
    """MA이탈 매도 플래그 설정 (다음날 아침 09:00 시초가 매도 예약)"""
    _set_pos_flag(code, "ma_exit_pending", flag,
                  f"chore: S4 MA이탈플래그 {code}={'ON' if flag else 'OFF'}")


# ── 매수 대기 목록 (저녁 배치에서 결정 → 아침에 실행) ──────────────────────

def get_entry_pending() -> list:
    """저녁 배치에서 결정한 매수 후보 목록 반환"""
    if not SEPA_POS_PATH.exists():
        return []
    with open(SEPA_POS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f).get("entry_pending", [])
        except json.JSONDecodeError:
            return []


def set_entry_pending(entries: list) -> None:
    """매수 후보 목록 저장 (저녁 배치 호출)"""
    SEPA_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if SEPA_POS_PATH.exists():
        with open(SEPA_POS_PATH, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = {}
    existing["entry_pending"] = entries
    with open(SEPA_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    n     = len(entries)
    codes = " ".join(e["code"] for e in entries[:3]) + ("..." if n > 3 else "")
    git_commit_push(
        [str(SEPA_POS_PATH)],
        f"chore: S4 매수대기 {n}종목" + (f" {codes}" if n else ""),
    )


def update_position_peak(code: str, current_price: int, current_date: str) -> None:
    """고점 가격·수익률 갱신 + 조기익절 트리거 체크 (21일 이내 +15% → 목표 +25%)"""
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
                    f"[S4 조기익절트리거] [{code}] {pos.get('name', '')}  "
                    f"+{gain:.1%} ({days_held}일) → 목표 +25%로 상향"
                )
        except (ValueError, KeyError):
            pass

    if changed:
        positions[code] = pos
        save_positions(positions)


# ── git 커밋·푸시 ──────────────────────────────────────────────────────

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
