"""autotrade/reconcile.py — 08:20 잔고 대사

증권사 KIS 잔고를 조회해 로컬 상태 파일(positions_v3.csv / positions_stock.csv) 과 비교.
불일치 발견 시 자동매매 중단 + 텔레그램 경고.

CAPITAL 고정 모드라 잔고 금액 검증은 하지 않음 (사용자가 입금 책임).
수량/보유 종목만 비교.

실행: python -m autotrade.reconcile
"""
from __future__ import annotations

import sys

import pandas as pd
from loguru import logger

from autotrade import common, config as cfg, state as S, report as R
from config.settings import get_settings
from kis.factory import KIS


def _compare_holdings(kis_positions: list, local_etf: pd.DataFrame, local_stock: pd.DataFrame) -> list[str]:
    """KIS 잔고 vs 로컬 positions. 수량 차이·누락 종목 리스트 반환."""
    diffs: list[str] = []

    # KIS 잔고 — {code: qty}
    kis_map: dict[str, int] = {}
    for p in kis_positions:
        code = str(p.code).zfill(6)
        kis_map[code] = int(p.quantity)

    # 로컬 — ETF
    local_map: dict[str, int] = {}
    etf_long = str(cfg.get("etf.long_code")).zfill(6)
    etf_short = str(cfg.get("etf.short_code")).zfill(6)
    for _, r in local_etf.iterrows():
        qty = int(r.get("계약수") or 0)
        pat = int(r.get("패턴") or 0)
        # pat 11은 숏 (인버스), 나머지는 롱 (레버리지)
        code = etf_short if pat == 11 else etf_long
        local_map[code] = local_map.get(code, 0) + qty

    # 로컬 — 종목
    for _, r in local_stock.iterrows():
        code = str(r.get("code") or "").zfill(6)
        qty = int(r.get("수량") or 0)
        if code:
            local_map[code] = local_map.get(code, 0) + qty

    # 비교
    all_codes = set(kis_map) | set(local_map)
    for code in sorted(all_codes):
        k_qty = kis_map.get(code, 0)
        l_qty = local_map.get(code, 0)
        if k_qty != l_qty:
            diffs.append(f"[{code}] KIS={k_qty}주  로컬={l_qty}주  차이 {k_qty - l_qty:+}")
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

    local_etf = S.load_etf_positions()
    local_stock = S.load_stock_positions()

    diffs = _compare_holdings(bal.positions, local_etf, local_stock)
    if diffs:
        msg = R.format_reconcile_mismatch(diffs)
        logger.error(msg)
        if notifier:
            notifier.notify(msg)
        return 1

    logger.info(
        f"[reconcile] 대사 OK — KIS 잔고 {len(bal.positions)}종목  "
        f"로컬 ETF {len(local_etf)}건 / 종목 {len(local_stock)}건"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
