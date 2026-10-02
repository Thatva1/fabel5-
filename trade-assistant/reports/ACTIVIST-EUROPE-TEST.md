# The activist proxy on the UK and European universe

*Research only. Nothing here was executed and nothing here is financial advice.*

## The plan, written before the run

**Committed on 2 October 2026 before any European result existed.** The US study tried four ideas and `activist_13d_tracking` came out best, which is how false discoveries are made. One test on data it has not seen, with everything fixed in advance, is what tells a finding from a fluke.

| | |
|---|---|
| **Rule** | `activist_13d_tracking` exactly as shipped: buy when the 14-day RSI goes from below 40 to above 60 in one session. No parameter changed. |
| **Universe** | 811 European operating companies: 262 on the London Stock Exchange and 549 on thirteen continental exchanges. Built by fixed rules in `tools/build_europe_universe.py` and saved as `reports/universe-europe.csv`. |
| **Period** | Ten years of daily bars, the same span as the US study. |
| **Baseline** | A long in any of the same instruments while the STOXX Europe 600 is in an uptrend. It is what an unselective long with a stop earned; a rule has to beat it to show it selects anything. |
| **Measure** | Result per trade as a multiple of the amount risked (R), averaged within each calendar month, then the rule's month minus the baseline's month. |
| **Split** | 12 April 2022, the date the US study split on. |

### The pass mark

- **PASS** — the monthly difference is positive with t of 2.0 or more, **and** positive in both halves, **and** the average trade is positive after costs.
- **FAIL** — the difference is zero or negative, **or** t is below 1.0, **or** the average trade loses money after costs.
- **INCONCLUSIVE** — anything in between, or fewer than 200 signals.

Costs are commission both ways and UK stamp duty of 0.5% on purchases. On a stop two ATRs away, stamp duty alone is roughly a tenth of the amount risked, so the UK starts each trade behind.

### What the same scoring says about the US result

Run on the US study's trades, this script gives: 411 signals, +0.26R a month ahead of the baseline, t = 2.7 over 94 months. By half: +0.48R (t = 3.4) before April 2022 and **+0.03R (t = 0.3) after**. So the US result passes the mark above only narrowly; nearly all of it comes from the first half. Europe is being asked whether the rule works, and also whether the first half was the exception.

### Known limits, accepted in advance

- Today's list of companies, so survivorship bias applies as it does to the US study.
- Returns are in each share's own currency. Nothing is converted, so this measures the rule and not the exchange rate.
- French and Italian transaction taxes are not modelled; UK stamp duty is.
- Prices come from Yahoo throughout.

---

## The result

*To be filled in after the run.*
