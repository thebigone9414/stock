"""
장중 포지션 모니터링 (cron-job.org에서 09:00~15:00 KST 매시 트리거)

보유 포지션의 현재가를 실시간 조회하여 손절/익절 즉시 집행.
다음날 아침 집행으로 인한 시간 리스크 제거.

[S2/S3/S4/수동 조건]
  ① 손절:       현재가 ≤ 매수가 × 0.93
  ② 부분익절:   현재가 ≥ 매수가 × 1.15 (처음) → 절반 매도
  ③ 러너 MA이탈: 고점 ≥ +15% 후 MA21 < MA62 AND MA62 5일 하락
  ④ 트레일링스탑: 고점 ≥ +10% 후 현재가 < 고점 × 0.90

[S5 조건]
  ① 손절:      현재가 ≤ 매수가 × 0.93
  ② MA5 하회:  현재가 < MA5
  ③ 시간스탑:  보유 21일 이상

[자동 매도 금지 종목]
  manual_store.NO_AUTO_SELL_CODES 에 등록된 종목은 모든 청산 로직에서 제외.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

import data.ma_store as ma_store
import data.canslim_store as canslim_store
import data.sepa_store as sepa_store
import data.momentum_store as momentum_store
import data.manual_store as manual_store

KST = pytz.timezone("Asia/Seoul")

STOP_LOSS        = 0.07
RUNNER_THRESHOLD = 0.15
TRAIL_STOP_MIN   = 0.10
TRAIL_STOP_PCT   = 0.10

# 자동 청산 금지 — manual_store.NO_AUTO_SELL_CODES와 동기화
_PROTECTED_CODES = set(manual_store.NO_AUTO_SELL_CODES)


def _get_price(market, code: str, retries: int = 0) -> int:
    """현재가 조회. retries>0이면 실패 시 짧게 재시도."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            price = market.get_quote(code).price
            if price > 0:
                return price
            last_err = "price=0"
        except Exception as e:
            last_err = str(e)
        if attempt < retries:
            time.sleep(1.0 + attempt * 0.5)
    logger.warning(
        f"[장중모니터] [{code}] 현재가 조회 실패 (시도 {retries + 1}회): {last_err}"
    )
    return 0


def run_monitor(market, order, notifier=None, is_paper: bool = True) -> None:
    now      = datetime.now(KST)
    today    = now.strftime("%Y-%m-%d")
    now_str  = now.strftime("%Y-%m-%d %H:%M")

    market_open  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < market_open or now >= market_close:
        logger.info(f"[장중모니터] {now_str} 시장 외 시간 — 종료")
        return

    logger.info(f"[장중모니터] 시작 {now_str}")

    executed: list[str] = []
    holding_summary: list[str] = []

    # ── S2 / S3 / S4 공통 청산 ────────────────────────────────────────
    common_strategies = [
        ("S2",
         ma_store.get_positions,        ma_store.update_position_peak,
         ma_store.mark_half_sold,       ma_store.remove_position,
         ma_store.reduce_quantity),
        ("S3",
         canslim_store.load_positions,  canslim_store.update_position_peak,
         canslim_store.mark_half_sold,  canslim_store.remove_position,
         canslim_store.reduce_quantity),
        ("S4",
         sepa_store.load_positions,     sepa_store.update_position_peak,
         sepa_store.mark_half_sold,     sepa_store.remove_position,
         sepa_store.reduce_quantity),
    ]

    for strat, load_fn, upd_peak, mark_half, rm_pos, reduce_qty in common_strategies:
        positions = load_fn()
        for code, tranches in list(positions.items()):
            if code in _PROTECTED_CODES:
                continue
            for entry_date, pos in list(tranches.items()):
                entry_price = pos.get("entry_price", 0)
                quantity    = pos.get("quantity", 0)
                name        = pos.get("name", code)
                half_sold   = pos.get("half_sold", False)
                if not entry_price or not quantity:
                    continue

                current = _get_price(market, code)
                if current <= 0:
                    continue

                gain = (current - entry_price) / entry_price

                # 장중 신고가 갱신
                if current > pos.get("peak_price", entry_price):
                    upd_peak(code, entry_date, current, today)
                    pos = load_fn().get(code, {}).get(entry_date, pos)

                peak_price = pos.get("peak_price", entry_price)
                peak_gain  = (peak_price - entry_price) / entry_price
                stock_ma   = ma_store.get_stock(code)

                reason     = None
                sell_qty   = quantity
                is_partial = False

                if gain <= -STOP_LOSS:
                    reason = f"손절(-{STOP_LOSS:.0%})"

                elif gain >= RUNNER_THRESHOLD and not half_sold:
                    half_qty = quantity // 2
                    if half_qty > 0:
                        reason     = f"부분익절(+{RUNNER_THRESHOLD:.0%})"
                        sell_qty   = half_qty
                        is_partial = True

                elif peak_gain >= RUNNER_THRESHOLD:
                    if (stock_ma and stock_ma.get("ma21_below_ma62")
                            and stock_ma.get("ma62_declining_5d")):
                        reason = f"MA이탈(러너 고점{peak_gain:+.1%})"

                elif peak_gain >= TRAIL_STOP_MIN and current < peak_price * (1 - TRAIL_STOP_PCT):
                    reason = f"트레일링스탑(고점{peak_gain:+.1%}→고점-{TRAIL_STOP_PCT:.0%})"

                if not reason:
                    holding_summary.append(
                        f"[{code}]{name}({strat}) {gain:+.2%}"
                    )
                    logger.info(
                        f"[장중{strat}] [{code}] {name}  "
                        f"현재:{current:,}  {gain:+.2%}  고점:{peak_gain:+.2%}"
                    )
                    continue

                pnl = (current - entry_price) * sell_qty
                try:
                    if not is_paper:
                        resp = order.sell_market(code, sell_qty)
                        logger.info(f"[장중모니터] [{strat}] 매도 주문 응답: {resp}")
                    else:
                        logger.info(f"[장중모니터] [{strat}] 모의투자 — 주문 생략")

                    if is_partial:
                        reduce_qty(code, entry_date, sell_qty)
                        mark_half(code, entry_date)
                    else:
                        rm_pos(code, entry_date)
                        if strat == "S3" and "손절" in reason:
                            canslim_store.add_to_stop_blacklist(code, today)

                    msg = (
                        f"[장중{strat}{'부분' if is_partial else ''}매도] "
                        f"[{code}] {name}  {reason}\n"
                        f"매수:{entry_price:,} → 현재:{current:,}  "
                        f"{gain:+.2%} ({pnl:+,}원)"
                    )
                    logger.info(msg)
                    executed.append(msg)
                    if notifier:
                        notifier.notify(msg)

                except Exception as e:
                    logger.error(
                        f"[장중모니터] [{strat}] [{code}] {name} 매도 실패: {e}"
                    )

    # ── S5 전용 청산 ──────────────────────────────────────────────────
    s5_positions = momentum_store.load_positions()
    for code, tranches in list(s5_positions.items()):
        if code in _PROTECTED_CODES:
            continue
        for entry_date, pos in list(tranches.items()):
            entry_price = pos.get("entry_price", 0)
            quantity    = pos.get("quantity", 0)
            name        = pos.get("name", code)
            if not entry_price or not quantity:
                continue

            current = _get_price(market, code)
            if current <= 0:
                continue

            gain     = (current - entry_price) / entry_price
            stock_ma = ma_store.get_stock(code)
            reason   = None

            if gain <= -STOP_LOSS:
                reason = f"손절(-{STOP_LOSS:.0%})"
            elif stock_ma and current < stock_ma.get("ma5", 0):
                reason = f"MA5 하회({stock_ma.get('ma5', 0):,})"
            else:
                try:
                    days = (
                        datetime.strptime(today, "%Y-%m-%d")
                        - datetime.strptime(entry_date, "%Y-%m-%d")
                    ).days
                    if days >= 21:
                        reason = f"시간스탑({days}일)"
                except (ValueError, KeyError):
                    pass

            if not reason:
                holding_summary.append(f"[{code}]{name}(S5) {gain:+.2%}")
                logger.info(
                    f"[장중S5] [{code}] {name}  현재:{current:,}  {gain:+.2%}"
                )
                continue

            pnl = (current - entry_price) * quantity
            try:
                if not is_paper:
                    resp = order.sell_market(code, quantity)
                    logger.info(f"[장중모니터] [S5] 매도 주문 응답: {resp}")
                else:
                    logger.info(f"[장중모니터] [S5] 모의투자 — 주문 생략")

                momentum_store.remove_position(code, entry_date)

                msg = (
                    f"[장중S5매도] [{code}] {name}  {reason}\n"
                    f"매수:{entry_price:,} → 현재:{current:,}  "
                    f"{gain:+.2%} ({pnl:+,}원)"
                )
                logger.info(msg)
                executed.append(msg)
                if notifier:
                    notifier.notify(msg)

            except Exception as e:
                logger.error(f"[장중모니터] [S5] [{code}] {name} 매도 실패: {e}")

    # ── 수동 포지션 현황 (알림만, 매도 없음) ────────────────────────────
    for code, tranches in list(manual_store.load_positions().items()):
        if code in _PROTECTED_CODES:
            continue
        for entry_date, pos in list(tranches.items()):
            entry_price = pos.get("entry_price", 0)
            quantity    = pos.get("quantity", 0)
            name        = pos.get("name", code)
            if not entry_price or not quantity:
                continue
            current = _get_price(market, code)
            if current <= 0:
                continue
            gain = (current - entry_price) / entry_price
            holding_summary.append(f"[{code}]{name}(수동) {gain:+.2%}")
            logger.info(f"[장중수동] [{code}] {name}  현재:{current:,}  {gain:+.2%}")

    # ── 완료 알림 (매도 없어도 heartbeat 전송) ───────────────────────────
    summary_line = "  |  ".join(holding_summary) if holding_summary else "보유 없음"
    heartbeat = f"[장중모니터] {now_str}\n{summary_line}"
    if executed:
        heartbeat += f"\n매도 {len(executed)}건 집행"
        logger.info(f"[장중모니터] 완료 — 매도:{len(executed)}건")
    logger.info(heartbeat)
    if notifier:
        notifier.notify(heartbeat)
