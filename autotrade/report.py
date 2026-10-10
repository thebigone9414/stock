"""autotrade/report.py — v2 텔레그램 보고 포맷터

지시서 v2 §8 양식:

  [자동매매 10/13 20:20]  코스피200 1,0xx.xx (+x.xx%)  국면: 하락 (62일선 ...)

  ■ A ETF 11패턴  배분 25,000,000원
    보유  #7 3일하락  09/14 신호, 10/13 매수 500만원  손절선 945.75  평가 +x.x%
    내일 매도  없음
    내일 매수  없음

  ■ B 월말 모멘텀  배분 10,000,000원   다음 교체 10/30
    보유  없음
    내일 매도  없음
    순위 참고  1 주성엔지니어링 +316% ...
"""
from __future__ import annotations

from typing import Any, Optional

from autotrade import common, config as cfg


def _fmt_won(amount: int | float, short: bool = False) -> str:
    amount = int(amount)
    if short and abs(amount) >= 10000:
        return f"{amount // 10000:,}만원"
    return f"{amount:,}원"


def _fmt_pct(ratio: float) -> str:
    return f"{ratio*100:+.2f}%"


def _fmt_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        d = date_str.split(" ")[0] if " " in date_str else date_str
        parts = d.split("-")
        if len(parts) == 3:
            return f"{parts[1]}/{parts[2]}"
    except Exception:
        pass
    return date_str


def format_daily_report(orders: dict) -> str:
    lines: list[str] = []
    now = common.now_kst().strftime("%m/%d %H:%M")
    k200 = orders.get("k200", {})
    reg = orders.get("regime_detail", {})

    close = k200.get("close", 0)
    chg = k200.get("chg", 0)
    regime = "상승" if orders.get("regime_on") else "하락"
    lines.append(
        f"[자동매매 {now}]  코스피200 {close:,.2f} ({chg*100:+.2f}%)  "
        f"국면: {regime} (62일선 {reg.get('ma62', 0):,.1f} / 20일 전 {reg.get('ma62_20ago', 0):,.1f})"
    )
    lines.append("")

    # ── A ETF ──────────────────────────────────────────────────
    a_sells = orders.get("sell", {}).get("A", [])
    a_buys  = orders.get("buy", {}).get("A", [])
    a_cap   = orders.get("modules", {}).get("A", {}).get("capital", 0)
    lines.append(f"■ A ETF 11패턴  배분 {_fmt_won(a_cap)}")

    # 보유 요약 (매도 대상 라인 포함)
    if a_sells:
        lines.append(f"  내일 매도 ({len(a_sells)}건):")
        for s in a_sells:
            lines.append(
                f"    [{s['code']}] {s['name']} #{s['pat']}  {s['reason']}  "
                f"{s['quantity']}주  진입 {_fmt_date(s['entry_date'])} ({s['entry_px']:,.2f})  "
                f"평가 {_fmt_pct(s.get('pnl_pct', 0))}"
            )
    else:
        lines.append("  내일 매도  없음")

    if a_buys:
        lines.append(f"  내일 매수 ({len(a_buys)}건):")
        for b in a_buys:
            lines.append(
                f"    [{b['code']}] {b['name']} #{b['pat']} {b['pattern_name']}  "
                f"{_fmt_won(b['amount'])}  손절 {b['stop_level']:,.2f}  "
                f"신호일 {_fmt_date(b.get('signal_date'))} ({b.get('entry_px', 0):,.2f})"
            )
    else:
        near = orders.get("A_near", [])
        if near:
            misses = ", ".join(f"#{n['pat']} ({len(n.get('missing', []))}개)" for n in near[:3])
            lines.append(f"  내일 매수  없음 (근접 {len(near)}: {misses})")
        else:
            lines.append("  내일 매수  없음")
    lines.append("")

    # ── B 월말 모멘텀 ───────────────────────────────────────────
    b_sells = orders.get("sell", {}).get("B", [])
    b_buys  = orders.get("buy", {}).get("B", [])
    b_cap   = orders.get("modules", {}).get("B", {}).get("capital", 0)
    is_rebal = orders.get("is_rebalance", False)
    rebal_tag = "  🔁 내일 월말 교체" if is_rebal else ""
    lines.append(f"■ B 월말 모멘텀  배분 {_fmt_won(b_cap)}{rebal_tag}")

    if b_sells:
        lines.append(f"  내일 매도 ({len(b_sells)}건):")
        for s in b_sells:
            lines.append(
                f"    [{s['code']}] {s['name']}  {s['reason']}  "
                f"{s['quantity']}주  매수 {_fmt_date(s.get('entry_date'))} ({s.get('entry_px', 0):,.0f})  "
                f"평가 {_fmt_pct(s.get('pnl_pct', 0))}"
            )
    else:
        lines.append("  내일 매도  없음")

    if b_buys:
        lines.append(f"  내일 매수 ({len(b_buys)}건, 월말 교체):")
        for b in b_buys:
            skip = f"  [{b['skip_note']}]" if b.get("skip_note") else ""
            lines.append(
                f"    [{b['code']}] {b['name']}  6M {b['r120']*100:+.1f}%  "
                f"{b['qty']}주 @ {b['close']:,.0f} ({_fmt_won(b['amount'])}){skip}"
            )
    else:
        # 순위 참고 (매수 없을 때)
        rank = orders.get("B_rank_top", [])
        if rank:
            cand_txt = " / ".join(f"{i+1} {r['name']} {r['r120']*100:+.0f}%" for i, r in enumerate(rank[:5]))
            reason = "국면 하락" if not orders.get("regime_on") else "평일 (월말 아님)"
            lines.append(f"  내일 매수  없음 ({reason})   순위 참고: {cand_txt}")
        else:
            lines.append("  내일 매수  없음")
    lines.append("")

    # ── 입금 안내 (CAPITAL 고정 모드) ───────────────────────────
    if orders.get("capital_mode") == "fixed":
        total_needed = sum(b.get("amount", 0) for b in a_buys) + sum(b.get("amount", 0) for b in b_buys)
        if total_needed > 0:
            lines.append(f"■ 내일 매수 필요 입금  총 {_fmt_won(total_needed)}")
            if a_buys:
                lines.append(f"    A ETF  {_fmt_won(sum(b['amount'] for b in a_buys))}")
            if b_buys:
                lines.append(f"    B 종목 {_fmt_won(sum(b['amount'] for b in b_buys))}")
            lines.append("")

    # ── 경고 ────────────────────────────────────────────────────
    warnings = _collect_warnings(orders)
    if warnings:
        lines.append("■ 경고")
        for w in warnings:
            lines.append(f"  {w}")
    else:
        lines.append("■ 경고  없음")

    return "\n".join(lines)


def _collect_warnings(orders: dict) -> list[str]:
    out = []
    if common.is_dry_run():
        out.append("DRY-RUN 모드 활성화 — 실제 주문 안 됨")
    if common.is_stopped("A"):
        out.append("STOP_A 파일 존재 — A 모듈 매수 차단")
    if common.is_stopped("B"):
        out.append("STOP_B 파일 존재 — B 모듈 매수 차단")
    return out


# ── 이벤트 알림 ────────────────────────────────────────────────
def format_order_filled(side: str, module: str, code: str, name: str, quantity: int, price: int, reason: str = "") -> str:
    side_kr = "매수" if side == "buy" else "매도"
    mod_kr = f"[{module}]"
    amount = quantity * price
    msg = f"{mod_kr} {side_kr} 체결  [{code}] {name}  {quantity}주 @ {price:,}원 ({_fmt_won(amount)})"
    if reason:
        msg += f"  사유: {reason}"
    return msg


def format_order_failed(side: str, module: str, code: str, name: str, quantity: int, error: str) -> str:
    side_kr = "매수" if side == "buy" else "매도"
    mod_kr = f"[{module}]"
    return f"{mod_kr} {side_kr} 실패  [{code}] {name}  {quantity}주  오류: {error[:150]}"


def format_reconcile_mismatch(diffs: list[str]) -> str:
    body = "\n".join(f"  {d}" for d in diffs)
    return f"[잔고 대사 불일치] 자동매매 보류\n{body}"


def format_data_error(context: str, error: str) -> str:
    return f"[데이터 오류] {context}\n  {error[:300]}"
