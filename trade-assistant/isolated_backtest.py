import pandas as pd
import glob
import os
import csv
from assistant.backtest import engine, report
from assistant.core.config import load_config
from assistant.strategies import registry
from assistant.strategies.base import Strategy

csv_files = glob.glob("/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/Client_Data_Export/**/*Daily*.csv", recursive=True)

prices_dict = {}
tickers = []
for f in csv_files:
    basename = os.path.basename(f)
    ticker = basename.split("_Daily")[0].replace("_X", "=X").replace("_F", "=F").replace("_L", ".L")
    df = pd.read_csv(f)
    df['Date'] = pd.to_datetime(df['Date'], utc=True)
    df.set_index('Date', inplace=True)
    df.sort_index(inplace=True)
    df['ATR'] = df['Close'].rolling(14).std()
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    prices_dict[ticker] = df
    tickers.append(ticker)

config = load_config()
def price_fn(t): return prices_dict.get(t)
benchmark = lambda: prices_dict.get("SPY")['Close']

all_strategies = [s.name for s in registry.BUILTIN if issubclass(s, Strategy)]

results = []
all_trades = []

print(f"Running isolated backtests for {len(all_strategies)} strategies...")

for strat_name in all_strategies:
    config["strategy_priority"] = [strat_name]
    result = engine.run_backtest(tickers, config, price_fn, benchmark_fn=benchmark)
    out = report.build(result, config, period="10y")
    
    # Extract only this strategy's stats from the dictionary
    strat_stats = out.get("by_strategy", {}).get(strat_name)
            
    if strat_stats and strat_stats['trades'] > 0:
        results.append({
            "Strategy": strat_name,
            "Trades": strat_stats['trades'],
            "Win Rate %": round(strat_stats.get('win_rate_pct', 0) * 100, 1),
            "Expectancy (R)": round(strat_stats.get('expectancy_r', 0), 2),
            "P&L": round(strat_stats.get('pnl', 0), 2)
        })
        strat_trades = [t for t in out.get("trades", []) if t["strategy"] == strat_name]
        all_trades.extend(strat_trades)
    else:
        results.append({
            "Strategy": strat_name,
            "Trades": 0,
            "Win Rate %": 0,
            "Expectancy (R)": 0,
            "P&L": 0
        })

report_path = "/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/Isolated_Strategies_Report.csv"
if results:
    with open(report_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["Strategy", "Trades", "Win Rate %", "Expectancy (R)", "P&L"])
        writer.writeheader()
        writer.writerows(results)
    
trades_path = "/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/Isolated_All_Trades.csv"
if all_trades:
    keys = list(all_trades[0].keys())
    with open(trades_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(all_trades)
        
print(f"SUCCESS: Wrote {len(results)} rows to {report_path}")
