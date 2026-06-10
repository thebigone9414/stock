"""
CANSLIM 전략3 데이터 영속성 레이어

data/canslim_data.json    — 일일 스크리닝 결과 (update_canslim.py 기록)
data/canslim_positions.json — S3 포지션 (strategies/canslim.py 기록)
"""
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger

CANSLIM_DATA_PATH        = Path("data/canslim_data.json")
CANSLIM_POS_PATH         = Path("data/canslim_positions.json")
CANSLIM_CA_SCREENED_PATH = Path("data/dart_ca_screened.json")
CANSLIM_STOP_BL_PATH     = Path("data/canslim_stop_blacklist.json")

STOP_BLACKLIST_DAYS = 90  # 손절 후 재진입 금지 일수

_EMPTY_DATA = {"updated_at": "", "market_uptrend": False, "stocks": {}}
_EMPTY_POS  = {"positions": {}}


# ── canslim_data (스크리닝 결과) ────────────────────────────────────────

def load_data() -> dict:
    if not CANSLIM_DATA_PATH.exists():
        return {k: v for k, v in _EMPTY_DATA.items()}
    with open(CANSLIM_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    CANSLIM_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANSLIM_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_buy_candidates() -> list:
    """all_pass=True인 매수 후보 [(code, info)] 리스트 반환 (score 내림차순)"""
    data   = load_data()
    stocks = data.get("stocks", {})
    result = [
        (code, info)
        for code, info in stocks.items()
        if info.get("all_pass")
    ]
    return sorted(result, key=lambda x: x[1].get("score", 0), reverse=True)


# ── C·A 사전 필터링 목록 (dart_ca_screened.json) ────────────────────────

def load_ca_screened() -> dict:
    """C·A 조건 통과 사전 필터링 결과 로드"""
    if not CANSLIM_CA_SCREENED_PATH.exists():
        return {"updated_at": "", "screened": []}
    with open(CANSLIM_CA_SCREENED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ca_screened(data: dict) -> None:
    CANSLIM_CA_SCREENED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANSLIM_CA_SCREENED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_screened_universe() -> list:
    """C·A 통과 종목 리스트 반환 [{"code", "name", "sector", ...}]
    파일이 없거나 비어 있으면 CANSLIM_UNIVERSE(전체 목록) 반환.
    """
    data     = load_ca_screened()
    screened = data.get("screened", [])
    if screened:
        return screened
    # 사전 필터링 없음 → 전체 유니버스로 폴백
    from data.canslim_universe import CANSLIM_UNIVERSE
    return [{"code": s["code"], "name": s["name"], "sector": s["sector"]} for s in CANSLIM_UNIVERSE]


# ── C·A 조건 판별 함수 (update_dart.py / update_canslim.py 공유) ───────────

def check_C(corp: dict) -> bool:
    """C 조건: 최근 분기 EPS YoY +25% 이상"""
    if not corp:
        return False
    qeps = corp.get("quarterly_eps", [])
    if len(qeps) < 2:
        return False
    latest = next((q for q in qeps if q.get("eps") is not None), None)
    if not latest or not latest["eps"]:
        return False
    qnum, qyear = latest["quarter"], latest["year"]
    prev = next(
        (q for q in qeps
         if q["year"] == qyear - 1 and q["quarter"] == qnum and q.get("eps") is not None),
        None,
    )
    if not prev or not prev["eps"] or prev["eps"] <= 0:
        return False
    return (latest["eps"] - prev["eps"]) / abs(prev["eps"]) >= 0.25


def check_A(corp: dict) -> bool:
    """A 조건: 최근 3년 연간 EPS CAGR +15% 이상"""
    if not corp:
        return False
    aeps = [a for a in corp.get("annual_eps", []) if a.get("eps") is not None and a["eps"] > 0]
    if len(aeps) < 2:
        return False
    aeps_sorted = sorted(aeps, key=lambda x: x["year"], reverse=True)
    latest  = aeps_sorted[0]["eps"]
    oldest  = aeps_sorted[min(2, len(aeps_sorted) - 1)]
    years   = aeps_sorted[0]["year"] - oldest["year"]
    if years <= 0:
        return False
    return (latest / oldest["eps"]) ** (1 / years) - 1 >= 0.15


# ── 손절 블랙리스트 (재진입 금지) ────────────────────────────────────────

def get_stop_blacklist() -> dict:
    """{code: stop_date} 반환 — 파일 없으면 빈 dict"""
    if not CANSLIM_STOP_BL_PATH.exists():
        return {}
    with open(CANSLIM_STOP_BL_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("stop_blacklist", {})


def add_to_stop_blacklist(code: str, stop_date: str) -> None:
    """손절 종목을 블랙리스트에 추가 (STOP_BLACKLIST_DAYS 동안 재진입 금지)"""
    bl = get_stop_blacklist()
    bl[code] = stop_date
    CANSLIM_STOP_BL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANSLIM_STOP_BL_PATH, "w", encoding="utf-8") as f:
        json.dump({"stop_blacklist": bl}, f, ensure_ascii=False, indent=2)
    git_commit_push(
        [str(CANSLIM_STOP_BL_PATH)],
        f"chore: S3 손절 블랙리스트 추가 {code} ({stop_date})",
    )


# ── canslim_positions (포지션) ──────────────────────────────────────────

def load_positions() -> dict:
    if not CANSLIM_POS_PATH.exists():
        return {}
    with open(CANSLIM_POS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("positions", {})


def save_positions(positions: dict) -> None:
    CANSLIM_POS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANSLIM_POS_PATH, "w", encoding="utf-8") as f:
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
        msg = f"chore: S3 추가매수 {code} (수량 {old_qty}→{new_qty}, 평단 {old_price:,}→{int(round(new_avg)):,})"
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
        msg = f"chore: S3 포지션 추가 {code} {name} @{entry_price:,}"

    save_positions(positions)
    git_commit_push([str(CANSLIM_POS_PATH)], msg)


def remove_position(code: str) -> None:
    positions = load_positions()
    positions.pop(code, None)
    save_positions(positions)
    git_commit_push([str(CANSLIM_POS_PATH)], f"chore: S3 포지션 제거 {code}")


def set_ma_exit_pending(code: str, flag: bool = True) -> None:
    """MA이탈 매도 플래그 설정 (다음날 아침 09:00 시초가 매도 예약)"""
    if not CANSLIM_POS_PATH.exists():
        return
    with open(CANSLIM_POS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    pos = data.get("positions", {}).get(code)
    if pos is None:
        return
    pos["ma_exit_pending"] = flag
    with open(CANSLIM_POS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    git_commit_push(
        [str(CANSLIM_POS_PATH)],
        f"chore: S3 MA이탈플래그 {code}={'ON' if flag else 'OFF'}",
    )


def update_position_peak(code: str, current_price: int, current_date: str) -> None:
    """고점 가격·수익률 갱신 + 조기익절 트리거 체크"""
    positions = load_positions()
    pos = positions.get(code)
    if not pos:
        return

    ep   = pos.get("entry_price", 0)
    gain = (current_price - ep) / ep if ep > 0 else 0.0

    if current_price > pos.get("peak_price", 0):
        pos["peak_price"]    = current_price
        pos["peak_gain_pct"] = round(gain, 6)

    # 조기 +15% 달성 여부 체크 (진입 후 21 캘린더일 이내)
    if not pos.get("early_gain_triggered") and gain >= 0.15:
        from datetime import datetime
        try:
            entry_dt   = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
            current_dt = datetime.strptime(current_date, "%Y-%m-%d")
            days_held  = (current_dt - entry_dt).days
            if days_held <= 21:
                pos["early_gain_triggered"] = True
                logger.info(
                    f"[S3 조기익절트리거] [{code}] {pos.get('name', '')}  "
                    f"+{gain:.1%} ({days_held}일) → 목표 +25%로 상향"
                )
        except (ValueError, KeyError):
            pass

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
