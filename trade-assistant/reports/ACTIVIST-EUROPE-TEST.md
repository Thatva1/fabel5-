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

**INCONCLUSIVE, by the pass mark above. It did not confirm the US finding, and it should stay switched off.**

Run on 2 October 2026, after the plan above was committed. 805 of the 811 instruments had prices (six London names timed out at Yahoo). 790 signals.

| | Signals | Average trade, before costs | After costs | Baseline | Ahead of baseline, month by month | t |
|---|---|---|---|---|---|---|
| **Whole period** | 790 | +0.19R | +0.15R | +0.08R | **+0.11R** | **1.3** |
| Before 12 April 2022 | 358 | +0.14R | +0.09R | +0.07R | +0.04R | 0.3 |
| From 12 April 2022 | 432 | +0.22R | +0.19R | +0.09R | +0.18R | 2.0 |
| UK only | 313 | +0.13R | +0.04R | +0.03R | +0.26R | 1.8 |
| Continent only | 477 | +0.22R | +0.22R | +0.11R | +0.07R | 0.7 |

Against the pass mark:

- Positive in both halves: **yes**.
- Positive after costs: **yes** (+0.15R).
- Ahead of the baseline with t of 2.0 or more: **no**. It is ahead by 0.11R with t = 1.3, which is between the fail line (1.0) and the pass line (2.0).

### What that means

**The effect shrank by more than half on data it had not seen.** In the US it was 0.26R a month ahead of the baseline. In Europe it is 0.11R ahead, and that is not distinguishable from zero. This is what a finding that was partly luck looks like: still pointing the same way, much smaller, and no longer statistically clear.

**The two regions disagree about when it worked.** In the US nearly all of the lead came before April 2022 (+0.48R, then +0.03R). In Europe it is the reverse (+0.04R, then +0.18R). A real effect would be expected to show up in the same periods in both places. An effect that moves around like this is more likely noise.

**In the UK, stamp duty takes most of it.** UK trades average +0.13R before costs and +0.04R after. The 0.5% duty on each purchase is about a tenth of the amount risked on the trade, and this rule's edge in the UK is about that size.

**As a portfolio it earned very little.** Scored the same way as the published US table (a concentrated book of up to 12 positions):

| | Activist proxy | Baseline | Buy and hold |
|---|---|---|---|
| United States | 11.3% a year | 10.2% | 22.9% |
| UK and Europe | **2.5% a year** | -0.5% | 15.1% |

European figures are realised results in each share's own currency. Holding the same 805 shares returned 15.1% a year.

### What to do with it

1. **Leave it switched off.** It did not pass.
2. **Do not tune it.** Trying a different RSI period or threshold until Europe passes would be a fifth idea, with the same problem the first four had.
3. **If you still want to know,** the only honest evidence left is time: let it log signals without trading them and look again after a year of data nobody had when the rule was written.
4. **The more useful lesson is about the method.** The best of four ideas looked like a discovery on one data set and faded on the next. Every strategy in the library was chosen on the same US decade, so each of them deserves the same test before it is trusted.

### Files

- `reports/universe-europe.csv` — the 811 instruments
- `reports/FULL-STUDY-EUROPE.json` — the run's own summary (under today's portfolio settings)
- `tools/activist_europe_test.py reports/FULL-STUDY-EUROPE-candidates.json` — reproduces the table above; the candidates file is 43 MB and is not committed
