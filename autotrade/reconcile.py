"""autotrade/reconcile.py — 08:20 잔고·장부 대사 (v2)

autotrade 추적 종목(positions_v3.csv + positions_B.csv) 과 KIS 잔고 비교.
사용자 수동 보유(KODEX 레버리지·2차전지·현대차 등)는 간섭 안 함:
  'KIS 수량 >= 로컬 autotrade 수량' 이면 OK, 반대면 경고.

CAPITAL 고정 모드라 잔고 금액 검증은 생략.
"""
from __future__ import annotations

import sys

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg, state as S, report as R
from config.settings import get_settings
from kis.factory import KIS


def _compare(kis_positions: list, local_A: pd.DataFrame, local_B: pd.DataFrame) -> list[str]:
    diffs: list[str] = []

    kis_map: dict[str, int] = {}
    for p in kis_positions:
        code = str(p.code).zfill(6)
        kis_map[code] = int(p.quantity)

    local_map: dict[str, int] = {}
    etf_long  = str(cfg.get("etf.long_code")).zfill(6)
    etf_short = str(cfg.get("etf.short_code")).zfill(6)
    for _, r in local_A.iterrows():
        pat = int(r.get("패턴") or 0)
        qty = int(r.get("계약수") or 0)
        code = etf_short if pat == 11 else etf_long
        local_map[code] = local_map.get(code, 0) + qty
    for _, r in local_B.iterrows():
        code = str(r.get("code") or "").zfill(6)
        qty = int(r.get("수량") or 0)
        if code:
            local_map[code] = local_map.get(code, 0) + qty

    # "KIS >= 로컬" 이면 OK (수동 추가 보유 허용)
    for code in sorted(local_map):
        l_qty = local_map[code]
        k_qty = kis_map.get(code, 0)
        if k_qty < l_qty:
            diffs.append(
                f"[{code}] KIS={k_qty}주  로컬 autotrade={l_qty}주  부족 {l_qty - k_qty}  "
                f"(autotrade 포지션이 KIS 잔고에 없음)"
            )
    return diffs


def run() -> int:
    notifier = common.setup()
    today = common.today_str()

    if not common.is_trading_today():
        logger.info(f"[reconcile] {today} 휴장일 — 종료")
        return 0

    settings = get_settings()
    kis = KIS(settings)

    try:
        bal = kis.account.get_balance()
    except Exception as e:
        logger.error(f"[reconcile] 잔고 조회 실패: {e}")
        if notifier:
            notifier.notify(f"[reconcile] 잔고 조회 실패\n{str(e)[:300]}")
        return 1

    local_A = S.load_A_positions()
    local_B = S.load_B_positions()

    diffs = _compare(bal.positions, local_A, local_B)
    if diffs:
        msg = R.format_reconcile_mismatch(diffs)
        logger.error(msg)
        if notifier:
            notifier.notify(msg)
        return 1

    logger.info(
        f"[reconcile] 대사 OK — KIS {len(bal.positions)}종목  "
        f"로컬 A {len(local_A)}건 / B {len(local_B)}건"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
