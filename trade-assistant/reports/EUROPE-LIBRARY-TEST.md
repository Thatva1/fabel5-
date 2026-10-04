# The whole strategy library on the UK and European universe

*Research only. Nothing here was executed and nothing here is financial advice.*

## The plan, written before the run

**Committed on 4 October 2026 before any result from this run existed.** Every strategy in the library was chosen and tuned on one US decade. Europe is data none of them has seen. This is the same test the activist proxy was given (`ACTIVIST-EUROPE-TEST.md`), applied to the rest of the library at once.

| | |
|---|---|
| **Strategies** | All sixteen, exactly as configured today. No parameter changed. The four September proxies are switched on for the run only. |
| **Universe** | The same 811 European operating companies as the activist test (`reports/universe-europe.csv`). |
| **Period** | Ten years of daily bars. |
| **Ranking** | Strategies that rank an instrument against others rank it against the whole European list, across all fourteen exchanges, on returns in each share's own currency. |
| **Baseline** | `macro_sector_rotation` in the same run: a long in anything while the STOXX Europe 600 is in an uptrend. |
| **Measure** | Per strategy, its average trade in each calendar month minus the baseline's in the same month, in multiples of the amount risked (R). |
| **Split** | 12 April 2022. |
| **Scored by** | `tools/region_test.py`, committed with this plan. |

### The pass mark, per strategy

- **PASS** — ahead of the baseline with t of 2.0 or more, ahead in both halves, and the average trade positive after costs.
- **FAIL** — not ahead of the baseline, **or** t below 1.0, **or** the average trade loses money after costs.
- **INCONCLUSIVE** — anything in between, or fewer than 200 signals.
- **UNTESTABLE** — no signals at all.

Fourteen strategies tested at once means some may clear t = 2.0 by luck; at this bar the expected number of lucky passes across fourteen is about a third of one. A PASS is a reason to look harder, not a reason to trade.

### Expected in advance

- `sector_momentum` only ever holds the eleven US sector funds, so it has nothing to trade here and will be **UNTESTABLE**.
- UK stamp duty (0.5% on each purchase) costs roughly a tenth of the amount risked per trade, so UK results after costs will sit well below the rest.

### The same scorer on the US study, for reference

This is in-sample: these are the data the strategies were chosen on, so they should look their best here.

| Strategy | US verdict | Signals | Lead over baseline | t |
|---|---|---|---|---|
| `activist_13d_tracking` | PASS | 411 | +0.26R | 2.7 |
| `band_reversion` | PASS | 3,189 | +0.21R | 2.6 |
| `ts_momentum` | PASS | 20,780 | +0.06R | 2.4 |
| `dual_momentum` | PASS | 2,701 | +0.14R | 2.1 |
| `high_52w` | PASS | 10,350 | +0.09R | 2.1 |
| `halloween` | PASS | 3,768 | +0.17R | 2.1 |
| `long_reversal` | PASS | 1,785 | +0.17R | 2.0 |
| `low_volatility` | INCONCLUSIVE | 2,322 | +0.10R | 1.5 |
| `low_beta` | INCONCLUSIVE | 8,244 | +0.04R | 1.1 |
| `sector_momentum` | INCONCLUSIVE | 157 | +0.03R | 0.3 |
| `xs_momentum` | FAIL | 1,059 | +0.03R | 0.5 |
| `short_reversal` | FAIL | 3,186 | +0.03R | 0.4 |
| `turn_of_month` | FAIL | 11,588 | -0.02R | -0.4 |
| `pead_drift` | FAIL | 2,604 | -0.00R | -0.1 |
| `qmj_factor` | FAIL | 18,211 | -0.01R | -0.4 |

Seven pass in the US. The question Europe answers is how many of the seven survive on data they were not chosen on.

### Known limits, accepted in advance

- Today's list of companies, so survivorship bias applies.
- Returns are in each share's own currency; nothing is converted.
- French and Italian transaction taxes are not modelled; UK stamp duty is.
- Prices come from Yahoo throughout.
- The activist proxy is in this run too. Its result here is for the table only; its verdict was settled by its own committed test.

---

## The result

Run on 4 October 2026, after the plan above was committed. All 811 instruments had prices. 53,758 baseline signals; the baseline's average trade earned +0.08R before costs, about half what it earned in the US.

### Verdicts

| Strategy | US (in-sample) | **Europe** | Signals | Lead over baseline | t | After costs, per trade |
|---|---|---|---|---|---|---|
| `band_reversion` | PASS | **PASS** | 5,358 | +0.21R | 3.3 | +0.01R |
| `halloween` | PASS | **PASS** | 6,441 | +0.13R | 3.1 | +0.16R |
| `low_volatility` | inconclusive | **PASS** | 3,719 | +0.14R | 3.0 | +0.11R |
| `high_52w` | PASS | **PASS** | 13,057 | +0.09R | 3.0 | +0.08R |
| `ts_momentum` | PASS | **PASS** | 28,448 | +0.09R | 2.7 | +0.09R |
| `dual_momentum` | PASS | **PASS** | 4,851 | +0.10R | 2.4 | +0.08R |
| `low_beta` | inconclusive | inconclusive | 11,320 | +0.08R | 1.995 | +0.07R |
| `pead_drift` | FAIL | inconclusive | 6,999 | +0.07R | 1.9 | +0.09R |
| `long_reversal` | PASS | **inconclusive** | 2,954 | +0.11R | 1.7 | +0.11R |
| `short_reversal` | FAIL | inconclusive | 5,095 | +0.08R | 1.5 | +0.03R |
| `turn_of_month` | FAIL | inconclusive | 18,917 | +0.05R | 1.3 | +0.09R |
| `activist_13d_tracking` | PASS | inconclusive | 793 | +0.11R | 1.3 | +0.15R |
| `xs_momentum` | FAIL | FAIL | 1,155 | +0.03R | 0.7 | +0.11R |
| `qmj_factor` | FAIL | FAIL | 27,537 | +0.02R | 0.7 | +0.07R |
| `sector_momentum` | inconclusive | untestable | 0 | — | — | — |

`low_beta` missed the pass line by 0.005 of a t. The line was fixed in advance, so it stays inconclusive.

### What it says

**Five of the seven US passes survived.** Time-series momentum, dual momentum, the 52-week high, Halloween and band reversion all beat the baseline on data they were not chosen on, in both halves. Fourteen tests would be expected to produce about a third of one lucky pass, so six passes is real signal, not noise. Low volatility, inconclusive in the US, passed here.

**Two did not.** `long_reversal`, the strongest strategy in the US study and first in the book's claim order, is ahead in Europe but not clearly (t = 1.7). The activist proxy came out as its own test did (t = 1.3).

**Passing is not the same as being worth trading.** The test asks whether a strategy picks better trades than buying anything in an uptrend. It does not ask whether those trades make money:

- `band_reversion` passes most strongly because it buys sharp falls, which happen in the months when everything else is losing. Its trades lose less than the baseline's in those months. After costs it makes +0.01R a trade, and as a book it lost money: -11% a year in a concentrated book, -0.6% at today's settings.
- After costs the passing strategies earn between +0.08R and +0.16R per trade. These are small edges.

### As portfolios

Each strategy as a concentrated book of up to 12 positions (the published study's settings), with results realised in each share's own currency:

| Strategy | Return a year | Worst realised drawdown |
|---|---|---|
| *Buy and hold the same 811 shares* | *15.1%* | *36.9%* |
| `pead_drift` | 7.8% | 30.1% |
| `long_reversal` | 6.2% | 20.9% |
| `xs_momentum` | 5.3% | 19.6% |
| `ts_momentum` | 5.3% | 35.5% |
| `halloween` | 5.1% | 16.0% |
| `qmj_factor` | 2.4% | 34.7% |
| `high_52w` | 1.9% | 19.9% |
| `activist_13d_tracking` | 1.8% | 20.0% |
| `dual_momentum` | 0.4% | 41.7% |
| `low_beta` | 0.3% | 25.5% |
| `turn_of_month` | 0.2% | 28.4% |
| `low_volatility` | -1.3% | 18.1% |
| `short_reversal` | -2.4% | 33.1% |
| `macro_sector_rotation` (baseline) | -4.2% | 37.3% |
| `band_reversion` | -11.4% | 74.0% |

Read this table with care. The verdicts above use every signal a strategy produced. A 12-position book uses a handful of them, and which handful is decided by a tie-break: the order of the universe list, which here is London first, then by market capitalisation. The US study showed that changing the tie-break alone can halve a result. These book returns describe this construction more than the rules.

What the table does show is the same thing the US study showed: **no strategy, built into a book this way, comes near simply holding the shares.**

### What to do with it

1. **The signals that held up in two regions are the momentum family (`ts_momentum`, `dual_momentum`, `high_52w`), `low_volatility` and `halloween`.** That is better evidence than anything else in the library has.
2. **`long_reversal` rests on US evidence alone.** It is first in `strategy_priority`, which gives it first claim on every instrument. Consider whether it should keep that place on evidence from one region.
3. **`band_reversion` should not be traded despite passing.** Its edge is in losing less, not in making money.
4. **The return is being lost in portfolio construction, not in the signals.** In both regions several signals pick better than chance, and in both regions the books made from them earn a fraction of holding. How much of the book is invested, how positions are sized and how ties are broken is now the larger question than which signals to add.

### Files

- `reports/FULL-STUDY-EUROPE-LIBRARY.json` — the run's own summary, at today's portfolio settings
- `reports/FULL-STUDY-EUROPE-LIBRARY-published-settings.json` — the concentrated-book table above
- `python tools/region_test.py reports/FULL-STUDY-EUROPE-LIBRARY-candidates.json` — reproduces the verdicts; the candidates file is 153 MB and is not committed
