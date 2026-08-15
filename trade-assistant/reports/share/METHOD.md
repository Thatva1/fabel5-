# Method and caveats

Period: 10y (8.94 years of data)
Universe: 528 instruments — 500 US equities,
8 FX pairs, 20 futures.
Candidates generated: 63,014

## How it was run
One historical replay, bar by bar. At each bar the strategies see only bars up
to that point — the slice handed to them physically cannot contain the future.
Each strategy got its own position slot so they never competed for the same
instrument, meaning each strategy's numbers are its own.

Results are portfolio-level: compounding equity, a gross exposure cap, a limit
on open positions, a per-market cap and a correlation cap. Costs (commission,
slippage) are charged on both sides. Buy-and-hold is equal-weight, fully
invested, unlevered, on the SAME universe.

## Caveats — read these before quoting any number

1. IN-SAMPLE. These rules were selected knowing how this decade went. An
   out-of-sample split was run and reported a verdict per strategy, but the
   underlying develop/validate figures failed to extract due to a reporting bug
   (wrong dictionary keys). The verdicts are real; the detail is missing.

2. SURVIVORSHIP BIAS. Only instruments that still exist today can be tested.
   Companies that delisted or went bust are invisible, which flatters every
   result including buy-and-hold.

3. FUTURES ARE MIS-SIZED. 20 futures in this run are sized as if their full notional were paid in cash, with no margin and no contract multiplier. Returns and risk are both understated against a real futures account; treat these as price-series research, not as a futures book.

4. NO INTRABAR DATA. When a bar's range covers both the stop and the target,
   the stop is always assumed. Pessimistic, deliberately.

5. TECHNICALS ONLY. No news, fundamentals or earnings were used.

6. ONE MARKET, ONE DECADE. US-listed instruments, roughly 2016-2026, a period
   dominated by one of the strongest equity bull runs on record.

Research only. Not financial advice.
