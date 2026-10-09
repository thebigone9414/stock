"""
S2 (MA Cross / Golden Alignment) Strategy Backtest
Uses historical OHLCV data from ohlcv_cache.json
"""
import sys
import json
import math
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, '/home/user/stock')
from data.holidays import is_market_holiday

# ── Constants ────────────────────────────────────────────────────────────────
BUDGET_PER_STOCK   = 3_000_000   # KRW per position
MAX_POSITIONS      = 10
STOP_LOSS          = -0.07
TAKE_PROFIT        = 0.20
TAKE_PROFIT_EXT    = 0.25        # extended if early_gain_triggered
RUNNER_THRESHOLD   = 0.20        # peak gain >= 20% → runner mode
TRAIL_STOP_PCT     = 0.10        # trailing stop: peak * (1 - 0.10)
EARLY_GAIN_PCT     = 0.15        # +15% within 21 calendar days → early gain
EARLY_GAIN_DAYS    = 21          # calendar days

# MA periods
MA_PERIODS = [5, 21, 62, 248, 744]

# ── Step 1: Build trading-day calendar ───────────────────────────────────────
def build_trading_days(end_date: date, count: int) -> list:
    """Return list of `count` trading days ending on end_date (inclusive), oldest first."""
    days = []
    d = end_date
    while len(days) < count:
        if not is_market_holiday(d):
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return days

GLOBAL_END = date(2026, 6, 12)
# We need enough extra trading days to cover MA-744 lookback across all stocks
# Max data length is 820, so generate 820 + some buffer
ALL_TRADING_DAYS = build_trading_days(GLOBAL_END, 900)

def get_trading_days_for_stock(last_date_str: str, n_closes: int) -> list:
    """Return the n_closes trading days ending on last_date for this stock."""
    last_date = date.fromisoformat(last_date_str)
    return build_trading_days(last_date, n_closes)

# ── Step 2: Load data ─────────────────────────────────────────────────────────
print("Loading OHLCV cache...")
with open('/home/user/stock/data/ohlcv_cache.json') as f:
    raw_cache = json.load(f)

print(f"Loaded {len(raw_cache)} stocks.")

# ── Step 3: Pre-compute MA series per stock ───────────────────────────────────
def compute_mas(closes: list, period: int) -> list:
    """Return MA series (same length as closes), None where not enough data."""
    result = []
    for i in range(len(closes)):
        if i + 1 < period:
            result.append(None)
        else:
            result.append(sum(closes[i+1-period:i+1]) / period)
    return result

def prepare_stock(code: str, stock_data: dict):
    """Return structured stock object with dates, prices, MAs."""
    closes  = stock_data['closes']
    opens   = stock_data['opens']
    last_dt = stock_data['last_date']
    n       = len(closes)

    trading_days = get_trading_days_for_stock(last_dt, n)
    if len(trading_days) != n:
        # Truncate to minimum
        m = min(len(trading_days), n)
        trading_days = trading_days[-m:]
        closes = closes[-m:]
        opens  = opens[-m:]

    ma_data = {}
    for p in MA_PERIODS:
        ma_data[p] = compute_mas(closes, p)

    return {
        'code':         code,
        'dates':        trading_days,   # list of date objects
        'closes':       closes,
        'opens':        opens,
        'ma':           ma_data,        # {period: [values]}
    }

print("Pre-computing MA series for all stocks...")
stocks = {}
for code, sd in raw_cache.items():
    stocks[code] = prepare_stock(code, sd)

# ── Step 4: Build date-indexed lookup ─────────────────────────────────────────
# For each stock, map date -> index
print("Building date index...")
date_idx = {}  # code -> {date: int_index}
for code, s in stocks.items():
    date_idx[code] = {d: i for i, d in enumerate(s['dates'])}

# ── Step 5: Collect all unique trading days in the backtest window ────────────
# Use ALL_TRADING_DAYS as the simulation timeline
# Only start after enough data exists (at least 249 trading days from start)
SIM_DAYS = ALL_TRADING_DAYS  # already sorted oldest-first

# ── Step 6: Helper functions ───────────────────────────────────────────────────
def get_val(stock, field, idx):
    """Safely get value from stock arrays at index idx."""
    arr = stock[field]
    if idx < 0 or idx >= len(arr):
        return None
    return arr[idx]

def get_ma(stock, period, idx):
    arr = stock['ma'][period]
    if idx < 0 or idx >= len(arr):
        return None
    return arr[idx]

def is_fully_aligned(stock, idx):
    """MA5 > MA21 > MA62 > MA248 > MA744 all present and aligned."""
    vals = [get_ma(stock, p, idx) for p in MA_PERIODS]
    if any(v is None for v in vals):
        return False
    ma5, ma21, ma62, ma248, ma744 = vals
    return ma5 > ma21 > ma62 > ma248 > ma744

def is_partial_aligned(stock, idx):
    """MA5 > MA21 > MA62 > MA248, MA744 not available."""
    vals = [get_ma(stock, p, idx) for p in [5, 21, 62, 248]]
    if any(v is None for v in vals):
        return False
    ma744 = get_ma(stock, 744, idx)
    if ma744 is not None:
        return False   # has full data, not "partial"
    ma5, ma21, ma62, ma248 = vals
    return ma5 > ma21 > ma62 > ma248

def is_ma62_declining_5d(stock, idx):
    """MA62 has been declining for 5 consecutive days ending at idx."""
    if idx < 4:
        return False
    vals = [get_ma(stock, 62, idx - 4 + i) for i in range(5)]
    if any(v is None for v in vals):
        return False
    return all(vals[i] > vals[i+1] for i in range(4))

# ── Step 7: Backtest simulation ───────────────────────────────────────────────
print("Running backtest simulation...")

# Positions: list of dicts
# Each position: {
#   code, entry_date, entry_price, quantity, peak_price, peak_gain,
#   early_gain_triggered, sell_next_open, sell_reason
# }
open_positions = []   # active positions
closed_trades  = []   # completed trades

# Track which stocks already have a position (to avoid duplicate entry same day)
# and pending buys for next open
pending_buys   = []   # list of (code, signal_date) to buy next open
pending_sells  = []   # list of position dicts to sell next open

max_concurrent = 0

for sim_idx, sim_date in enumerate(SIM_DAYS):

    # ── Execute pending sells (at today's open) ───────────────────────────────
    for pos in pending_sells:
        code = pos['code']
        s    = stocks[code]
        idx  = date_idx[code].get(sim_date)
        if idx is None:
            # No data for this stock on this date — use close of last available
            exit_price = pos['peak_price']  # fallback
        else:
            exit_price = get_val(s, 'opens', idx)
            if exit_price is None or exit_price == 0:
                exit_price = pos['entry_price']

        entry_price = pos['entry_price']
        qty         = pos['quantity']
        gain_pct    = (exit_price - entry_price) / entry_price

        closed_trades.append({
            'code':        code,
            'entry_date':  pos['entry_date'],
            'exit_date':   sim_date,
            'entry_price': entry_price,
            'exit_price':  exit_price,
            'quantity':    qty,
            'strategy':    pos.get('sell_reason', 'unknown'),
            'gain_pct':    gain_pct,
            'pnl':         (exit_price - entry_price) * qty,
        })
        # Remove from open positions
        open_positions = [p for p in open_positions if not (p['code'] == code and p['entry_date'] == pos['entry_date'])]

    pending_sells = []

    # ── Execute pending buys (at today's open) ────────────────────────────────
    new_buys = []
    for (code, signal_date) in pending_buys:
        if len(open_positions) >= MAX_POSITIONS:
            break  # slot full
        # Check no existing open position for this stock
        if any(p['code'] == code for p in open_positions):
            continue  # already have position
        s   = stocks[code]
        idx = date_idx[code].get(sim_date)
        if idx is None:
            continue
        open_price = get_val(s, 'opens', idx)
        if open_price is None or open_price == 0:
            continue
        qty = math.floor(BUDGET_PER_STOCK / open_price)
        if qty == 0:
            continue

        open_positions.append({
            'code':                code,
            'entry_date':          sim_date,
            'entry_price':         open_price,
            'quantity':            qty,
            'peak_price':          open_price,
            'early_gain_triggered': False,
            'sell_reason':         None,
        })
        new_buys.append(code)

    pending_buys = []

    max_concurrent = max(max_concurrent, len(open_positions))

    # ── Check each open position for exit signals (using today's close) ───────
    codes_with_position = {p['code'] for p in open_positions}
    sell_codes = set()

    for pos in open_positions:
        code = pos['code']
        s    = stocks[code]
        idx  = date_idx[code].get(sim_date)
        if idx is None:
            continue   # no data today, hold

        close = get_val(s, 'closes', idx)
        if close is None:
            continue

        entry_price = pos['entry_price']
        gain        = (close - entry_price) / entry_price

        # Update peak
        if close > pos['peak_price']:
            pos['peak_price'] = close
        peak_gain = (pos['peak_price'] - entry_price) / entry_price

        # Early gain trigger: +15% within 21 calendar days from entry
        if not pos['early_gain_triggered']:
            days_held = (sim_date - pos['entry_date']).days
            if days_held <= EARLY_GAIN_DAYS and gain >= EARLY_GAIN_PCT:
                pos['early_gain_triggered'] = True

        # Determine take profit threshold
        tp_threshold = TAKE_PROFIT_EXT if pos['early_gain_triggered'] else TAKE_PROFIT

        # ── Exit checks ───────────────────────────────────────────────────────
        reason = None

        # 1. Stop loss: gain <= -7%
        if gain <= STOP_LOSS:
            reason = 'stop_loss'

        # 2. Runner mode: peak_gain >= +20% AND MA21 < MA62 AND MA62 declining 5d
        elif peak_gain >= RUNNER_THRESHOLD:
            ma21 = get_ma(s, 21, idx)
            ma62 = get_ma(s, 62, idx)
            if ma21 is not None and ma62 is not None:
                if ma21 < ma62 and is_ma62_declining_5d(s, idx):
                    reason = 'runner_ma_exit'

        # 3. Trailing stop: peak_gain >= +10% AND current_close < peak * 0.90
        if reason is None and peak_gain >= 0.10:
            if close < pos['peak_price'] * (1 - TRAIL_STOP_PCT):
                reason = 'trailing_stop'

        # 4. Take profit: gain >= threshold
        if reason is None and gain >= tp_threshold:
            reason = 'take_profit'

        if reason:
            pos['sell_reason'] = reason
            pending_sells.append(pos)
            sell_codes.add(code)

    # ── Generate entry signals for next day ───────────────────────────────────
    # Only if slots available (after today's sells are counted, but sells execute next day)
    current_slots = len(open_positions)  # sells happen next open
    available_slots = MAX_POSITIONS - current_slots + len(sell_codes)  # rough estimate

    entered_today = set()

    for code, s in stocks.items():
        if available_slots <= len(entered_today):
            break
        if code in codes_with_position:
            continue  # already have position; skip (strategy: 1 position per stock)

        idx = date_idx[code].get(sim_date)
        if idx is None or idx < 1:
            continue

        prev_idx = idx - 1

        # Check full alignment: MA5>MA21>MA62>MA248>MA744 cross moment
        ma744_today    = get_ma(s, 744, idx)
        ma744_prev     = get_ma(s, 744, prev_idx)

        if ma744_today is not None:
            # Full alignment check
            full_now  = is_fully_aligned(s, idx)
            full_prev = is_fully_aligned(s, prev_idx)
            if full_now and not full_prev:
                entered_today.add(code)
                pending_buys.append((code, sim_date))
        else:
            # Partial alignment check (MA5>MA21>MA62>MA248, no MA744)
            part_now  = is_partial_aligned(s, idx)
            part_prev = is_partial_aligned(s, prev_idx)
            if part_now and not part_prev:
                entered_today.add(code)
                pending_buys.append((code, sim_date))

# ── Final: close any remaining open positions at last available close ──────────
for pos in open_positions:
    code = pos['code']
    s    = stocks[code]
    # Find last available close
    last_idx   = len(s['closes']) - 1
    exit_price = s['closes'][last_idx]
    exit_date  = s['dates'][last_idx]

    entry_price = pos['entry_price']
    qty         = pos['quantity']
    gain_pct    = (exit_price - entry_price) / entry_price

    closed_trades.append({
        'code':        code,
        'entry_date':  pos['entry_date'],
        'exit_date':   exit_date,
        'entry_price': entry_price,
        'exit_price':  exit_price,
        'quantity':    qty,
        'strategy':    'end_of_backtest',
        'gain_pct':    gain_pct,
        'pnl':         (exit_price - entry_price) * qty,
    })

# ── Step 8: Analytics ─────────────────────────────────────────────────────────
print("\n" + "="*70)
print("  S2 (MA Cross / Golden Alignment) Backtest Results")
print("="*70)

total_trades = len(closed_trades)
if total_trades == 0:
    print("No trades were generated.")
    sys.exit(0)

wins   = [t for t in closed_trades if t['gain_pct'] > 0]
losses = [t for t in closed_trades if t['gain_pct'] <= 0]

win_rate      = len(wins) / total_trades * 100
avg_win       = sum(t['gain_pct'] for t in wins) / len(wins) * 100   if wins   else 0
avg_loss      = sum(t['gain_pct'] for t in losses) / len(losses) * 100 if losses else 0
gross_profit  = sum(t['pnl'] for t in wins)
gross_loss    = abs(sum(t['pnl'] for t in losses)) if losses else 0
profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
total_pnl     = sum(t['pnl'] for t in closed_trades)

print(f"\n{'─'*70}")
print(f"  TRADE SUMMARY")
print(f"{'─'*70}")
print(f"  Total trades        : {total_trades}")
print(f"  Wins                : {len(wins)}  ({win_rate:.1f}%)")
print(f"  Losses              : {len(losses)}  ({100-win_rate:.1f}%)")
print(f"  Avg gain on wins    : {avg_win:+.2f}%")
print(f"  Avg loss on losses  : {avg_loss:+.2f}%")
print(f"  Profit factor       : {profit_factor:.2f}")
print(f"  Max concurrent pos  : {max_concurrent}")

print(f"\n{'─'*70}")
print(f"  PnL SUMMARY")
print(f"{'─'*70}")
print(f"  Total PnL           : {total_pnl:>+14,.0f} KRW")
print(f"  Gross profit        : {gross_profit:>+14,.0f} KRW")
print(f"  Gross loss          : {-gross_loss:>+14,.0f} KRW")

# Annualized return
STARTING_CAPITAL = 30_000_000
# Backtest period: from first trade to last trade
if closed_trades:
    first_date = min(t['entry_date'] for t in closed_trades)
    last_date  = max(t['exit_date']  for t in closed_trades)
    calendar_days = (last_date - first_date).days
    if calendar_days > 0:
        total_return = total_pnl / STARTING_CAPITAL
        annual_return = (1 + total_return) ** (365 / calendar_days) - 1
        print(f"\n  Backtest period     : {first_date} → {last_date} ({calendar_days} calendar days)")
        print(f"  Starting capital    : {STARTING_CAPITAL:>14,.0f} KRW")
        print(f"  Total return        : {total_return*100:>+.2f}%")
        print(f"  Annualized return   : {annual_return*100:>+.2f}%")

# Exit reason breakdown
reason_counts = defaultdict(int)
for t in closed_trades:
    reason_counts[t['strategy']] += 1

print(f"\n{'─'*70}")
print(f"  EXIT REASON BREAKDOWN")
print(f"{'─'*70}")
for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
    print(f"  {reason:<25}: {cnt} trades  ({cnt/total_trades*100:.1f}%)")

# Monthly PnL
print(f"\n{'─'*70}")
print(f"  MONTHLY PnL SUMMARY")
print(f"{'─'*70}")
monthly_pnl = defaultdict(float)
monthly_cnt = defaultdict(int)
for t in closed_trades:
    key = t['exit_date'].strftime('%Y-%m')
    monthly_pnl[key] += t['pnl']
    monthly_cnt[key] += 1

print(f"  {'Month':<10}  {'PnL (KRW)':>16}  {'Trades':>7}")
print(f"  {'─'*10}  {'─'*16}  {'─'*7}")
for month in sorted(monthly_pnl.keys()):
    pnl = monthly_pnl[month]
    cnt = monthly_cnt[month]
    sign = '+' if pnl >= 0 else ''
    print(f"  {month:<10}  {sign}{pnl:>15,.0f}  {cnt:>7}")

print(f"  {'─'*10}  {'─'*16}  {'─'*7}")
total_monthly = sum(monthly_pnl.values())
sign = '+' if total_monthly >= 0 else ''
print(f"  {'TOTAL':<10}  {sign}{total_monthly:>15,.0f}  {total_trades:>7}")

# Top 10 best trades
print(f"\n{'─'*70}")
print(f"  TOP 10 BEST TRADES")
print(f"{'─'*70}")
print(f"  {'Code':<8}  {'Entry':>10}  {'Exit':>10}  {'Gain%':>8}  {'PnL (KRW)':>14}  Exit Reason")
print(f"  {'─'*8}  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*14}  {'─'*15}")
for t in sorted(closed_trades, key=lambda x: -x['gain_pct'])[:10]:
    print(f"  {t['code']:<8}  {str(t['entry_date']):>10}  {str(t['exit_date']):>10}  {t['gain_pct']*100:>+7.2f}%  {t['pnl']:>+14,.0f}  {t['strategy']}")

# Top 10 worst trades
print(f"\n{'─'*70}")
print(f"  TOP 10 WORST TRADES")
print(f"{'─'*70}")
print(f"  {'Code':<8}  {'Entry':>10}  {'Exit':>10}  {'Gain%':>8}  {'PnL (KRW)':>14}  Exit Reason")
print(f"  {'─'*8}  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*14}  {'─'*15}")
for t in sorted(closed_trades, key=lambda x: x['gain_pct'])[:10]:
    print(f"  {t['code']:<8}  {str(t['entry_date']):>10}  {str(t['exit_date']):>10}  {t['gain_pct']*100:>+7.2f}%  {t['pnl']:>+14,.0f}  {t['strategy']}")

print(f"\n{'='*70}")
print("  End of Backtest")
print(f"{'='*70}\n")
