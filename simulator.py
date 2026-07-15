"""
=============================================================
SIMULATION ONLY — NO REAL MONEY INVOLVED
=============================================================
NSE Intraday Trading Simulator
Starting capital: ₹1,000 | Target: ₹1,500 | Stop: ₹500
=============================================================
"""

import yfinance as yf
import time
import csv
import os
import json
import signal
import sys

# Force UTF-8 output so the Rupee sign (₹) renders correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime
import pytz

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
STARTING_CAPITAL   = 1000.0
TARGET_CAPITAL     = 1500.0
STOP_CAPITAL       = 500.0
BANKROLL_RESET_FLOOR = 750.0      # reset to STARTING_CAPITAL if prior day's bankroll ends below this
POLL_INTERVAL      = 300          # seconds between price refreshes (5 min)
MAX_POSITIONS      = 2            # never hold more than this many stocks at once
MAX_INVEST_RATIO   = 0.80         # never invest more than 80% of bankroll in one position
CUTOFF_BUY         = (14, 45)     # no new buys after 2:45 PM IST (hour, minute)
FORCE_SELL         = (15, 15)     # force-close all positions at 3:15 PM IST
WARNING_LOSS_PCT   = 3.0          # warn if unrealised loss on a position exceeds 3%
WARNING_CHARGES_RS = 100.0        # warn if total charges exceed ₹100

# Backtesting 166 round-trip trades across 14 sessions showed a 9.6% win rate:
# most of the watchlist trades under ₹1, where NSE's ₹0.01 tick alone is a
# 2-6% price move — bigger than the old flat 0.5%/1%/2% thresholds below. The
# bot was buying single-tick noise and getting stop-lossed by the very next
# tick. Thresholds are now the larger of a flat % and a tick-count, so cheap
# stocks need a real multi-tick move instead of one tick of noise.
#
# That tick-size fix didn't move the needle: 22 live sessions (Jun 9-Jul 14)
# still showed a ~10% win rate, with 64% of exits being stop-losses fired on
# the very next poll after entry (e.g. bought 09:31, stop-lossed 09:36). Two
# more causes identified from those logs:
#  1. The momentum entry only required the last poll to not be a reversal —
#     a single 5-min spike on a thin stock still qualified. Confirmation now
#     spans one more poll (15 min instead of 10) before we'll buy into it.
#  2. RSI regularly pinned at exactly 100.0 and forced flat/losing exits.
#     That happens when a stale, thinly-traded feed shows zero down-ticks in
#     the lookback window — an artifact of no real trading, not a genuine
#     overbought move. RSI readings from a too-uniform window are now
#     treated as unusable instead of trusted at face value.
TICK_SIZE           = 0.01        # NSE minimum tick size (₹) in this price range
BUY_MOMENTUM_PCT     = 0.5        # base momentum threshold (% since previous poll)
STOP_LOSS_PCT        = 1.0        # base stop-loss threshold (%)
TAKE_PROFIT_PCT      = 2.0        # base take-profit threshold (%)
MIN_MOMENTUM_TICKS   = 3          # buy move must clear at least this many ticks
MIN_STOPLOSS_TICKS   = 4          # stop-loss must be at least this many ticks below buy price
MIN_TAKEPROFIT_TICKS = 8          # take-profit must be at least this many ticks above buy price
MIN_VOLUME_5MIN      = 2000       # min shares traded in the latest 5-min bar (liquidity filter)
MIN_RSI_DISTINCT_PRICES = 4       # min distinct closes in the RSI lookback before trusting it

WATCHLIST = [
    "AURIGROW.NS",    # Auri Grow India Ltd
    "DHARAN.NS",      # Dharan Infra-EPC Ltd
    "GATECH.NS",      # GACM Technologies Ltd
    "FCONSUMER.NS",   # Future Consumer Ltd
    "GRADIENTE.NS",   # Gradiente Infotainment Ltd
    "OSIAHYPER.NS",   # Osia Hyper Retail Ltd
    "INVENTURE.NS",   # Inventure Growth & Securities Ltd
    "TPHQ.NS",        # Teamo Productions HQ Ltd
    "GATECHDVR.NS",   # GACM Technologies Ltd-DVR
    "GANGAFORGE.NS",  # Ganga Forging Ltd
    "HAVISHA.NS",     # Sri Havisha Hospitality & Infra Ltd
    "ORCHASP.NS",     # Orchasp Ltd
    "ROLLT.NS",       # Rollatainers Ltd
    "MITTAL.NS",      # Mittal Life Style Ltd
    "SANWARIA.NS",    # Sanwaria Consumer Ltd
    "VCL.NS",         # Vaxtex Cotfab Ltd
    "SITINET.NS",     # Siti Networks Ltd
    "VIVIDHA.NS",     # Visagar Polytex Ltd
    "SRPL.NS",        # Shree Ram Proteins Ltd
    "FEL.NS",         # Future Enterprises Ltd
    "SUNDARAM.NS",    # Sundaram Multi Pap Ltd
    "AKSHAR.NS",      # Akshar Spintex Ltd
    "ARSHIYA.NS",     # Arshiya Ltd
    "AJOONI.NS",      # Ajooni Biotech Ltd
    "ANTGRAPHIC.NS",  # Antarctica Ltd
    "MEP.NS",         # MEP Infrastructure Developers Ltd
    "DIL.NS",         # Debock Industries Ltd
    "VINNY.NS",       # Vinny Overseas Ltd
    "AGSTRA.NS",      # AGS Transact Technologies Ltd
    "SAIFL.NS",       # Sameera Agro and Infra Ltd
    "SUPREMEENG.NS",  # Supreme Engineering Ltd
    "PARASPETRO.NS",  # Paras Petrofils Ltd
    "GAYAHWS.NS",     # Gayatri Highways Ltd
    "COUNCODOS.NS",   # Country Condos Ltd
    "NOIDATOLL.NS",   # Noida Toll Bridge Company Ltd
    "GOYALALUM.NS",   # Goyal Aluminiums Ltd
    "VIJIFIN.NS",     # Viji Finance Ltd
    "VAISHALI.NS",    # Vaishali Pharma Ltd
    "TRU.NS",         # TruCap Finance Ltd
    "PRAKASHSTL.NS",  # Prakash Steelage Ltd
    "LCCINFOTEC.NS",  # LCC Infotech Ltd
    "HDIL.NS",        # Housing Development & Infrastructure Ltd
    "ORIENTALTL.NS",  # Oriental Trimex Ltd
    "KANANIIND.NS",   # Kanani Industries Ltd
    "EDUCOMP.NS",     # Educomp Solutions Ltd
    "ZENITHSTL.NS",   # Zenith Steel Pipes & Industries Ltd
    "ACCURACY.NS",    # Accuracy Shipping Ltd
    "NEXTMEDIA.NS",   # Next Mediaworks Ltd
    "DANGEE.NS",      # Dangee Dums Ltd
    "BGLOBAL.NS",     # Bharatiya Global Infomedia Ltd
]

TRADE_LOG_FILE  = "trade_log.csv"
TRADE_LOG_FIELDS = ["timestamp", "symbol", "action", "qty", "price", "charges", "reason", "bankroll_after"]
BANKROLL_FILE   = "bankroll.json"
IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# CARRY-OVER BANKROLL
# ─────────────────────────────────────────────
def load_bankroll() -> float:
    """Returns previous session's ending bankroll, or STARTING_CAPITAL if none.
    If the saved bankroll is below BANKROLL_RESET_FLOOR (₹750), resets to STARTING_CAPITAL (₹1,000).
    """
    if os.path.isfile(BANKROLL_FILE):
        try:
            data = json.load(open(BANKROLL_FILE))
            saved = float(data.get("bankroll", STARTING_CAPITAL))
            print(f"  Carried over bankroll from last session: ₹{saved:.2f}")
            if saved < BANKROLL_RESET_FLOOR:
                print(f"  Bankroll ₹{saved:.2f} is below ₹{BANKROLL_RESET_FLOOR:.0f} — resetting to ₹{STARTING_CAPITAL:.0f}")
                return STARTING_CAPITAL
            return saved
        except Exception:
            pass
    return STARTING_CAPITAL

def save_bankroll(amount: float):
    """Persists the ending bankroll so the next session can carry it over."""
    with open(BANKROLL_FILE, "w") as f:
        json.dump({"bankroll": round(amount, 2)}, f)


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
    Returns None if insufficient or degenerate data.
    """
    if len(prices) < period + 1:
        return None

    # Use only the most recent (period+1) prices to get 'period' changes
    recent = prices[-(period + 1):]

    # A too-uniform window (few distinct prices) means the feed is mostly
    # stale/repeated closes — one lone uptick then makes avg_loss trivially
    # 0 and RSI pins at exactly 100, which reads as "overbought" but is
    # really just an absence of data rather than a real move.
    if len(set(recent)) < MIN_RSI_DISTINCT_PRICES:
        return None

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
# TICK-SIZE-AWARE THRESHOLDS
# ─────────────────────────────────────────────
def min_pct_for_ticks(price: float, ticks: int) -> float:
    """
    Converts a tick count into a % move at the given price.
    Used to keep buy/stop-loss/take-profit thresholds meaningfully above
    single-tick noise on low-priced penny stocks.
    """
    if price <= 0:
        return 0.0
    return ticks * TICK_SIZE / price * 100


# ─────────────────────────────────────────────
# FETCH LIVE PRICE VIA YFINANCE
# ─────────────────────────────────────────────
def fetch_price(symbol: str) -> tuple[float, float] | None:
    """
    Fetches the latest available price and volume for the given NSE symbol.
    Uses a 1-day, 1-minute interval ticker: price is the most recent close,
    volume is summed over the last 5 one-minute bars to approximate the
    volume traded over one poll interval (for the MIN_VOLUME_5MIN filter —
    a single 1-min bar understates a 5-min window by ~5x).
    Returns (price, volume) or None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="1d", interval="1m")
        if hist.empty:
            return None
        price  = float(hist["Close"].iloc[-1])
        volume = float(hist["Volume"].tail(5).sum())
        return price, volume
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
def log_trade(timestamp, symbol, action, qty, price, charges, bankroll_after, reason=""):
    """Appends one trade record to the in-memory log and flushes to CSV."""
    record = {
        "timestamp"     : timestamp,
        "symbol"        : symbol,
        "action"        : action,
        "qty"           : qty,
        "price"         : price,
        "charges"       : charges,
        "reason"        : reason,
        "bankroll_after": bankroll_after,
    }
    trade_log.append(record)

    # Fieldnames are a fixed schema (not derived from this record's own
    # keys) so every row — BUY or SELL, whatever reason string it carries —
    # lands in the same columns instead of silently drifting if a future
    # call passes a different key set.
    file_exists = os.path.isfile(TRADE_LOG_FILE)
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
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
def attempt_buy(symbol: str, current_price: float, prev_price: float, rsi: float,
                 volume: float | None, price_hist: list):
    """
    BUY signal: price rose above a tick-size-adjusted momentum threshold AND
    that rise is confirmed over the last two polls (not a single-tick blip)
    AND RSI < 65 AND liquidity is adequate AND not near upper circuit AND we
    have room for another position.
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

    # Liquidity check — thinly traded stocks produce stale/noisy 1-min bars
    if volume is None or volume < MIN_VOLUME_5MIN:
        return

    # Signal check
    if prev_price is None or prev_price == 0:
        return
    price_change_pct = (current_price - prev_price) / prev_price * 100
    required_momentum_pct = max(BUY_MOMENTUM_PCT, min_pct_for_ticks(current_price, MIN_MOMENTUM_TICKS))
    if price_change_pct <= required_momentum_pct:
        return

    # Confirmation check — require the rise to hold over the last three
    # polls (15 min), not just a single tick bouncing up then back down.
    # Live logs showed single 5-min spikes on thin stocks reverting on the
    # very next poll; the wider window filters that out before we buy.
    if len(price_hist) >= 3 and price_hist[-3] > price_hist[-2]:
        return
    if len(price_hist) >= 4 and price_hist[-4] > price_hist[-3]:
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

    ts     = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    reason = f"momentum +{price_change_pct:.2f}%, RSI={rsi}"
    log_trade(ts, symbol, "BUY", qty, current_price, charges, round(bankroll, 2), reason=reason)
    last_action = f"BOUGHT {qty} x {symbol} @ ₹{current_price:.2f} | charges ₹{charges:.2f}"
    print(f"  >>> BUY  {symbol}: {qty} shares @ ₹{current_price:.2f} | RSI={rsi} | +{price_change_pct:.2f}% | charges ₹{charges:.2f}")


# ─────────────────────────────────────────────
# SELL LOGIC
# ─────────────────────────────────────────────
def attempt_sell(symbol: str, current_price: float, rsi: float, force: bool = False):
    """
    SELL if:
      (a) stop-loss: price dropped below a tick-size-adjusted floor below buy price
      (b) take-profit: price rose above a tick-size-adjusted ceiling above buy price
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

    stop_loss_pct   = max(STOP_LOSS_PCT, min_pct_for_ticks(buy_price, MIN_STOPLOSS_TICKS))
    take_profit_pct = max(TAKE_PROFIT_PCT, min_pct_for_ticks(buy_price, MIN_TAKEPROFIT_TICKS))

    sell_reason = None
    if force:
        sell_reason = "FORCE-CLOSE (session end)"
    elif pnl_pct <= -stop_loss_pct:
        sell_reason = f"STOP-LOSS ({pnl_pct:.2f}%)"
    elif pnl_pct >= take_profit_pct:
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
    log_trade(ts, symbol, "SELL", qty, current_price, charges, round(bankroll, 2), reason=sell_reason)
    last_action = f"SOLD {qty} x {symbol} @ ₹{current_price:.2f} [{sell_reason}] | charges ₹{charges:.2f}"
    print(f"  >>> SELL {symbol}: {qty} shares @ ₹{current_price:.2f} | {sell_reason} | charges ₹{charges:.2f}")


# ─────────────────────────────────────────────
# BEST-AVAILABLE PRICE FOR A HELD POSITION
# ─────────────────────────────────────────────
def resolve_price(symbol: str, current_prices: dict) -> float:
    """
    Best available price for a symbol we hold: this poll's live quote if we
    have one, else the last price we actually saw for it, else (only if
    we've never seen a quote at all) the entry price. Falling straight to
    avg_price on a missed fetch would manufacture a fake breakeven — the
    last known market price is always a better estimate of what it's
    actually worth right now.
    """
    if symbol in current_prices:
        return current_prices[symbol]
    hist = price_history.get(symbol)
    if hist:
        return hist[-1]
    return positions[symbol]["avg_price"]


# ─────────────────────────────────────────────
# FORCE-CLOSE ALL OPEN POSITIONS
# ─────────────────────────────────────────────
def force_close_all_positions(current_prices: dict):
    """
    Force-sells every open position at the best available price.
    Must run before any session-ending print_summary()/sys.exit() — target
    reached, stop-loss, and EOD all end the session, and print_summary()
    only persists the cash bankroll, not the value of unsold positions.
    """
    for sym in list(positions.keys()):
        cp  = resolve_price(sym, current_prices)
        rsi = compute_rsi(price_history[sym])
        attempt_sell(sym, cp, rsi, force=True)


# ─────────────────────────────────────────────
# TERMINAL DISPLAY
# ─────────────────────────────────────────────
def print_status(prices: dict):
    """Prints one poll-cycle status report to the terminal."""
    global poll_count
    poll_count += 1

    now_str        = now_ist().strftime("%Y-%m-%d %H:%M:%S IST")
    position_value = sum(resolve_price(sym, prices) * pos["qty"] for sym, pos in positions.items())
    total_value    = bankroll + position_value
    progress       = (total_value - STARTING_CAPITAL) / (TARGET_CAPITAL - STARTING_CAPITAL) * 100

    print("\n" + "=" * 62)
    print(f"  POLL #{poll_count} | {now_str}")
    print("=" * 62)
    print(f"  Bankroll      : ₹{bankroll:.2f}")
    print(f"  Total charges : ₹{total_charges:.2f}", end="")

    if total_charges > WARNING_CHARGES_RS:
        print("  ⚠ WARNING: charges > ₹100!", end="")
    print()

    # Progress bar — tracks total portfolio value (cash + open positions),
    # not cash alone, so it doesn't look like a loss right after a normal buy
    bar_len    = 30
    filled     = max(0, min(bar_len, int(bar_len * progress / 100)))
    bar        = "█" * filled + "░" * (bar_len - filled)
    print(f"  Goal progress : [{bar}] {progress:.1f}%  (₹{total_value:.0f} / ₹{TARGET_CAPITAL:.0f})")
    print(f"  Last action   : {last_action}")

    # Open positions
    if positions:
        print(f"\n  {'Symbol':<12} {'Qty':>5} {'Buy@':>8} {'Now@':>8} {'UnrPnL':>10} {'UnrPnL%':>8}")
        print("  " + "-" * 56)
        for sym, pos in positions.items():
            cp  = resolve_price(sym, prices)
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

    sells  = [t for t in trade_log if t["action"] == "SELL"]
    buys   = [t for t in trade_log if t["action"] == "BUY"]

    # Match buys and sells by symbol to compute per-trade P&L, net of the
    # brokerage charged on both legs — a trade whose price barely moved can
    # still be a net loser once charges are subtracted, so a raw
    # sell_price > buy_price comparison would overstate the win rate.
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
                buy_cost     = buy_rec["price"] * buy_rec["qty"] + buy_rec["charges"]
                sell_proceeds = t["price"] * t["qty"] - t["charges"]
                if sell_proceeds > buy_cost:
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
    save_bankroll(bankroll)


# ─────────────────────────────────────────────
# GRACEFUL EXIT ON CTRL+C
# ─────────────────────────────────────────────
def handle_exit(sig, frame):
    print("\n\n  [Shutdown signal received — closing out and saving state]")
    force_close_all_positions({})
    print_summary("Interrupted (signal)")
    sys.exit(0)

# Handle both Ctrl+C (SIGINT) and process termination (SIGTERM — this is
# what GitHub Actions sends on job cancellation or a timeout-minutes kill).
# Without SIGTERM handled here, a cancelled/timed-out run would skip
# force-closing positions and saving the bankroll entirely.
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    global bankroll, last_action, STARTING_CAPITAL
    bankroll = STARTING_CAPITAL = load_bankroll()

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
    print(f"  NSE Intraday Simulator | Start: ₹{STARTING_CAPITAL:.2f} | Target: ₹{TARGET_CAPITAL:.2f}")
    print("=" * 62)
    print(f"  Watchlist : {', '.join(WATCHLIST)}")
    print(f"  Interval  : {POLL_INTERVAL // 60} minutes")
    print(f"  Cutoff    : {CUTOFF_BUY[0]:02d}:{CUTOFF_BUY[1]:02d} IST (no new buys after)")
    print(f"  Force-sell: {FORCE_SELL[0]:02d}:{FORCE_SELL[1]:02d} IST")
    print("=" * 62 + "\n")

    seed_price_history()

    while True:
        current_prices  = {}   # symbol -> latest price this cycle
        current_volumes = {}   # symbol -> latest 1-min bar volume this cycle
        prev_prices     = {}   # symbol -> second-to-last price in history

        # ── 1. Fetch prices for all symbols ──────────────────────
        print("  Fetching prices...")
        for symbol in WATCHLIST:
            result = fetch_price(symbol)
            if result is None:
                continue
            price, volume = result
            current_prices[symbol]  = price
            current_volumes[symbol] = volume

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
            force_close_all_positions(current_prices)
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

        # ── 4. Evaluate buy signals, strongest momentum first ──
        # With MAX_POSITIONS as the binding constraint, walking WATCHLIST in
        # its fixed order would always let earlier tickers claim open slots
        # regardless of signal strength — rank candidates by this poll's
        # price move instead so the strongest mover wins ties.
        candidates = []
        for sym in WATCHLIST:
            cp   = current_prices.get(sym)
            prev = prev_prices.get(sym)
            if cp is None or not prev:
                continue
            change_pct = (cp - prev) / prev * 100
            candidates.append((change_pct, sym))
        candidates.sort(reverse=True)

        for _, sym in candidates:
            cp   = current_prices[sym]
            prev = prev_prices[sym]
            rsi  = compute_rsi(price_history[sym])
            vol  = current_volumes.get(sym)
            attempt_buy(sym, cp, prev, rsi, vol, price_history[sym])

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
            resolve_price(sym, current_prices) * pos["qty"]
            for sym, pos in positions.items()
        )
        total_value = bankroll + position_value

        if total_value >= TARGET_CAPITAL:
            print(f"\n  🎯 TARGET REACHED! Portfolio = ₹{total_value:.2f}")
            force_close_all_positions(current_prices)
            print_summary("TARGET REACHED — ₹1,500 achieved!")
            sys.exit(0)

        if total_value < STOP_CAPITAL:
            print(f"\n  ❌ STOP TRIGGERED! Portfolio dropped to ₹{total_value:.2f}")
            force_close_all_positions(current_prices)
            print_summary("STOP LOSS TRIGGERED — portfolio below ₹500")
            sys.exit(1)

        # ── 7. Sleep until next poll ──────────────────────────────
        print(f"\n  Next poll in {POLL_INTERVAL // 60} min. Press Ctrl+C to exit.\n")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
