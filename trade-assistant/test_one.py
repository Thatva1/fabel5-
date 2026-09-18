print("Script starting...")
import pandas as pd
print("Imported pandas...")
import glob
import os
print("Importing engine...")
from assistant.backtest import engine, report
print("Imported engine...")
from assistant.core.config import load_config
print("Imported config...")
from assistant.strategies import registry

print("Finding CSVs...")
csv_files = glob.glob("/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/Client_Data_Export/**/*Daily*.csv", recursive=True)

prices_dict = {}
tickers = []
for f in csv_files:
    print(f"Loading {f}...")
    basename = os.path.basename(f)
    ticker = basename.split("_Daily")[0].replace("_X", "=X").replace("_F", "=F").replace("_L", ".L")
    df = pd.read_csv(f)
    df['Date'] = pd.to_datetime(df['Date'], utc=True)
    df.set_index('Date', inplace=True)
    df.sort_index(inplace=True)
    prices_dict[ticker] = df
    tickers.append(ticker)

config = load_config()
def price_fn(t): return prices_dict.get(t)

print("Starting backtest...")
config["strategy_priority"] = ["pead_drift"]
result = engine.run_backtest(tickers, config, price_fn, benchmark_fn=lambda: prices_dict.get("SPY")['Close'])
print("Backtest finished.")
