# NSE Intraday Trading Simulator

> **SIMULATION ONLY — NO REAL MONEY INVOLVED**

Automated intraday trading simulator for NSE-listed stocks.  
Starts with ₹1,000 and targets ₹1,500 (50% gain). Stops at ₹500 (50% loss).

## How it works

- Watches 50 NSE penny stocks by volume: `AURIGROW.NS`, `DHARAN.NS`, `GATECH.NS`, `FCONSUMER.NS`, `GRADIENTE.NS`, `OSIAHYPER.NS`, `INVENTURE.NS`, `TPHQ.NS`, `GATECHDVR.NS`, `GANGAFORGE.NS`, `HAVISHA.NS`, `ORCHASP.NS`, `ROLLATAIN.NS`, `MITTALLIFE.NS`, `SANWARIA.NS`, `VCL.NS`, `SITI.NS`, `VISAGAR.NS`, `SRPL.NS`, `FUTENTER.NS`, `SUNDARAM.NS`, `AKSHAR.NS`, `ARSHIYA.NS`, `AJOONI.NS`, `ANTARCTICA.NS`, `MEPINFRA.NS`, `DEBOCK.NS`, `VINNY.NS`, `AGSTRA.NS`, `SAIFL.NS`, `SUPREMEENG.NS`, `PARASPETRO.NS`, `GAYAHWS.NS`, `COUNCODOS.NS`, `NOIDATOLL.NS`, `GOYALALUM.NS`, `VIJIFIN.NS`, `VAISHALI.NS`, `TRUCAP.NS`, `PRAKASHSTL.NS`, `LCCINFOTEC.NS`, `HDIL.NS`, `ORIENTALTL.NS`, `KANANIIND.NS`, `EDUCOMP.NS`, `ZENITHSTL.NS`, `ACCURACY.NS`, `NEXTMEDIA.NS`, `DANGEE.NS`, `BGLOBAL.NS`
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
