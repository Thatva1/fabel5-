# 34 Strategies Backtest Results (Client CSV Dataset)

> **Correction, 1 October 2026 — read before quoting anything below.**
>
> - **This is a result for 4 strategies, not 34.** Only the four listed at the
>   bottom placed a trade. The four daily strategies added in September crashed
>   on every signal, nine intraday ones were empty, and the others placed no
>   trade on ten instruments. `Isolated_Strategies_Report.csv` has the corrected
>   per-strategy figures, with the September strategies now running.
> - **The "GBP" total is not pounds.** The run did no currency conversion, so it
>   adds dollars (the US shares, bonds, gold, oil), pence (AZN, HSBA) and
>   currency-pair units into one number. The R figures and win rates are valid;
>   the money figure is not.
> - **Ten instruments, chosen by hand, in one rising decade.** It shows the
>   engine runs on this data. It is not evidence of an edge.
>
> See `AUDIT-2026-10-01.md` in the project root.

**Universe:** 10 highly liquid core instruments spanning 5 asset classes (loaded locally from your CSV export).
**Period:** 10 Years

### Overall Performance
- **Total Trades:** 517
- **Win Rate:** 44.1% (228W / 289L / 0S)
- **Expectancy:** 0.14R
- **Profit Factor:** 1.28
- **Net P&L:** 427,885.71 GBP

### Strategy Allocation (Who 'won' the capital)
- **ts_momentum**: 372 trades, 47.6% win rate, 0.25R expectancy, P&L: £481,526
- **low_beta**: 67 trades, 40.3% win rate, 0.13R expectancy, P&L: £8,949
- **high_52w**: 53 trades, 41.5% win rate, -0.05R expectancy, P&L: -£1,021
- **band_reversion**: 25 trades, 8.0% win rate, -1.01R expectancy, P&L: -£61,568
