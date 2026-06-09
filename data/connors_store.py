"""
Connors RSI(2) 전략6 데이터 영속성 레이어

data/connors_data.json      — 일일 스크리닝 결과 (update_connors.py 기록)
data/connors_positions.json — S6 포지션 (strategies/connors.py 기록)

S6는 단기 평균회귀 전략이므로 스크리닝 데이터에 rsi2/rsi2_exit 를 함께 저장해
보유 중인 종목의 청산 신호도 처리한다.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from loguru import logger

CONNORS_DATA_PATH = Path("data/connors_data.json")
CONNORS_POS_PATH  = Path("data/connors_positions.json")

_EMPTY_DATA = {"updated_at": "", "market_uptrend": False, "stocks": {}}


# ── connors_data (스크리닝 결과) ─────────────────────────────────────────

def load_data() -> dict:
    if not CONNORS_DATA_PATH.exists():
        return {**_EMPTY_DATA}
    with open(CONNORS_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    CONNORS_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONNORS_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_buy_candidates() -> list:
    """all_pass=True인 매수 후보 [(code, info)] 리스트 (rsi2 오름차순 — 더 과매도 순)"""
    data   = load_data()
    stocks = data.get("stocks", {})
    result = [(code, info) for code, info in stocks.items() if info.get("all_pass")]
    return sorted(result, key=lambda x: x[1].get("rsi2", 100))


def get_exit_signal(code: str) -> bool:
    """보유 종목의 RSI(2) >= 65 청산 신호 여부"""
    data  = load_data()
    stock = data.get("stocks", {}).get(code, {})
    return bool(stock.get("rsi2_exit", False))


def get_rsi2(code: str) -> float:
    """보유 종목의 현재 RSI(2) 값 반환 (없으면 -1)"""
    data  = load_data()
    stock = data.get("stocks", {}).get(code, {})
    return float(stock.get("rsi2", -1.0))


# ── connors_positions (포지션) ───────────────────────────────────────────

def load_positions() -> dict:
    if not CONNORS_POS_PATH.exists():
        return {}
    with open(CONNORS_POS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("positions", {})


def save_positions(positions: dict) -> None:
    CONNORS_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONNORS_POS_PATH, "w", encoding="utf-8") as f:
        json.dump({"positions": positions}, f, ensure_ascii=False, indent=2)


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
        msg = f"chore: S6 추가매수 {code} (수량 {old_qty}→{new_qty})"
    else:
        positions[code] = {
            "name":       name,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "quantity":   quantity,
        }
        msg = f"chore: S6 포지션 추가 {code} {name} @{entry_price:,}"
    save_positions(positions)
    git_commit_push([str(CONNORS_POS_PATH)], msg)


def remove_position(code: str) -> None:
    positions = load_positions()
    positions.pop(code, None)
    save_positions(positions)
    git_commit_push([str(CONNORS_POS_PATH)], f"chore: S6 포지션 제거 {code}")


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
        run(["git", "pull", "--rebase", "--autostash"])
        rc, out = run(["git", "push"])
        if rc == 0:
            logger.info(f"git push 완료: {message}")
            return
        wait = 2 ** attempt
        logger.warning(f"git push 실패 (시도 {attempt+1}/4) {wait}s 후 재시도: {out.strip()}")
        time.sleep(wait)

    logger.error("git push 최종 실패")
