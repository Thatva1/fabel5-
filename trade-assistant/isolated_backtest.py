"""Backtest each daily strategy ON ITS OWN on the ten-instrument CSV export.

    python isolated_backtest.py

Isolation means every other strategy is switched off for that run. Putting one
name at the top of `strategy_priority` is not isolation: the others still
trade, still take the one slot each instrument has, and block the strategy
being measured — so its trade count is whatever the rest left over.

A strategy switched off in config.yaml is switched ON for its own run, so an
unmeasured rule can be looked at here without touching the live paper book.

Ten instruments is a smoke test, not a study. A strategy that ranks an
instrument against a universe (xs_momentum, dual_momentum, sector_momentum,
low_volatility, the reversals) has no universe here and will show zero trades.
That is the data set, not the strategy.
"""
import copy
import csv
import os
from multiprocessing import Pool

from assistant.core.config import load_config
from assistant.strategies import registry
from run_csv_backtest import DATA_DIR, load_csv_prices, run, write_trades

COLUMNS = ["Strategy", "Trades", "Win Rate %", "Expectancy (R)", "P&L"]


def isolate(config, name):
    """A copy of `config` in which `name` is the only strategy switched on."""
    isolated = copy.deepcopy(config)
    blocks = isolated.setdefault("strategies", {})
    for cls in registry.BUILTIN:
        blocks.setdefault(cls.name, {})
        blocks[cls.name] = {**(blocks[cls.name] or {}), "enabled": cls.name == name}
    return isolated


def measure(name):
    """One strategy alone. Loads its own data so it can run in its own process."""
    out = run(isolate(load_config(), name), load_csv_prices())
    stats = out.get("by_strategy", {}).get(name) or {}
    # win_rate_pct is already a percentage; multiplying it by 100 again is what
    # once printed a win rate of 4,760%.
    row = {"Strategy": name,
           "Trades": stats.get("trades", 0),
           "Win Rate %": stats.get("win_rate_pct") or 0,
           "Expectancy (R)": round(stats.get("expectancy_r") or 0, 2),
           "P&L": round(stats.get("pnl") or 0, 2)}
    return row, [t for t in out.get("trades", []) if t["strategy"] == name]


if __name__ == "__main__":
    names = [cls.name for cls in registry.BUILTIN]
    print(f"Running isolated backtests for {len(names)} strategies...", flush=True)

    rows, all_trades = [], []
    # Each run is a couple of minutes of pure computation and shares nothing
    # with the others, so they run side by side.
    with Pool(min(len(names), os.cpu_count() or 1)) as pool:
        for row, trades in pool.imap(measure, names):
            rows.append(row)
            all_trades.extend(trades)
            print(f"  {row['Strategy']:24s} {row['Trades']:4d} trades  "
                  f"{row['Win Rate %']:5.1f}% wins  {row['Expectancy (R)']:+.2f}R",
                  flush=True)

    report_path = os.path.join(DATA_DIR, "Isolated_Strategies_Report.csv")
    with open(report_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    if all_trades:
        write_trades(all_trades, "Isolated_All_Trades.csv")
    print(f"Wrote {len(rows)} rows to {report_path}")
