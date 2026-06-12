# NSE Intraday Trading Simulator

> **SIMULATION ONLY — NO REAL MONEY INVOLVED**

Automated intraday trading simulator for NSE-listed stocks.  
Starts with ₹1,000 and targets ₹1,500 (50% gain). Stops at ₹500 (50% loss).

## How it works

- Watches: `CIG`, `WIT`, `PLUG`, `DNN`, `ABEV`, `GRAB`, `BBD`, `BTG`, `OPEN`, `VRRM`, `CCC`, `GGB`, `BTE`, `TMC`, `LCID`, `NIO`, `ACHR`, `SNAP`, `LYG`, `SBS`, `KEEL`, `PTON`, `AUR`, `RIG`, `EOSE`
- Polls prices every 5 minutes via `yfinance`
- Buys/sells automatically using RSI + momentum signals
- Simulates realistic Zerodha brokerage charges
- Force-closes all positions at 3:15 PM IST

## Automation (GitHub Actions)

The workflow runs **automatically every weekday at 9:15 AM IST**.  
After each session, a dated `trade_log_YYYY-MM-DD.csv` is saved as a downloadable artifact under the **Actions** tab.

### Manual run
Go to **Actions → NSE Market Simulator → Run workflow**.

## Local run

```bash
pip install yfinance pandas requests pytz
python simulator.py
```
