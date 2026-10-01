"""Backtest the whole strategy library on the ten-instrument CSV export.

    python run_csv_backtest.py

Reads the daily CSVs under `antigravity results and data/Client_Data_Export`
and writes every trade to `Backtest_All_Trades.csv` beside them. Ten
instruments is a smoke test, not a study: most of the library ranks an
instrument against a universe, and ten mixed assets are not one.
"""
import csv
import glob
import os

import pandas as pd

from assistant.backtest import engine, report
from assistant.core.config import load_config

DATA_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "antigravity results and data"))


def load_csv_prices():
    """{ticker: daily OHLCV frame} for every *Daily* CSV in the export."""
    pattern = os.path.join(DATA_DIR, "Client_Data_Export", "**", "*Daily*.csv")
    prices = {}
    for path in sorted(glob.glob(pattern, recursive=True)):
        name = os.path.basename(path).split("_Daily")[0]
        ticker = name.replace("_X", "=X").replace("_F", "=F").replace("_L", ".L")
        frame = pd.read_csv(path)
        frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
        prices[ticker] = frame.set_index("Date").sort_index()
    return prices


def run(config, prices):
    """One replay of `config` over `prices`; returns the report dict."""
    result = engine.run_backtest(list(prices), config, prices.get,
                                 benchmark_fn=lambda: prices["SPY"]["Close"])
    return report.build(result, config, period="10y")


def write_trades(trades, filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(trades[0].keys()),
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trades)
    return path


if __name__ == "__main__":
    out = run(load_config(), load_csv_prices())
    trades = out.get("trades", [])
    if trades:
        print(f"Wrote {len(trades)} trades to {write_trades(trades, 'Backtest_All_Trades.csv')}")
    else:
        print("NO TRADES")
