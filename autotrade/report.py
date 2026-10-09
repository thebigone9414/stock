"""autotrade/report.py — 텔레그램 보고 포맷터

지시서 §7 양식:

  [자동매매 10/09 20:20]  코스피200 1,044.52 (-2.71%)  국면: 하락 (62일선 ...)

  ■ ETF 규칙 (KODEX 레버리지)  평가 31,4xx,xxx원 (현금 2x,xxx,xxx)
    보유  #7 3일하락  09/15 매수 628만원  손절선 945.75  평가 -0.6%  → 보유
    내일 매도  없음
    내일 매수  없음 (신호 없음)

  ■ 종목 규칙  평가 13,4xx,xxx원 (현금 13,4xx,xxx)
    보유  없음
    내일 매도  없음
    내일 매수  없음 (국면 하락)   참고 후보 7: 피에스케이...

  ■ 합계 평가 44,8xx,xxx원  당일 손익 ...원  MTD ...  YTD ...
  ■ 경고  없음

이벤트 알림 (즉시):
  format_order_filled      — 매수/매도 체결 성공
  format_order_failed      — 주문 실패
  format_reconcile_mismatch — 잔고 대사 불일치
  format_stop_hit          — 손절 체결
  format_data_error        — 데이터 수집 실패
"""
from __future__ import annotations

from typing import Any, Optional

from autotrade import common, config as cfg


# ── 공통 유틸 ─────────────────────────────────────────────────
def _fmt_won(amount: int | float, short: bool = False) -> str:
    """금액 포매팅. short=True면 '628만원', False면 '6,280,000원'."""
    amount = int(amount)
    if short:
        if abs(amount) >= 10000:
            return f"{amount // 10000:,}만원"
        return f"{amount:,}원"
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


# ── 20:10 일일 보고 ─────────────────────────────────────────────
def format_daily_report(orders: dict) -> str:
    """judge.run() 반환 dict 를 받아 텔레그램 전체 보고 문자열 생성."""
    lines: list[str] = []
    now = common.now_kst().strftime("%m/%d %H:%M")
    k200 = orders.get("k200", {})
    reg = orders.get("regime_detail", {})

    # 헤더
    close = k200.get("close", 0)
    chg = k200.get("chg", 0)
    regime = "상승" if orders.get("regime_on") else "하락"
    lines.append(
        f"[자동매매 {now}]  코스피200 {close:,.2f} ({chg*100:+.2f}%)  "
        f"국면: {regime} (62일선 {reg.get('ma62', 0):,.1f} / 20일 전 {reg.get('ma62_20ago', 0):,.1f})"
    )
    lines.append("")

    # ── ETF ────────────────────────────────────────────────────
    etf_sells = orders.get("sell", {}).get("etf", [])
    etf_buys = orders.get("buy", {}).get("etf", [])
    etf_dep = orders.get("etf_dep", 0)
    lines.append(f"■ ETF 규칙 (KODEX 레버리지/인버스)  배분 {_fmt_won(etf_dep)}")

    # 보유 (sells 리스트는 매도 대상, orders에 전체 포지션은 등 — 매도 대상이 아닌 보유는 etf_result에서)
    etf_pos_count = _count_etf_holdings(orders)
    if etf_pos_count == 0:
        lines.append("  보유  없음")
    # (상세 보유 라인은 매도·매수 리스트와 함께 표시 — 아래)

    if etf_sells:
        lines.append(f"  내일 매도 ({len(etf_sells)}건):")
        for s in etf_sells:
            lines.append(
                f"    [{s['code']}] {s['name']} #{s['pat']}  {s['reason']}  "
                f"{s['quantity']}주  진입 {_fmt_date(s['entry_date'])} ({s['entry_px']:,.2f})"
            )
    else:
        lines.append("  내일 매도  없음")

    if etf_buys:
        lines.append(f"  내일 매수 ({len(etf_buys)}건):")
        for b in etf_buys:
            lines.append(
                f"    [{b['code']}] {b['name']} #{b['pat']} {b['pattern_name']}"
            )
            lines.append(
                f"      금액 {_fmt_won(b['amount'])}  손절선 {b['stop_level']:,.2f}  "
                f"신호일 {_fmt_date(b['signal_date'])} ({b['entry_px']:,.2f})"
            )
    else:
        near = orders.get("etf_near", [])
        if near:
            misses = ", ".join(f"#{n['pat']} ({len(n['missing'])}개 미달)" for n in near[:3])
            lines.append(f"  내일 매수  없음 (근접 {len(near)}건: {misses})")
        else:
            lines.append("  내일 매수  없음 (신호 없음)")
    lines.append("")

    # ── 종목 ────────────────────────────────────────────────────
    stk_sells = orders.get("sell", {}).get("stock", [])
    stk_buys = orders.get("buy", {}).get("stock", [])
    stock_dep = orders.get("stock_dep", 0)
    lines.append(f"■ 종목 규칙  배분 {_fmt_won(stock_dep)}")

    if stk_sells:
        lines.append(f"  내일 매도 ({len(stk_sells)}건):")
        for s in stk_sells:
            lines.append(
                f"    [{s['code']}] {s['name']}  {s['reason']}  "
                f"매수 {_fmt_date(s['entry_date'])} ({s['entry_px']:,.0f})  "
                f"평가 {_fmt_pct(s['pnl_pct'])}"
            )
    else:
        lines.append("  내일 매도  없음")

    if stk_buys:
        lines.append(f"  내일 매수 ({len(stk_buys)}건):")
        for b in stk_buys:
            lines.append(
                f"    [{b['code']}] {b['name']} [{b['card']}]  "
                f"{b['qty']}주 @ {b['close']:,.0f} ({_fmt_won(b['amount'])})  "
                f"손절 {b['stop_level']:,.0f} ({b['stop_pct']*100:.1f}%)"
            )
    else:
        cand = orders.get("stock_candidates", [])
        if cand:
            cand_txt = " / ".join(f"{c['name']}[{c['card']}]" for c in cand[:5])
            reason = "국면 하락" if not orders.get("regime_on") else "자리 없음"
            lines.append(f"  내일 매수  없음 ({reason})   참고 후보 {len(cand)}: {cand_txt}")
        else:
            lines.append("  내일 매수  없음 (신호 없음)")
    lines.append("")

    # ── 입금 안내 (CAPITAL 고정 모드) ───────────────────────────
    if orders.get("capital_mode") == "fixed":
        total_needed = sum(b.get("amount", 0) for b in etf_buys) + sum(b.get("amount", 0) for b in stk_buys)
        if total_needed > 0:
            lines.append(f"■ 내일 매수 필요 입금  총 {_fmt_won(total_needed)}")
            if etf_buys:
                lines.append(f"    ETF   {_fmt_won(sum(b['amount'] for b in etf_buys))}")
            if stk_buys:
                lines.append(f"    종목  {_fmt_won(sum(b['amount'] for b in stk_buys))}")
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


def _count_etf_holdings(orders: dict) -> int:
    """ETF 보유 건수 (매도 대상 포함, judge 시점 전체)."""
    # orders에는 매도 대상만 저장돼 있어서 정확하지 않음.
    # 임시: 매도 리스트 사이즈로 추정 (TODO: positions 전체도 orders에 저장)
    return len(orders.get("sell", {}).get("etf", []))


def _collect_warnings(orders: dict) -> list[str]:
    out = []
    if common.is_dry_run():
        out.append("DRY-RUN 모드 활성화 — 실제 주문 안 됨")
    if common.is_stopped():
        out.append("STOP 파일 존재 — 신규 매수 차단, 매도·손절만 수행")
    return out


# ── 이벤트 알림 ────────────────────────────────────────────────
def format_order_filled(side: str, rule: str, code: str, name: str, quantity: int, price: int, reason: str = "") -> str:
    side_kr = "매수" if side == "buy" else "매도"
    rule_kr = "ETF" if rule == "etf" else "종목"
    amount = quantity * price
    msg = f"[{rule_kr} {side_kr} 체결] [{code}] {name}  {quantity}주 @ {price:,}원 ({_fmt_won(amount)})"
    if reason:
        msg += f"  사유: {reason}"
    return msg


def format_order_failed(side: str, rule: str, code: str, name: str, quantity: int, error: str) -> str:
    side_kr = "매수" if side == "buy" else "매도"
    rule_kr = "ETF" if rule == "etf" else "종목"
    return f"[{rule_kr} {side_kr} 실패] [{code}] {name}  {quantity}주  오류: {error[:150]}"


def format_stop_hit(code: str, name: str, current: int, stop_level: int, quantity: int, mode: str) -> str:
    return (
        f"[손절 발동] [{code}] {name}  현재가 {current:,} ≤ 손절가 {stop_level:,}\n"
        f"  모드: {mode}  수량: {quantity}주  시장가 매도 접수"
    )


def format_reconcile_mismatch(diffs: list[str]) -> str:
    body = "\n".join(f"  {d}" for d in diffs)
    return f"[잔고 대사 불일치] 자동매매 보류\n{body}"


def format_data_error(context: str, error: str) -> str:
    return f"[데이터 오류] {context}\n  {error[:300]}"


def format_dryrun_notice(cmd: str) -> str:
    return f"[DRY-RUN] {cmd} 실행됨 — 실제 주문 안 함"
