"""
=============================================================
SIMULATION ONLY — NO REAL MONEY INVOLVED
=============================================================
NSE Intraday Trading Simulator
Starting capital: ₹1,000 | Target: ₹1,500 | Stop: ₹500
=============================================================
"""

import yfinance as yf
import pandas as pd
import time
import csv
import os
import signal
import sys

# Force UTF-8 output so the Rupee sign (₹) renders correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime, date
import pytz

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
STARTING_CAPITAL   = 1000.0
TARGET_CAPITAL     = 1500.0
STOP_CAPITAL       = 500.0
POLL_INTERVAL      = 300          # seconds between price refreshes (5 min)
MAX_POSITIONS      = 2            # never hold more than this many stocks at once
MAX_INVEST_RATIO   = 0.80         # never invest more than 80% of bankroll in one position
CUTOFF_BUY         = (14, 45)     # no new buys after 2:45 PM IST (hour, minute)
FORCE_SELL         = (15, 15)     # force-close all positions at 3:15 PM IST
WARNING_LOSS_PCT   = 3.0          # warn if unrealised loss on a position exceeds 3%
WARNING_CHARGES_RS = 100.0        # warn if total charges exceed ₹100

WATCHLIST = [
    "IDEA.NS",
    "SUZLON.NS",
    "YESBANK.NS",
    "NHPC.NS",
    "IRFC.NS",
    "BHEL.NS",
    "ETERNAL.NS",
    "PAYTM.NS",
    "PNB.NS",
]

TRADE_LOG_FILE = "trade_log.csv"
IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# GLOBAL STATE
# ─────────────────────────────────────────────
bankroll        = STARTING_CAPITAL
positions       = {}   # symbol -> {qty, avg_price, buy_time}
price_history   = {s: [] for s in WATCHLIST}   # stores recent closing prices for RSI
total_charges   = 0.0
trade_log       = []
last_action     = "WAITING"
poll_count      = 0


# ─────────────────────────────────────────────
# SEED PRICE HISTORY FROM PREVIOUS DAY
# ─────────────────────────────────────────────
def seed_price_history():
    """
    Pre-populates price_history with the previous trading day's 1-min closes.
    This means RSI is computable from the very first poll instead of after
    ~75 minutes of warmup. Uses the last 50 1-min bars from the prior session.
    """
    print("  Seeding price history from previous session data...")
    seeded = 0
    for symbol in WATCHLIST:
        try:
            ticker = yf.Ticker(symbol)
            # Fetch 5 days of 1-min data; yfinance returns up to 7 calendar days
            hist = ticker.history(period="5d", interval="1m")
            if hist.empty:
                print(f"    [WARN] No historical data for {symbol} — RSI will warm up live")
                continue

            today_date = now_ist().date()
            # Keep only bars from before today (previous trading sessions)
            hist.index = hist.index.tz_convert(IST)
            prev_bars = hist[hist.index.date < today_date]

            if prev_bars.empty:
                print(f"    [WARN] No prior-day bars for {symbol} — RSI will warm up live")
                continue

            closes = list(prev_bars["Close"].tail(50))
            price_history[symbol] = [float(c) for c in closes]
            seeded += 1
        except Exception as e:
            print(f"    [WARN] Could not seed {symbol}: {e}")

    print(f"  Seeded {seeded}/{len(WATCHLIST)} symbols — RSI ready from poll #1\n")


# ─────────────────────────────────────────────
# BROKERAGE CHARGE CALCULATION (Zerodha intraday)
# ─────────────────────────────────────────────
def calculate_charges(price: float, qty: int, side: str) -> float:
    """
    side: 'BUY' or 'SELL'
    Returns total charges in ₹ for the given trade.
    """
    turnover   = price * qty
    brokerage  = min(20.0, 0.0003 * turnover)          # ₹20 or 0.03%, whichever is lower
    stt        = 0.00025 * turnover if side == "SELL" else 0.0   # 0.025% on sell-side only
    exchange   = 0.0000345 * turnover                  # 0.00345%
    sebi       = 0.000001  * turnover                  # 0.0001%
    gst        = 0.18 * (brokerage + exchange + sebi)  # 18% on broker + exchange + SEBI
    stamp      = 0.00003   * turnover if side == "BUY" else 0.0  # 0.003% on buy-side only
    total      = brokerage + stt + exchange + sebi + gst + stamp
    return round(total, 4)


# ─────────────────────────────────────────────
# RSI CALCULATION (manual, 14-period)
# ─────────────────────────────────────────────
def compute_rsi(prices: list, period: int = 14) -> float | None:
    """
    Computes RSI from a list of price readings.
    Returns None if insufficient data.
    """
    if len(prices) < period + 1:
        return None

    # Use only the most recent (period+1) prices to get 'period' changes
    recent = prices[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(recent)):
        change = recent[i] - recent[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0  # no losses at all → fully overbought
    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


# ─────────────────────────────────────────────
# FETCH LIVE PRICE VIA YFINANCE
# ─────────────────────────────────────────────
def fetch_price(symbol: str) -> float | None:
    """
    Fetches the latest available price for the given NSE symbol.
    Uses a 1-day, 1-minute interval ticker and takes the most recent close.
    Returns None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="1d", interval="1m")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"  [WARN] Could not fetch {symbol}: {e}")
        return None


# ─────────────────────────────────────────────
# UPPER CIRCUIT APPROXIMATION
# ─────────────────────────────────────────────
def near_upper_circuit(symbol: str, current_price: float) -> bool:
    """
    Approximates the upper circuit as 20% above the previous day close.
    Returns True if we are within 2% of that limit (avoid chasing circuit stocks).
    """
    try:
        ticker    = yf.Ticker(symbol)
        prev_data = ticker.history(period="2d", interval="1d")
        if len(prev_data) < 2:
            return False
        prev_close    = float(prev_data["Close"].iloc[-2])
        upper_circuit = prev_close * 1.20
        return current_price >= upper_circuit * 0.98   # within 2% of circuit
    except Exception:
        return False


# ─────────────────────────────────────────────
# TRADE LOGGING
# ─────────────────────────────────────────────
def log_trade(timestamp, symbol, action, qty, price, charges, bankroll_after):
    """Appends one trade record to the in-memory log and flushes to CSV."""
    record = {
        "timestamp"     : timestamp,
        "symbol"        : symbol,
        "action"        : action,
        "qty"           : qty,
        "price"         : price,
        "charges"       : charges,
        "bankroll_after": bankroll_after,
    }
    trade_log.append(record)

    file_exists = os.path.isfile(TRADE_LOG_FILE)
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=record.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)


# ─────────────────────────────────────────────
# IST TIME HELPERS
# ─────────────────────────────────────────────
def now_ist() -> datetime:
    return datetime.now(IST)

def time_tuple_ist() -> tuple:
    t = now_ist()
    return (t.hour, t.minute)

def is_past_cutoff() -> bool:
    """Returns True if it's too late to open new positions."""
    h, m = time_tuple_ist()
    co_h, co_m = CUTOFF_BUY
    return (h, m) >= (co_h, co_m)

def is_force_sell_time() -> bool:
    """Returns True if it's time to force-close all positions."""
    h, m = time_tuple_ist()
    fs_h, fs_m = FORCE_SELL
    return (h, m) >= (fs_h, fs_m)


# ─────────────────────────────────────────────
# BUY LOGIC
# ─────────────────────────────────────────────
def attempt_buy(symbol: str, current_price: float, prev_price: float, rsi: float):
    """
    BUY signal: price rose > 0.5% from last reading AND RSI < 65
    AND not near upper circuit AND we have room for another position.
    """
    global bankroll, total_charges, last_action

    # Already holding this stock
    if symbol in positions:
        return

    # Position limit check
    if len(positions) >= MAX_POSITIONS:
        return

    # Time check
    if is_past_cutoff():
        return

    # Signal check
    if prev_price is None or prev_price == 0:
        return
    price_change_pct = (current_price - prev_price) / prev_price * 100
    if price_change_pct <= 0.5:
        return
    if rsi is None or rsi >= 65:
        return
    if near_upper_circuit(symbol, current_price):
        return

    # Position sizing: invest up to 80% of bankroll
    invest_amount = bankroll * MAX_INVEST_RATIO
    qty = int(invest_amount // current_price)
    if qty < 1:
        print(f"  [SKIP BUY] {symbol}: not enough bankroll for 1 share @ ₹{current_price:.2f}")
        return

    charges = calculate_charges(current_price, qty, "BUY")
    cost    = current_price * qty + charges

    if cost > bankroll:
        # Reduce qty so we can afford it
        while qty > 0:
            charges = calculate_charges(current_price, qty, "BUY")
            if current_price * qty + charges <= bankroll:
                break
            qty -= 1
        if qty < 1:
            return

    charges = calculate_charges(current_price, qty, "BUY")
    cost    = current_price * qty + charges

    bankroll      -= cost
    total_charges += charges
    positions[symbol] = {
        "qty"       : qty,
        "avg_price" : current_price,
        "buy_time"  : now_ist().isoformat(),
    }

    ts = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    log_trade(ts, symbol, "BUY", qty, current_price, charges, round(bankroll, 2))
    last_action = f"BOUGHT {qty} x {symbol} @ ₹{current_price:.2f} | charges ₹{charges:.2f}"
    print(f"  >>> BUY  {symbol}: {qty} shares @ ₹{current_price:.2f} | RSI={rsi} | +{price_change_pct:.2f}% | charges ₹{charges:.2f}")


# ─────────────────────────────────────────────
# SELL LOGIC
# ─────────────────────────────────────────────
def attempt_sell(symbol: str, current_price: float, rsi: float, force: bool = False):
    """
    SELL if:
      (a) stop-loss: price dropped 1% below buy price
      (b) take-profit: price rose 2% above buy price
      (c) RSI > 75 (overbought)
      (d) force=True (end-of-day close)
    """
    global bankroll, total_charges, last_action

    if symbol not in positions:
        return

    pos       = positions[symbol]
    qty       = pos["qty"]
    buy_price = pos["avg_price"]
    pnl_pct   = (current_price - buy_price) / buy_price * 100

    sell_reason = None
    if force:
        sell_reason = "FORCE-CLOSE (EOD)"
    elif pnl_pct <= -1.0:
        sell_reason = f"STOP-LOSS ({pnl_pct:.2f}%)"
    elif pnl_pct >= 2.0:
        sell_reason = f"TAKE-PROFIT ({pnl_pct:.2f}%)"
    elif rsi is not None and rsi > 75:
        sell_reason = f"RSI OVERBOUGHT ({rsi})"

    if sell_reason is None:
        return

    charges        = calculate_charges(current_price, qty, "SELL")
    proceeds       = current_price * qty - charges
    bankroll      += proceeds
    total_charges += charges

    del positions[symbol]

    ts = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    log_trade(ts, symbol, "SELL", qty, current_price, charges, round(bankroll, 2))
    last_action = f"SOLD {qty} x {symbol} @ ₹{current_price:.2f} [{sell_reason}] | charges ₹{charges:.2f}"
    print(f"  >>> SELL {symbol}: {qty} shares @ ₹{current_price:.2f} | {sell_reason} | charges ₹{charges:.2f}")


# ─────────────────────────────────────────────
# TERMINAL DISPLAY
# ─────────────────────────────────────────────
def print_status(prices: dict):
    """Prints one poll-cycle status report to the terminal."""
    global poll_count
    poll_count += 1

    now_str   = now_ist().strftime("%Y-%m-%d %H:%M:%S IST")
    progress  = (bankroll - STARTING_CAPITAL) / (TARGET_CAPITAL - STARTING_CAPITAL) * 100

    print("\n" + "=" * 62)
    print(f"  POLL #{poll_count} | {now_str}")
    print("=" * 62)
    print(f"  Bankroll      : ₹{bankroll:.2f}")
    print(f"  Total charges : ₹{total_charges:.2f}", end="")

    if total_charges > WARNING_CHARGES_RS:
        print("  ⚠ WARNING: charges > ₹100!", end="")
    print()

    # Progress bar
    bar_len    = 30
    filled     = max(0, min(bar_len, int(bar_len * progress / 100)))
    bar        = "█" * filled + "░" * (bar_len - filled)
    print(f"  Goal progress : [{bar}] {progress:.1f}%  (₹{bankroll:.0f} / ₹{TARGET_CAPITAL:.0f})")
    print(f"  Last action   : {last_action}")

    # Open positions
    if positions:
        print(f"\n  {'Symbol':<12} {'Qty':>5} {'Buy@':>8} {'Now@':>8} {'UnrPnL':>10} {'UnrPnL%':>8}")
        print("  " + "-" * 56)
        for sym, pos in positions.items():
            cp  = prices.get(sym)
            if cp is None:
                cp = pos["avg_price"]
            unr = (cp - pos["avg_price"]) * pos["qty"]
            pct = (cp - pos["avg_price"]) / pos["avg_price"] * 100
            warn = "  ⚠ >3% LOSS!" if pct < -WARNING_LOSS_PCT else ""
            print(f"  {sym:<12} {pos['qty']:>5} {pos['avg_price']:>8.2f} {cp:>8.2f} {unr:>+10.2f} {pct:>+7.2f}%{warn}")
    else:
        print("\n  Open positions: None (cash)")

    print("=" * 62)


# ─────────────────────────────────────────────
# EXIT SUMMARY
# ─────────────────────────────────────────────
def print_summary(reason: str):
    """Prints final trade summary on exit."""
    print("\n" + "=" * 62)
    print(f"  SESSION ENDED: {reason}")
    print("=" * 62)

    wins   = sum(1 for t in trade_log if t["action"] == "SELL" and
                 # find the matching buy to compare prices — simplified: check if bankroll grew
                 True)  # we'll compute properly below
    sells  = [t for t in trade_log if t["action"] == "SELL"]
    buys   = [t for t in trade_log if t["action"] == "BUY"]

    # Match buys and sells by symbol to compute per-trade P&L
    winning_trades = 0
    losing_trades  = 0
    buy_map        = {}  # symbol -> list of buy records
    for t in trade_log:
        if t["action"] == "BUY":
            buy_map.setdefault(t["symbol"], []).append(t)
        elif t["action"] == "SELL":
            matching_buys = buy_map.get(t["symbol"], [])
            if matching_buys:
                buy_rec  = matching_buys.pop(0)
                buy_val  = buy_rec["price"] * buy_rec["qty"]
                sell_val = t["price"] * t["qty"]
                if sell_val > buy_val:
                    winning_trades += 1
                else:
                    losing_trades += 1

    net_pnl = bankroll - STARTING_CAPITAL
    print(f"  Starting capital : ₹{STARTING_CAPITAL:.2f}")
    print(f"  Final bankroll   : ₹{bankroll:.2f}")
    print(f"  Net P&L          : ₹{net_pnl:+.2f}")
    print(f"  Total trades     : {len(trade_log)}")
    print(f"  BUY orders       : {len(buys)}")
    print(f"  SELL orders      : {len(sells)}")
    print(f"  Winning trades   : {winning_trades}")
    print(f"  Losing trades    : {losing_trades}")
    print(f"  Total charges    : ₹{total_charges:.2f}")
    print(f"  Trade log saved  : {TRADE_LOG_FILE}")
    print("=" * 62)
    print("\nSIMULATION ONLY — NO REAL MONEY INVOLVED\n")


# ─────────────────────────────────────────────
# GRACEFUL EXIT ON CTRL+C
# ─────────────────────────────────────────────
def handle_exit(sig, frame):
    print("\n\n  [Ctrl+C received — shutting down]")
    print_summary("User interrupted (Ctrl+C)")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    global bankroll, last_action

    # ── Too-late guard ────────────────────────────────────────────
    # If GitHub Actions delayed the scheduled run past the buy cutoff,
    # there is nothing useful to do — exit immediately rather than
    # sitting idle until 3:15 PM force-sell with no positions.
    start_ist = now_ist()
    start_hm  = (start_ist.hour, start_ist.minute)
    if start_hm >= CUTOFF_BUY:
        print(f"\n  [LATE START] It is {start_ist.strftime('%H:%M')} IST — past the "
              f"{CUTOFF_BUY[0]}:{CUTOFF_BUY[1]:02d} buy cutoff. Exiting.")
        sys.exit(0)

    # ── Weekend guard ─────────────────────────────────────────────
    # NSE is closed on Saturdays and Sundays. Exit immediately so
    # manually triggered workflow_dispatch runs on weekends don't
    # waste GitHub Actions minutes or produce empty log files.
    today_ist = now_ist()
    weekday   = today_ist.weekday()   # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    if weekday >= 5:
        day_name = today_ist.strftime("%A")
        print(f"\n  [{day_name}] NSE is closed on weekends. Nothing to do.")
        print("  Exiting — no simulation run on weekends.\n")
        sys.exit(0)

    print("\n" + "=" * 62)
    print("  SIMULATION ONLY — NO REAL MONEY INVOLVED")
    print("  NSE Intraday Simulator | Start: ₹1,000 | Target: ₹1,500")
    print("=" * 62)
    print(f"  Watchlist : {', '.join(WATCHLIST)}")
    print(f"  Interval  : {POLL_INTERVAL // 60} minutes")
    print(f"  Cutoff    : {CUTOFF_BUY[0]:02d}:{CUTOFF_BUY[1]:02d} IST (no new buys after)")
    print(f"  Force-sell: {FORCE_SELL[0]:02d}:{FORCE_SELL[1]:02d} IST")
    print("=" * 62 + "\n")

    seed_price_history()

    while True:
        current_prices = {}   # symbol -> latest price this cycle
        prev_prices    = {}   # symbol -> second-to-last price in history

        # ── 1. Fetch prices for all symbols ──────────────────────
        print("  Fetching prices...")
        for symbol in WATCHLIST:
            price = fetch_price(symbol)
            if price is None:
                continue
            current_prices[symbol] = price

            # Store in history for RSI (keep last 50 readings)
            price_history[symbol].append(price)
            if len(price_history[symbol]) > 50:
                price_history[symbol].pop(0)

            # Previous price = second-to-last in history
            hist = price_history[symbol]
            prev_prices[symbol] = hist[-2] if len(hist) >= 2 else None

        # ── 2. Force-sell all at EOD ──────────────────────────────
        if is_force_sell_time():
            print("  [EOD] 3:15 PM — force-closing all positions.")
            for sym in list(positions.keys()):
                cp  = current_prices.get(sym)
                rsi = compute_rsi(price_history[sym])
                if cp:
                    attempt_sell(sym, cp, rsi, force=True)
            print_status(current_prices)
            print_summary("EOD — market closed at 3:15 PM IST")
            sys.exit(0)

        # ── 3. Evaluate sell signals on open positions ────────
        for sym in list(positions.keys()):
            cp  = current_prices.get(sym)
            if cp is None:
                continue
            rsi = compute_rsi(price_history[sym])
            attempt_sell(sym, cp, rsi)

        # ── 4. Evaluate buy signals on watchlist ──────────────
        for sym in WATCHLIST:
            cp   = current_prices.get(sym)
            prev = prev_prices.get(sym)
            if cp is None:
                continue
            rsi = compute_rsi(price_history[sym])
            attempt_buy(sym, cp, prev, rsi)

        # ── 5. Print status ───────────────────────────────────────
        print_status(current_prices)

        # If no action was taken this cycle
        if last_action == "WAITING" or last_action.startswith("HELD"):
            if positions:
                last_action = "HELD (monitoring positions)"
            else:
                last_action = "WAITING (no signal)"

        # ── 6. Win / loss checks ──────────────────────────────────
        position_value = sum(
            current_prices.get(sym, pos["avg_price"]) * pos["qty"]
            for sym, pos in positions.items()
        )
        total_value = bankroll + position_value

        if total_value >= TARGET_CAPITAL:
            print(f"\n  🎯 TARGET REACHED! Portfolio = ₹{total_value:.2f}")
            print_summary("TARGET REACHED — ₹1,500 achieved!")
            sys.exit(0)

        if total_value < STOP_CAPITAL:
            print(f"\n  ❌ STOP TRIGGERED! Portfolio dropped to ₹{total_value:.2f}")
            print_summary("STOP LOSS TRIGGERED — portfolio below ₹500")
            sys.exit(1)

        # ── 7. Sleep until next poll ──────────────────────────────
        print(f"\n  Next poll in {POLL_INTERVAL // 60} min. Press Ctrl+C to exit.\n")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
