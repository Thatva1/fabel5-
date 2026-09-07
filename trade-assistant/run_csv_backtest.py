import pandas as pd
import glob
import os
from assistant.backtest import engine, report
from assistant.core.config import load_config

csv_files = glob.glob("/Users/thatvagowda/Desktop/fabel 5/antigravity results and data/Client_Data_Export/**/*Daily*.csv", recursive=True)

prices_dict = {}
tickers = []
for f in csv_files:
    basename = os.path.basename(f)
    ticker = basename.split("_Daily")[0].replace("_X", "=X").replace("_F", "=F").replace("_L", ".L")
    if ticker == "CL=F" or ticker == "GC=F":
        pass
        
    df = pd.read_csv(f)
    df['Date'] = pd.to_datetime(df['Date'], utc=True)
    df.set_index('Date', inplace=True)
    df.sort_index(inplace=True)
    prices_dict[ticker] = df
    tickers.append(ticker)

config = load_config()

def price_fn(t):
    return prices_dict.get(t)

result = engine.run_backtest(tickers, config, price_fn, benchmark_fn=lambda: prices_dict.get("SPY")['Close'])
out = report.build(result, config, period="10y")

import json
output_text = f"# 34 Strategies Backtest Results (Client CSV Dataset)\n\n"
output_text += f"**Universe:** {len(tickers)} highly liquid core instruments spanning 5 asset classes (loaded locally from your CSV export).\n"
output_text += f"**Period:** 10 Years\n\n"
output_text += f"### Overall Performance\n"
overall = out["overall"]
if overall["trades"] == 0:
    output_text += f"No trades executed. The strict risk parameters did not trigger.\n"
else:
    output_text += f"- **Total Trades:** {overall['trades']}\n"
    output_text += f"- **Win Rate:** {overall['win_rate_pct']*100:.1f}% ({overall['wins']}W / {overall['losses']}L / {overall['scratches']}S)\n"
    output_text += f"- **Expectancy:** {overall['expectancy_r']:.2f}R\n"
    output_text += f"- **Profit Factor:** {overall['profit_factor']:.2f}\n"
    output_text += f"- **Net P&L:** {out['equity_curve']['final_pnl']:.2f} {out['base_currency']}\n\n"

    output_text += f"### Strategy Allocation (Who 'won' the capital)\n"
    # out["by_strategy"] is a dict mapping name to stats
    for strat_name, strat in out["by_strategy"].items():
        if strat['trades'] > 0:
            output_text += f"- **{strat_name}**: {strat['trades']} trades, {strat['win_rate_pct']*100:.1f}% win rate, {strat['expectancy_r']:.2f}R expectancy, P&L: {strat['pnl']:.0f}\n"

with open("/Users/thatvagowda/.gemini/antigravity/brain/aa6d07f9-a8ef-4a0d-9ce6-c4519a2de6b7/34_Strategies_Backtest.md", "w") as f:
    f.write(output_text)

print("Done. Wrote results to 34_Strategies_Backtest.md")
