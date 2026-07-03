"""
장중 포지션 모니터링 (cron-job.org에서 09:00~15:00 KST 매시 트리거)

보유 포지션의 현재가를 실시간 조회하여 손절/익절 즉시 집행.
다음날 아침 집행으로 인한 시간 리스크 제거.

[S2/S3/S4/수동 조건]
  ① 손절:       현재가 ≤ 매수가 × 0.93
  ② 부분익절:   현재가 ≥ 매수가 × 1.20 (처음) → 절반 매도
  ③ 러너 MA이탈: 고점 ≥ +20% 후 MA21 < MA62 AND MA62 5일 하락
  ④ 트레일링스탑: 고점 ≥ +10% 후 현재가 < 고점 × 0.90

[S5 조건]
  ① 손절:      현재가 ≤ 매수가 × 0.93
  ② MA5 하회:  현재가 < MA5
  ③ 시간스탑:  보유 21일 이상

[248 ETF 전략 — 15:00 배치 전용]
  트리거: 현대차(005380) 하락률만 본다.
  현대차 N% 하락 → 현대차 N주 매수 → 필요 금액 = 현대차 현재가 × N
  나머지 대상 종목은 그 필요 금액을 자기 현재가로 나눈 수량만큼 매수.
  매도 없음.
"""
import json
import sys
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

# ── 248 ETF 전략 설정 ────────────────────────────────────────────────
# 트리거: 현대차(005380) 하락률 하나만 본다.
#   현대차 N% 하락 → 현대차 N주 매수 → 필요 금액 = 현대차 현재가 × N
#   나머지 대상 종목은 필요 금액을 자기 현재가로 나눈 수량만큼 매수 (버림)
# 매도는 없다.
_ETF_248_TRIGGER = ("005380", "현대차")
_ETF_248_TARGETS = [
    ("005380", "현대차"),
    ("122630", "KODEX 레버리지"),
    ("462330", "KODEX 2차전지산업레버리지"),
    ("0190C0", "RISE 현대차고정피지컬AI"),
]

# 248 전략 대상 코드 집합 — 매수 로직·자동 청산 금지 양쪽 사용
_ETF_248_CODES = {code for code, _ in _ETF_248_TARGETS}
# 자동 청산 금지 — manual_store.NO_AUTO_SELL_CODES와 동기화
_PROTECTED_CODES = _ETF_248_CODES | manual_store.NO_AUTO_SELL_CODES


def _get_price(market, code: str) -> int:
    try:
        return market.get_quote(code).price
    except Exception as e:
        logger.warning(f"[장중모니터] [{code}] 현재가 조회 실패: {e}")
        return 0


def _get_prev_close(market, code: str) -> int:
    """전일 종가: ohlcv_cache → KIS API 순으로 조회
    장중 API 호출 시 당일 불완전 데이터가 마지막 행에 포함될 수 있으므로
    오늘 날짜 행을 제외하고 전 거래일 종가를 반환한다.
    """
    cache_path = Path("data/ohlcv_cache.json")
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            closes = cache.get(code, {}).get("closes", [])
            if closes:
                return int(closes[-1])
        except Exception:
            pass
    try:
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        df = market.get_ohlcv_long(code, days=5)
        if not df.empty:
            prev = df[df["date"].apply(lambda d: d.strftime("%Y-%m-%d")) < today_str]
            if not prev.empty:
                return int(prev["close"].iloc[-1])
            return int(df["close"].iloc[-1])
    except Exception as e:
        logger.warning(f"[248전략] [{code}] 전일종가 API 조회 실패: {e}")
    return 0


def _run_etf_248(market, order, notifier, now_str: str, is_paper: bool) -> list:
    """248 ETF 전략 — 15:00 배치 전용
    트리거: 현대차 하락률 → 현대차 N주 매수 → 필요 금액 = 현대차 현재가 × N
    나머지 대상 종목은 필요 금액을 자기 현재가로 나눈 수량만큼 매수 (버림).
    매도 없음.
    """
    msgs = []
    lines = [f"[248 ETF 전략] {now_str}"]

    trig_code, trig_name = _ETF_248_TRIGGER
    prev_close = _get_prev_close(market, trig_code)
    curr = _get_price(market, trig_code)
    if prev_close <= 0 or curr <= 0:
        logger.warning(f"[248전략] 트리거 [{trig_code}] {trig_name} 시세 조회 실패 — 전체 건너뜀")
        lines.append(f"  트리거 [{trig_code}] {trig_name} 시세 조회 실패 — 스킵")
        if notifier:
            notifier.notify("\n".join(lines))
        return msgs

    pct = (curr - prev_close) / prev_close * 100
    n   = min(int(abs(pct)), 10)

    logger.info(
        f"[248전략] 트리거 [{trig_code}] {trig_name}  "
        f"전일:{prev_close:,} 현재:{curr:,} {pct:+.2f}%"
    )
    lines.append(
        f"  트리거 [{trig_code}] {trig_name}  "
        f"전일:{prev_close:,} → 현재:{curr:,} ({pct:+.2f}%)"
    )

    if not (pct <= -1.0 and n >= 1):
        no_action = "  매수 조건 미충족 (현대차 -1% 미만)"
        lines.append(no_action)
        logger.info(f"[248전략] {no_action.strip()}")
        if notifier:
            notifier.notify("\n".join(lines))
        return msgs

    budget = curr * n  # 현대차 현재가 × N (필요 금액)
    lines.append(f"  → 현대차 {n}주 매수 기준액 {budget:,}원")

    for code, name in _ETF_248_TARGETS:
        if code == trig_code:
            qty = n  # 현대차 자신은 N주 그대로
            price = curr
        else:
            price = _get_price(market, code)
            if price <= 0:
                logger.warning(f"[248전략] [{code}] {name} 현재가 조회 실패 — 건너뜀")
                lines.append(f"  [{code}] {name}  현재가 조회 실패 — 스킵")
                continue
            qty = budget // price
            if qty < 1:
                lines.append(f"  [{code}] {name}  현재가:{price:,}  기준액 부족 — 스킵")
                continue

        action_msg = (
            f"  매수 [{code}] {name}\n"
            f"  현재가:{price:,}  {qty}주 매수  약 {price * qty:,}원"
        )
        logger.info(f"[248매수] {action_msg.strip()}")
        try:
            if not is_paper:
                resp = order.buy_market(code, qty)
                logger.info(f"[248전략] 매수 응답: {resp}")
            else:
                logger.info(f"[248전략] [{code}] 모의투자 — 매수 생략")
            msgs.append(action_msg)
            lines.append(action_msg)
        except Exception as e:
            logger.error(f"[248전략] [{code}] 매수 실패: {e}")

    if notifier:
        notifier.notify("\n".join(lines))

    return msgs


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

    # ── 248 ETF 전략 (15:00 배치 전용) ──────────────────────────────────
    etf_buy_msgs: list[str] = []
    if now.hour == 15:
        etf_buy_msgs = _run_etf_248(market, order, notifier, now_str, is_paper)

    # ── 완료 알림 (매도 없어도 heartbeat 전송) ───────────────────────────
    summary_line = "  |  ".join(holding_summary) if holding_summary else "보유 없음"
    heartbeat = f"[장중모니터] {now_str}\n{summary_line}"
    if executed:
        heartbeat += f"\n매도 {len(executed)}건 집행"
    if etf_buy_msgs:
        heartbeat += f"\n248ETF 매수 {len(etf_buy_msgs)}건 집행"
    if executed or etf_buy_msgs:
        logger.info(f"[장중모니터] 완료 — 매도:{len(executed)}건  248매수:{len(etf_buy_msgs)}건")
    logger.info(heartbeat)
    if notifier:
        notifier.notify(heartbeat)
