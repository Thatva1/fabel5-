# The backtest of the book the paper trader runs

*4 October 2026. Research only. Nothing here was executed and nothing here is financial advice.*

Until today the backtest described a different book from the paper trader's: at most about a quarter invested, a correlation cap that never ran, and each strategy acting on its own monthly or weekly calendar. Those are fixed (`config.yaml`, portfolio section). This is the first full run with the paper book's rules.

528 US-listed instruments, about nine years, the twelve strategies switched on, every strategy acting whenever the book rebalances, 93,394 signals.

## The book

| | Return a year | Worst fall | Sharpe | Trades |
|---|---|---|---|---|
| **The paper book's rules** | **15.6%** | **25.9%** | **0.99** | 9,363 |
| First half (to April 2022) | 12.1% | 25.9% | 0.81 | 4,744 |
| Second half | 16.7% | 19.8% | 1.03 | 4,739 |
| *Buy and hold the same 528* | *23.1%* | *37.3%* | *1.02* | — |
| Before today: 25-per-market cap, no correlation cap | 8.3% | 16.1% | 0.96 | 8,381 |

The book now earns about two-thirds of what holding earned, at the same return per unit of risk and with a worst fall about a third smaller. The jump from 8.3% is mostly the book being invested at all: the old per-market cap kept three-quarters of it in cash.

## What fills it

Share of capital over time: `ts_momentum` 20%, `high_52w` 18%, then `short_reversal`, `halloween`, `dual_momentum` and `long_reversal` at about 10% each. No strategy reaches the 25% cap for long.

## What refused candidates

| Reason | Candidates refused |
|---|---|
| The book was already full | 42,831 |
| Moved too closely with something held (correlation 0.8) | 20,105 |
| Already held | 16,274 |
| The same exposure under another name | 1,180 |
| The strategy had used its share | 888 |

The correlation cap is now the second-busiest rule in the book. Before today it refused nothing in any backtest, because it was never given the prices it needs.

## Each strategy alone, on the paper book's calendar

| | Return a year | Worst fall | Sharpe |
|---|---|---|---|
| `ts_momentum` | 14.5% | 22.1% | 0.96 |
| `halloween` | 11.8% | 29.0% | 0.73 |
| `long_reversal` | 11.1% | 8.9% | **1.44** |
| `short_reversal` | 10.0% | 26.9% | 0.75 |
| `turn_of_month` | 8.9% | 20.4% | 0.71 |
| `dual_momentum` | 8.0% | 14.5% | 0.74 |
| `high_52w` | 7.8% | 16.1% | 0.79 |
| `low_beta` | 4.9% | 11.3% | 0.77 |
| `xs_momentum` | 3.0% | 8.0% | 0.67 |
| `band_reversion` | 1.4% | 6.4% | 0.45 |
| `low_volatility` | 1.3% | 3.0% | 0.69 |
| `sector_momentum` | 0.2% | 0.8% | 0.56 |

These are not the published strategies: every rule here acts at every rebalance rather than on its own monthly or weekly date, because that is how the paper book runs them. For the rules as published, set `backtest.strategy_calendar: strategy`.

## The one difference kept

The paper book fills a new position at the close of the bar its signal came from; the backtest fills at the next open. Nobody can trade at a close they only learn once it has printed, so the backtest does not copy that. Changing the paper book to fill at the next open would close the last gap.

## Reproduce

```
STUDY_UNIVERSE=reports/share/universe.csv STUDY_TAG=OCT04-LIVE \
STUDY_FEED=yfinance STUDY_WORKERS=8 python tools/full_study.py
```

`reports/FULL-STUDY-OCT04-LIVE.json`; halves and mix in `reports/LIVE-BOOK-BACKTEST-halves.json`. The halves were re-scored on a separate price download, which is why the whole-period figure there (15.3%) differs slightly from the run's own.
