#!/usr/bin/env python3
"""
통합 매매 결정 배치 (20:00 KST)

S2/S3/S4/S5/수동 포지션 청산 결정 + S2/S3/S4/S5 매수 후보 취합 → trade_queue.json 저장
→ 다음날 08:55 morning_trade.py가 읽어 09:00 시장가 실행

[청산 조건 — S2/S3/S4/수동 공통]
  ① 손절:          매수가 대비 -7%
  ② 러너(고점≥+20%): MA21 < MA62 AND MA62 5일 하락추세 → MA이탈 청산
  ③ 트레일링스탑:   고점≥+10% 이후 고점 대비 -10%
  ④ 익절:          +20% (21일 이내 +15% 달성 시 +25% 확장)

[청산 조건 — S5 전용]
  ① 손절:    -7%
  ② MA5 하회: 종가 < MA5
  ③ 시간스탑: 보유 21 달력일 이상

[포지션 구조]
  positions[code][entry_date] = tranche
  - 동일 종목 다른 날짜 = 독립 트랜치 (각각 별도 청산)
  - 교차 전략 보유 허용 (S2 보유 중 S5도 매수 가능)
  - 동일 전략 당일 중복만 차단

[매수 우선순위]
  S4(RS높을수록) > S5(연속순매수일수) > S3(score높을수록) > S2(몸통비율높을수록)
  S2+S3+S4+S5 매수 후보: 슬롯 제한 없음, 가용현금을 후보 수로 균등 배분
  수동 포지션은 슬롯 미사용
"""
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from utils.notifier import Notifier
from kis.factory import KIS
import data.ma_store as ma_store
import data.canslim_store as canslim_store
import data.sepa_store as sepa_store
import data.momentum_store as momentum_store
import data.manual_store as manual_store
from data.trade_queue_store import save_queue, git_commit_push, QUEUE_PATH
from data.shared_slots import count_shared

KST = pytz.timezone("Asia/Seoul")

STOP_LOSS        = 0.07
RUNNER_THRESHOLD = 0.20
TRAIL_STOP_MIN   = 0.10
TRAIL_STOP_PCT   = 0.10
TAKE_PROFIT      = 0.20
TAKE_PROFIT_EXT  = 0.25
S2_S3_S4_BASE    = 10
MAX_BUY_AMOUNT   = 4_000_000  # 일일 최대 매수 예산 (400만원)


def _sync_positions_from_balance(bal_positions: list, today_str: str) -> None:
    """KIS 잔고 → 포지션 스토어 동기화

    스토어에 없는 종목(수동 매수 등)을 자동으로 추가한다.
    전략 배정: MANUAL_CODES → 수동, SEPA 유니버스 → S4, CANSLIM 유니버스 → S3, 나머지 → S2
    """
    all_tracked = (
        set(ma_store.get_positions().keys())
        | set(canslim_store.load_positions().keys())
        | set(sepa_store.load_positions().keys())
        | set(momentum_store.load_positions().keys())
        | set(manual_store.load_positions().keys())
    )

    canslim_universe = set(canslim_store.load_data().get("stocks", {}).keys())
    sepa_universe    = set(sepa_store.load_data().get("stocks", {}).keys())

    for pos in bal_positions:
        if pos.code in all_tracked:
            continue

        entry_price = int(round(pos.avg_price)) or pos.current_price

        if pos.code in manual_store.MANUAL_CODES:
            strategy = "수동"
            manual_store.add_position(pos.code, pos.name, today_str, entry_price, pos.quantity)
        elif pos.code in sepa_universe:
            strategy = "S4"
            sepa_store.add_position(pos.code, pos.name, today_str, entry_price, pos.quantity)
        elif pos.code in canslim_universe:
            strategy = "S3"
            canslim_store.add_position(pos.code, pos.name, today_str, entry_price, pos.quantity)
        else:
            strategy = "S2"
            ma_store.add_position(pos.code, pos.name, today_str, entry_price, pos.quantity)

        logger.warning(
            f"[잔고동기화] [{pos.code}] {pos.name} → {strategy} 자동 추가  "
            f"평단:{entry_price:,}  수량:{pos.quantity}"
        )


def _decide_exits(today_str: str) -> list:
    """S2/S3/S4/수동 공통 청산 + S5 전용 청산 → sell 리스트 반환
    sell 항목에 entry_date 포함 → morning_trade에서 정확한 트랜치 제거
    """
    sell_list = []

    # ── S2/S3/S4/수동 공통 청산 ──────────────────────────────────────
    common_strategies = [
        ("S2",  ma_store.get_positions(),       ma_store.update_position_peak,
         lambda c, d: ma_store.get_positions().get(c, {}).get(d, {})),
        ("S3",  canslim_store.load_positions(), canslim_store.update_position_peak,
         lambda c, d: canslim_store.load_positions().get(c, {}).get(d, {})),
        ("S4",  sepa_store.load_positions(),    sepa_store.update_position_peak,
         lambda c, d: sepa_store.load_positions().get(c, {}).get(d, {})),
        ("수동", manual_store.load_positions(), manual_store.update_position_peak,
         lambda c, d: manual_store.load_positions().get(c, {}).get(d, {})),
    ]

    for strat_name, positions, update_peak, reload_pos in common_strategies:
        if not positions:
            logger.info(f"[매매결정 {strat_name}] 보유 포지션 없음")
            continue

        for code, tranches in list(positions.items()):
            for entry_date, pos in list(tranches.items()):
                entry_price = pos.get("entry_price", 0)
                name        = pos.get("name", code)
                quantity    = pos.get("quantity", 0)
                early_trig  = pos.get("early_gain_triggered", False)
                if not entry_price or not quantity:
                    continue

                stock_ma = ma_store.get_stock(code)
                if not stock_ma:
                    logger.warning(
                        f"[매매결정 {strat_name}] [{code}] {name} — MA데이터 없음, 건너뜀"
                    )
                    continue

                close = int(stock_ma.get("close", 0))
                if close <= 0:
                    continue

                update_peak(code, entry_date, close, today_str)
                pos        = reload_pos(code, entry_date)
                peak_price = pos.get("peak_price", entry_price)
                peak_gain  = (peak_price - entry_price) / entry_price
                gain       = (close - entry_price) / entry_price
                target     = TAKE_PROFIT_EXT if early_trig else TAKE_PROFIT

                reason = None

                if gain <= -STOP_LOSS:
                    reason = f"손절(-{STOP_LOSS:.0%})"
                    logger.warning(
                        f"[{strat_name} 손절] [{code}] {name}  "
                        f"매수:{entry_price:,} → 마감:{close:,}  {gain:+.2%}"
                    )

                elif peak_gain >= RUNNER_THRESHOLD:
                    if stock_ma.get("ma21_below_ma62") and stock_ma.get("ma62_declining_5d"):
                        reason = f"MA이탈(러너 고점{peak_gain:+.1%})"
                        logger.info(
                            f"[{strat_name} MA이탈] [{code}] {name}  "
                            f"고점:{peak_gain:+.1%}  현재:{gain:+.2%}"
                        )
                    else:
                        logger.info(
                            f"[{strat_name} 러너보유] [{code}] {name}  "
                            f"현재:{gain:+.2%}  고점:{peak_gain:+.2%}"
                        )

                elif peak_gain >= TRAIL_STOP_MIN and close < peak_price * (1 - TRAIL_STOP_PCT):
                    reason = f"트레일링스탑(고점{peak_gain:+.1%}→고점-{TRAIL_STOP_PCT:.0%})"
                    logger.info(
                        f"[{strat_name} 트레일링스탑] [{code}] {name}  "
                        f"고점:{peak_price:,}(+{peak_gain:.1%}) → 마감:{close:,}({gain:+.2%})"
                    )

                elif gain >= target:
                    reason = f"익절({target:+.0%}" + (" 확장)" if early_trig else ")")
                    logger.info(
                        f"[{strat_name} 익절] [{code}] {name}  "
                        f"마감:{close:,}  {gain:+.2%} ≥ {target:+.0%}"
                    )

                else:
                    ext_mark = " (확장목표)" if early_trig else ""
                    logger.info(
                        f"[{strat_name} 보유중] [{code}] {name}  [{entry_date}]  "
                        f"마감:{close:,}  {gain:+.2%}  목표:{target:+.0%}{ext_mark}  "
                        f"고점:{peak_gain:+.2%}"
                    )

                if reason:
                    sell_list.append({
                        "code":        code,
                        "name":        name,
                        "strategy":    strat_name,
                        "entry_date":  entry_date,
                        "reason":      reason,
                        "quantity":    quantity,
                        "entry_price": entry_price,
                        "close":       close,
                        "gain":        round(gain, 6),
                    })

    # ── S5 전용 청산 ──────────────────────────────────────────────────
    s5_positions = momentum_store.load_positions()
    if not s5_positions:
        logger.info("[매매결정 S5] 보유 포지션 없음")
    else:
        for code, tranches in list(s5_positions.items()):
            for entry_date, pos in list(tranches.items()):
                entry_price = pos.get("entry_price", 0)
                name        = pos.get("name", code)
                quantity    = pos.get("quantity", 0)
                if not entry_price or not quantity:
                    continue

                stock_ma = ma_store.get_stock(code)
                if not stock_ma:
                    logger.warning(f"[매매결정 S5] [{code}] {name} — MA데이터 없음, 건너뜀")
                    continue

                close = int(stock_ma.get("close", 0))
                if close <= 0:
                    continue

                momentum_store.update_position_peak(code, entry_date, close, today_str)
                pos  = momentum_store.load_positions().get(code, {}).get(entry_date, {})
                gain = (close - entry_price) / entry_price

                reason = None

                if gain <= -STOP_LOSS:
                    reason = f"손절(-{STOP_LOSS:.0%})"
                    logger.warning(
                        f"[S5 손절] [{code}] {name}  "
                        f"매수:{entry_price:,} → 마감:{close:,}  {gain:+.2%}"
                    )
                elif close < stock_ma.get("ma5", 0):
                    reason = "MA5 하회"
                    logger.info(
                        f"[S5 MA5하회] [{code}] {name}  "
                        f"종가:{close:,} < MA5:{stock_ma.get('ma5',0):,}  {gain:+.2%}"
                    )
                else:
                    try:
                        holding_days = (
                            datetime.strptime(today_str, "%Y-%m-%d")
                            - datetime.strptime(entry_date, "%Y-%m-%d")
                        ).days
                    except (ValueError, KeyError):
                        holding_days = 0
                    if holding_days >= 21:
                        reason = f"시간스탑({holding_days}일)"
                        logger.info(
                            f"[S5 시간스탑] [{code}] {name}  "
                            f"보유:{holding_days}일  {gain:+.2%}"
                        )
                    else:
                        logger.info(
                            f"[S5 보유중] [{code}] {name}  [{entry_date}]  "
                            f"마감:{close:,}  {gain:+.2%}  보유:{holding_days}일"
                        )

                if reason:
                    sell_list.append({
                        "code":        code,
                        "name":        name,
                        "strategy":    "S5",
                        "entry_date":  entry_date,
                        "reason":      reason,
                        "quantity":    quantity,
                        "entry_price": entry_price,
                        "close":       close,
                        "gain":        round(gain, 6),
                    })

    return sell_list


def _collect_entries(today_str: str) -> list:
    """S4→S5→S3→S2 우선순위로 매수 후보 취합 (슬롯 수 제한 없음)

    - 같은 전략 당일 중복만 차단 (이미 오늘 해당 전략으로 매수한 종목)
    - 교차 전략 허용: S2 보유 중인 종목을 S5로 매수 가능
    - per_slot_budget은 호출측에서 가용현금/후보수로 계산해 주입
    """
    s2_today = {c for c, t in ma_store.get_positions().items() if today_str in t}
    s3_today = {c for c, t in canslim_store.load_positions().items() if today_str in t}
    s4_today = {c for c, t in sepa_store.load_positions().items() if today_str in t}
    s5_today = {c for c, t in momentum_store.load_positions().items() if today_str in t}

    all_candidates: list = []

    for e in sepa_store.get_entry_pending():
        if e.get("date") != today_str or e["code"] in s4_today:
            continue
        all_candidates.append({**e, "strategy": "S4", "_sort_key": e.get("rs_score", 0)})

    for e in momentum_store.get_entry_pending():
        if e.get("date") != today_str or e["code"] in s5_today:
            continue
        all_candidates.append({**e, "strategy": "S5", "_sort_key": e.get("consec_days", 0)})

    for e in canslim_store.get_entry_pending():
        if e.get("date") != today_str or e["code"] in s3_today:
            continue
        all_candidates.append({**e, "strategy": "S3", "_sort_key": e.get("score", 0)})

    for e in ma_store.get_entry_pending():
        if e.get("date") != today_str or e["code"] in s2_today:
            continue
        all_candidates.append({
            **e, "strategy": "S2",
            "_sort_key": e.get("candle_body_ratio", 0),
        })

    prio = {"S4": 3, "S5": 2, "S3": 1, "S2": 0}
    all_candidates.sort(
        key=lambda c: (prio.get(c["strategy"], 0), c.get("_sort_key", 0)),
        reverse=True,
    )

    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in all_candidates]


def run_decision(account, notifier: Notifier = None) -> None:
    from data.holidays import is_market_holiday

    today = datetime.now(KST).strftime("%Y-%m-%d")
    if is_market_holiday():
        logger.info(f"[매매결정] {today} 휴장일 — 실행 건너뜀")
        return

    logger.info(f"[매매결정] 시작 {datetime.now(KST).strftime('%Y-%m-%d %H:%M')}")

    try:
        bal = account.get_balance()
    except Exception as e:
        logger.error(f"[매매결정] 잔고 조회 실패: {e}")
        if notifier:
            notifier.notify(f"[매매결정] 잔고 조회 실패로 중단\n{e}")
        return

    _sync_positions_from_balance(bal.positions, today)

    base_cap     = ma_store.get_base_capital()
    extra        = ma_store.extra_slots(base_cap, bal.total_eval) if base_cap else 0
    max_shared   = S2_S3_S4_BASE + extra
    s2_n, s3_n, s4_n, s5_n, manual_n = count_shared()
    total_shared = s2_n + s3_n + s4_n + s5_n + manual_n
    slots_free   = max_shared - total_shared

    logger.info(
        f"[매매결정] 총자산:{bal.total_eval:,}원  현금:{bal.cash:,}원  "
        f"슬롯:{total_shared}/{max_shared}"
        f"(S2:{s2_n} S3:{s3_n} S4:{s4_n} S5:{s5_n} 수동:{manual_n})  "
        f"여유:{slots_free}"
    )

    sell_list = _decide_exits(today)

    try:
        avail_cash = account.get_available_cash()
    except Exception as e:
        logger.warning(f"[매매결정] 주문가능금액 조회 실패, 예수금 사용: {e}")
        avail_cash = bal.cash

    raw_candidates = _collect_entries(today)
    buy_list: list = []
    if raw_candidates:
        per_budget   = MAX_BUY_AMOUNT  # 종목당 고정 400만원
        total_needed = per_budget * len(raw_candidates)
        need_funding = max(0, total_needed - avail_cash)

        if avail_cash < per_budget:
            logger.info(f"[매매결정] 주문가능금액 부족 ({avail_cash:,}원 < {per_budget:,}원) — 매수 건너뜀")
        else:
            max_buyable = int(avail_cash / per_budget)
            if max_buyable < len(raw_candidates):
                raw_candidates = raw_candidates[:max_buyable]
                need_funding   = total_needed - avail_cash
                logger.info(
                    f"[매매결정] 현금 부족으로 상위 {max_buyable}종목으로 축소  "
                    f"필요:{total_needed:,}원  주문가능:{avail_cash:,}원"
                )
            buy_list = [{**c, "per_slot_budget": per_budget} for c in raw_candidates]
            logger.info(
                f"[매매결정] 매수 후보 {len(buy_list)}종목  "
                f"1종목당:{per_budget:,}원  합계:{per_budget * len(buy_list):,}원  주문가능:{avail_cash:,}원"
            )
    else:
        need_funding = 0
        logger.info("[매매결정] 매수 후보 없음")

    queue = {
        "date":       today,
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "sell":       sell_list,
        "buy":        buy_list,
    }
    save_queue(queue)
    git_commit_push(
        [str(QUEUE_PATH)],
        f"chore: 매매결정 {today}  매도:{len(sell_list)}  매수:{len(buy_list)}",
    )

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    lines   = [f"[매매결정] {now_str}"]
    lines  += [f"총자산:{bal.total_eval:,}원  주문가능:{avail_cash:,}원"]
    lines  += [f"슬롯:{total_shared}/{max_shared}(S2:{s2_n} S3:{s3_n} S4:{s4_n} S5:{s5_n} 수동:{manual_n})"]

    if sell_list:
        lines.append(f"\n내일 09:00 매도 ({len(sell_list)}종목):")
        for s in sell_list:
            lines.append(
                f"  [{s['code']}] {s['name']} [{s['strategy']}]  "
                f"{s['reason']}  {s['gain']:+.2%}"
            )
    else:
        lines.append("매도 예정 없음")

    if buy_list:
        lines.append(f"\n내일 09:00 매수 ({len(buy_list)}종목):")
        for b in buy_list:
            lines.append(
                f"  [{b['code']}] {b['name']} [{b['strategy']}]  "
                f"예산:{b['per_slot_budget']:,}원"
            )
        if need_funding > 0:
            lines.append(f"\n⚠️ 매수예산 부족: {need_funding:,}원 추가 입금 필요")
    else:
        lines.append("매수 예정 없음")

    logger.info("\n".join(lines))
    if notifier:
        notifier.notify("\n".join(lines))

    logger.info("[매매결정] 완료")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    notifier = Notifier.from_settings(settings)
    logger.info(
        f"=== 통합 매매결정 배치 [{'모의' if settings.kis_is_paper_trading else '실전'}투자] ==="
    )
    try:
        kis = KIS(settings)
        run_decision(account=kis.account, notifier=notifier)
    except Exception as _e:
        logger.exception(f"[매매결정] 예외 발생: {_e}")
        notifier.notify(f"[매매결정] 비정상 종료\n오류: {_e}")
        sys.exit(1)
