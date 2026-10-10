"""autotrade 상태 파일 I/O (멱등성)

- positions_v3.csv (ETF 보유)   : engine_v3가 읽는 파일
- positions_stock.csv (종목 보유): engine_v3가 읽는 파일
- orders_YYYYMMDD.json          : 하루 주문 계획
- trade_log_*.csv               : 체결 이력
- equity_log.csv                : 날짜별 평가자산
- state/*.json                  : 멱등성 플래그 (주문키-체결상태)
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from autotrade import config as cfg


# ── Orders (하루 주문 계획) ─────────────────────────────────────
def orders_path(target_date: str) -> Path:
    return cfg.abspath(cfg.get("paths.orders_dir")) / f"orders_{target_date.replace('-', '')}.json"


def save_orders(target_date: str, data: dict) -> Path:
    p = orders_path(target_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return p


def load_orders(target_date: str) -> Optional[dict]:
    p = orders_path(target_date)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Positions (ETF = 모듈 A) ─────────────────────────────────────
# engine_v3 가 읽는 처음 5개 컬럼: 패턴, 진입일, 진입가, 계약수, 손절
# 자동매매가 추가하는 열: etf매수가, etf매수일 (10/13 이후)
A_POS_COLS = ["패턴", "진입일", "진입가", "계약수", "손절", "etf매수가", "etf매수일"]


def load_A_positions() -> pd.DataFrame:
    p = cfg.abspath(cfg.get("paths.positions_A"))
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(columns=A_POS_COLS)
    df = pd.read_csv(p)
    for c in A_POS_COLS:
        if c not in df.columns:
            df[c] = None
    return df


def save_A_positions(df: pd.DataFrame) -> None:
    p = cfg.abspath(cfg.get("paths.positions_A"))
    base = [c for c in A_POS_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in A_POS_COLS]
    df[base + extra].to_csv(p, index=False)


# ── Positions (종목 = 모듈 B) ────────────────────────────────────
# engine_v3 가 읽는 열: code, name, 매수일, 매수가, 수량
B_POS_COLS = ["code", "name", "매수일", "매수가", "수량"]


def load_B_positions() -> pd.DataFrame:
    p = cfg.abspath(cfg.get("paths.positions_B"))
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(columns=B_POS_COLS)
    df = pd.read_csv(p, dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    for c in B_POS_COLS:
        if c not in df.columns:
            df[c] = None
    return df


def save_B_positions(df: pd.DataFrame) -> None:
    p = cfg.abspath(cfg.get("paths.positions_B"))
    base = [c for c in B_POS_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in B_POS_COLS]
    df[base + extra].to_csv(p, index=False)


# 하위 호환 (v1 이름)
load_etf_positions = load_A_positions
save_etf_positions = save_A_positions
load_stock_positions = load_B_positions
save_stock_positions = save_B_positions


# ── Trade log ──────────────────────────────────────────────────
def append_trade_log(module: str, row: dict) -> None:
    """module: 'A' | 'B' — 하나의 trade_log.csv 에 'module' 열로 구분 저장."""
    p = cfg.abspath(cfg.get("paths.trade_log"))
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"module": module, **row}
    is_new = not p.exists()
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)


# ── 모듈별 Ledger ──────────────────────────────────────────────
def append_ledger(module: str, row: dict) -> None:
    """module 'A' | 'B'. 날짜별 현금·보유평가·평가자산 기록."""
    key = "paths.ledger_A" if module == "A" else "paths.ledger_B"
    p = cfg.abspath(cfg.get(key))
    p.parent.mkdir(parents=True, exist_ok=True)
    is_new = not p.exists()
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)


# ── 멱등성 플래그 (주문키 상태) ─────────────────────────────────
# 주문키: f"{YYYYMMDD}:{code}:{reason}"
# 상태: "pending" → "submitted" → "filled" | "failed"
def _state_file(date_str: str) -> Path:
    return cfg.abspath(cfg.get("paths.state_dir")) / f"state_{date_str.replace('-', '')}.json"


def load_state(date_str: str) -> dict:
    p = _state_file(date_str)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(date_str: str, state: dict) -> None:
    p = _state_file(date_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def mark_order(date_str: str, order_key: str, status: str, extra: Optional[dict] = None) -> None:
    state = load_state(date_str)
    entry = state.get(order_key, {})
    entry["status"] = status
    if extra:
        entry.update(extra)
    state[order_key] = entry
    save_state(date_str, state)


def order_status(date_str: str, order_key: str) -> Optional[str]:
    return load_state(date_str).get(order_key, {}).get("status")
