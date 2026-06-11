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
    """포지션 추가 — 동일 code가 이미 있으면 가중평균 평단가로 통합"""
    data = load()
    positions = data.setdefault("positions", {})
    existing = positions.get(code)

    if existing and existing.get("quantity", 0) > 0 and existing.get("entry_price", 0) > 0:
        old_qty   = existing["quantity"]
        old_price = existing["entry_price"]
        new_qty   = old_qty + quantity
        new_avg   = (old_price * old_qty + entry_price * quantity) / new_qty
        positions[code] = {
            **existing,
            "name":            name,
            "last_entry_date": entry_date,
            "entry_price":     int(round(new_avg)),
            "quantity":        new_qty,
        }
        msg = f"chore: S2 포지션 추가매수 {code} (수량 {old_qty}→{new_qty}, 평단 {old_price:,}→{int(round(new_avg)):,})"
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
        msg = f"chore: S2 포지션 추가 {code} {name} @{entry_price:,}"

    save(data)
    git_commit_push([str(MA_DATA_PATH)], msg)


def remove_position(code: str) -> None:
    data = load()
    data.setdefault("positions", {}).pop(code, None)
    save(data)
    git_commit_push([str(MA_DATA_PATH)], f"chore: S2 포지션 제거 {code}")


def get_base_capital() -> int:
    """슬롯 확장 기준 자산 (최초 기록 시점의 총평가금액)"""
    return load().get("base_capital", 0)


def set_base_capital(amount: int) -> None:
    """기준 자산 저장 (최초 1회 설정 후 변경 없음)"""
    data = load()
    data["base_capital"] = amount
    save(data)


def extra_slots(base_capital: int, total_eval: int, slot_ratio: float = 0.20) -> int:
    """자산 증가 기반 추가 슬롯 수 계산 (수익 + 추가 입금 모두 반영)
    - (현재 총자산 - 기준 자산)이 기준 자산의 20%씩 증가할 때마다 S2+S3 공유 슬롯 1개 추가
    - 예) 기준 1000만 → 현재 1200만(수익/추가입금 무관): +1슬롯
    """
    if base_capital <= 0:
        return 0
    profit = total_eval - base_capital
    if profit <= 0:
        return 0
    return int(profit / (base_capital * slot_ratio))


def _set_pos_flag(code: str, key: str, flag: bool, msg: str) -> None:
    """포지션 단일 플래그 설정 공통 헬퍼"""
    data = load()
    pos = data.get("positions", {}).get(code)
    if pos is None:
        return
    pos[key] = flag
    save(data)
    git_commit_push([str(MA_DATA_PATH)], msg)


def set_stop_loss_pending(code: str, flag: bool = True) -> None:
    """포지션에 손절 대기 플래그 설정 (다음날 아침 시초가 매도 예약)"""
    _set_pos_flag(code, "stop_loss_pending", flag,
                  f"chore: S2 손절플래그 {code}={'ON' if flag else 'OFF'}")


def set_take_profit_pending(code: str, flag: bool = True) -> None:
    """포지션에 익절 대기 플래그 설정 (다음날 아침 시초가 매도 예약)"""
    _set_pos_flag(code, "take_profit_pending", flag,
                  f"chore: S2 익절플래그 {code}={'ON' if flag else 'OFF'}")


def set_trail_stop_pending(code: str, flag: bool = True) -> None:
    """포지션에 트레일링스탑 대기 플래그 설정 (다음날 아침 시초가 매도 예약)"""
    _set_pos_flag(code, "trail_stop_pending", flag,
                  f"chore: S2 트레일링스탑플래그 {code}={'ON' if flag else 'OFF'}")


def set_ma_exit_pending(code: str, flag: bool = True) -> None:
    """포지션에 MA이탈 대기 플래그 설정 (다음날 아침 시초가 매도 예약)"""
    _set_pos_flag(code, "ma_exit_pending", flag,
                  f"chore: S2 MA이탈플래그 {code}={'ON' if flag else 'OFF'}")


def update_position_peak(code: str, current_price: int, current_date: str) -> None:
    """고점 가격·수익률 갱신 + 조기익절 트리거(21일 이내 +15%) 체크"""
    data = load()
    pos  = data.get("positions", {}).get(code)
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
            days_held = (_dt.strptime(current_date, "%Y-%m-%d")
                         - _dt.strptime(pos["entry_date"], "%Y-%m-%d")).days
            if days_held <= 21:
                pos["early_gain_triggered"] = True
                changed = True
                logger.info(
                    f"[S2 조기익절트리거] [{code}] {pos.get('name', '')}  "
                    f"+{gain:.1%} ({days_held}일) → 목표 +25%로 상향"
                )
        except (ValueError, KeyError):
            pass

    if changed:
        data["positions"][code] = pos
        save(data)


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
        # push 전 원격 변경 사항 rebase — 동시 커밋으로 인한 non-fast-forward 방지
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
