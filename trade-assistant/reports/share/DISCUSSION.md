# Twelve quant strategies, one decade — results and open questions

*Research only. Nothing here was executed and nothing here is financial advice.*

This document is meant to be argued with. Every number below comes from the data
files shipped alongside it, and the parts I think are weakest are marked as such.

---

## 1. What was tested

| | |
|---|---|
| Period | 10y — 8.94 years of daily data |
| Instruments | 528 |
| — US equities | 500 |
| — FX pairs | 8 |
| — Futures | 20 |
| Strategies | 12, each with a published source |
| Signals generated | 63,014 |

Portfolio-level throughout: compounding equity, a gross exposure cap, limits on
open positions and per market, a correlation cap, and commission plus slippage
charged on both sides of every trade. Buy-and-hold is equal-weight, fully
invested, unlevered, on the **same** instruments.

Each strategy was given its own position slot during the replay, so its results
are its own rather than a by-product of losing a contention fight to a strategy
that fires more often.

---

## 2. Full results

Ranked by annual return. ▲ marks a strategy that beats buy-and-hold on Sharpe,
Sortino **and** Calmar.

| Strategy | Source | CAGR % | Total % | Vol % | DownDev % | MaxDD % | Ulcer | Sharpe | Sortino | Calmar | Gain/Pain | Win % | Trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **long_reversal** ▲ | De Bondt & Thaler 1985 | 22.54 | 246.4 | 14.78 | 9.75 | 22.89 | 7.06 | 1.45 | 2.20 | 0.98 | 1.29 | 44.9 | 388 |
| **ts_momentum** | Moskowitz, Ooi & Pedersen 2012 | 16.41 | 288.9 | 15.67 | 11.07 | 29.56 | 11.48 | 1.05 | 1.48 | 0.56 | 1.20 | 50.9 | 489 |
| **ALL_COMBINED** | — | 13.11 | 201.5 | 22.43 | 15.87 | 42.78 | 18.02 | 0.66 | 0.94 | 0.31 | 1.13 | 38.0 | 1404 |
| **dual_momentum** | Antonacci 2014 | 12.21 | 177.4 | 17.72 | 12.55 | 28.00 | 13.92 | 0.74 | 1.04 | 0.44 | 1.14 | 46.0 | 518 |
| **turn_of_month** | Ariel 1987 | 11.69 | 168.8 | 18.32 | 12.96 | 30.97 | 12.59 | 0.70 | 0.98 | 0.38 | 1.14 | 49.1 | 794 |
| **high_52w** | George & Hwang 2004 | 7.79 | 95.6 | 12.62 | 8.96 | 27.24 | 9.88 | 0.66 | 0.93 | 0.29 | 1.13 | 49.8 | 446 |
| **short_reversal** | Jegadeesh 1990 | 7.11 | 84.8 | 15.66 | 11.06 | 28.84 | 11.51 | 0.52 | 0.73 | 0.25 | 1.10 | 41.8 | 596 |
| **xs_momentum** | Jegadeesh & Titman 1993 | 7.10 | 83.6 | 15.63 | 11.16 | 32.70 | 17.54 | 0.52 | 0.72 | 0.22 | 1.10 | 44.5 | 422 |
| **band_reversion** | house rules | 5.89 | 67.0 | 18.12 | 12.73 | 39.04 | 16.91 | 0.41 | 0.58 | 0.15 | 1.08 | 32.2 | 1323 |
| **low_volatility** | Baker, Bradley & Wurgler 2011 | 4.19 | 44.4 | 5.96 | 4.28 | 11.28 | 4.76 | 0.72 | 1.00 | 0.37 | 1.14 | 50.0 | 428 |
| **low_beta** | Frazzini & Pedersen 2014 | 4.15 | 43.8 | 7.37 | 5.35 | 16.95 | 5.49 | 0.59 | 0.81 | 0.24 | 1.11 | 47.0 | 445 |
| **halloween** | Bouman & Jacobsen 2002 | 2.85 | 27.2 | 13.76 | 9.79 | 40.72 | 23.01 | 0.27 | 0.38 | 0.07 | 1.07 | 40.7 | 280 |
| **sector_momentum** | Moskowitz & Grinblatt 1999 | 2.49 | 24.3 | 4.82 | 3.52 | 7.45 | 3.09 | 0.53 | 0.73 | 0.33 | 1.11 | 47.8 | 157 |
| *Buy & hold — all 528* | equal weight | 23.13 | 700.5 | 23.24 | 16.47 | 37.76 | 11.81 | 1.01 | 1.43 | 0.61 | 1.20 | — | — |
| *Buy & hold — equities only* | equal weight | 23.75 | 741.6 | 23.82 | 16.88 | 38.48 | 12.05 | 1.01 | 1.43 | 0.62 | 1.20 | — | — |

**Reading the columns.** Ulcer index measures the depth *and* duration of
drawdowns, so two curves with the same maximum score differently if one stayed
down for years. Gain/pain is summed gains over summed losses on the equity
curve. Downside deviation is the volatility of losing periods only.
---

## 3. What the numbers say

### 3.1 One genuine risk-adjusted win

`long_reversal` returned **22.54%** a year against buy-and-hold's **23.13%** — slightly less — with a **22.89%** worst fall against **37.76%**. It beats holding on every risk-adjusted measure:

| | long_reversal | buy & hold |
|---|---|---|
| Sharpe | **1.45** | 1.01 |
| Sortino | **2.2** | 1.43 |
| Calmar | **0.98** | 0.61 |
| Ulcer (lower better) | **7.06** | 11.81 |

Roughly the same return for about 60% of the drawdown.

**The obvious objection, and I think it is right.** This strategy buys
three-year laggards. The decade tested was one where beaten-down names came
roaring back, which is precisely the trade it is built to make. 388 trades in
one favourable regime is not yet an edge.

### 3.2 Combining everything made it worse

Running all twelve together returned **13.11%** with a **42.78%** drawdown — the deepest in the study, and worse than four of its own components. Blending strong strategies with weak ones
diluted rather than diversified. This is the result I least expected and the
one I would most like a second opinion on.

### 3.3 The calmest books returned the least

`low_volatility` drew down just **11.28%** and `low_beta` **16.95%** — against buy-and-hold's 37.76% — for about 4% a year each. They behave exactly as
designed. The design is simply not competitive over this period.

### 3.4 Turnover bought nothing

`band_reversion` traded **1323** times for 5.89% a year and a 39.04% drawdown. `long_reversal` traded 388 times for 22.54%.
---

## 4. Bugs found while building this — and why they matter to the results

These are not housekeeping notes. Each one silently changed what the system
traded, and three of them made a whole market segment look bad when it had
simply never been given a chance.

### 4.1 The cross-market timezone bug

`yfinance` stamps every bar in the exchange's own timezone: a New York close is
midnight `America/New_York`, a London close is midnight `Europe/London`. Those
are different instants, so joining a US stock to a UK index matched **zero**
rows — not a few missing days, zero.

Consequence: `relative_strength` had been silently returning "no answer" for
every cross-market comparison since it was written. It failed quietly, so
nothing looked broken. Later the same root cause stopped a mixed-asset universe
from being *constructed at all*.

### 4.2 Ranking pooled market segments — the big one

Cross-sectional strategies rank instruments against each other. Pooling 500 US
shares with FX and futures means a currency pair is ranked against Nvidia.

Measured directly:

| | |
|---|---|
| Strongest non-equity instrument (silver futures, 12-month) | **+47.7%** |
| Where that ranks among a sample of 40 US stocks | **23rd** |
| Return needed to place top-12 among 500 stocks | **+183.5%** |

No currency pair or futures contract has ever moved like that. So six of the
twelve strategies were **structurally incapable of ever selecting one**. FX and
futures were in the universe, generating no trades, and the output looked
exactly like "tested and found wanting".

They now rank inside their own segment. A currency pair competes with currency
pairs, a bond future with bond futures.

### 4.3 A minimum universe size that excluded FX by arithmetic

Several strategies required at least 20 instruments to rank against. There are
only 8 major FX pairs in existence. FX could never qualify regardless of how it
behaved. Lowered to 5.

### 4.4 Every instrument counted as one "market"

`max_positions_per_market` was meant to spread the book across markets. The
function behind it returned `"US"` for Apple, for `EURUSD=X` and for a ten-year
note future alike — so a rule intended to diversify was capping the entire book
as a single bucket. In the large study it rejected **8,371** candidates. It now
buckets by asset class.

### 4.5 Slot allocation decided by list position

Every strategy targets a fixed multiple of its own stop, so reward-to-risk is
3.0, 3.0 and 2.5 **by construction** and discriminates almost nothing. The real
tie-break was the order instruments appeared in the universe list — which put
1,482 shares ahead of 46 macro instruments. A session produced **1,706
candidates across every segment and opened nine US equities**. Slots are now
offered to each segment in turn.

### 4.6 The same bet bought twice

With the above fixed, the book opened **spot sterling and the sterling future**
simultaneously — one position, two tickers, double the size. The correlation cap
should have stopped it. Measured:

| Pair | Correlation | Caught by an 0.8 cap? |
|---|---|---|
| SPY / ES=F | 0.98 | yes |
| GLD / GC=F | 0.93 | yes |
| TLT / ZB=F | 0.92 | yes |
| GBPUSD=X / 6B=F | **0.12** | **no** |
| EURUSD=X / 6E=F | **0.16** | **no** |
| USDJPY=X / 6J=F | **−0.03** | **no** |

Sterling spot and sterling futures are not 0.12 correlated. The FX bars close on
a different boundary from CME settlement, so the returns are computed over
offset windows and decorrelate even though the underlying is identical.

Identity is not a statistic. Same-exposure pairs are now asserted, not measured.

**This one has a further implication worth discussing:** if FX spot returns
decorrelate from their own futures, the FX spot series may be unreliable as a
signal input generally. That is unresolved.

### 4.7 Rate-limited data cached as though it were a finding

Screening the full 28,192-symbol US listing returned 537 "tradable" names with
NVDA, MSFT, SPY, QQQ, META and GOOGL all missing, and 88% of symbols marked "no
data" against 22% on a smaller sample. The data provider had rate-limited
partway through and whole batches came back empty — and the code cached the
wreckage as a valid universe.

The fix that mattered was not the retry logic. It was that a batch of 100 real
listings returning zero rows was *indistinguishable* from those hundred symbols
all being delisted. Failure looked exactly like a finding. There is now a
sanity check: a screen of the whole US market that loses SPY did not discover
anything, it failed.

Re-running with the over-the-counter tail excluded: 23 minutes, 2% no-data,
all reference names present.

---

## 5. Read this before quoting any number

1. **Everything is in-sample.** These rules were chosen knowing how the decade
   went. An out-of-sample split was run and returned a verdict per strategy —
   10 SURVIVED, 2 inconclusive — but a reporting bug (reading the wrong
   dictionary keys) meant the per-half figures failed to record. The verdicts
   are real; the detail behind them is missing and needs re-running. **This is
   the biggest open weakness in the whole document.**

2. **Survivorship bias.** Only instruments that still exist today can be
   tested. Companies that delisted or went bust are invisible. This flatters
   every row, including buy-and-hold.

3. **The futures are mis-sized.** All 20 are sized as though their full notional
   were paid in cash — no margin, no contract multiplier. Both return and risk
   are understated against a real futures account. Treat them as price-series
   research, not as a futures book.

4. **No options.** No usable historical options data reaches this stack. A
   proxy would be worse than the gap.

5. **One market, one decade.** US-listed instruments over roughly 2016–2026, a
   period dominated by one of the strongest equity bull runs on record.

6. **Modelled fills.** When a single bar's range covers both stop and target,
   the stop is always assumed — deliberately pessimistic. Slippage and
   commission are estimates.

---

## 6. What is not in the library, and why

Nine strategies from the original 25+ were not built. In each case the data
required would have produced numbers that look real and are not.

| Strategy | Blocker |
|---|---|
| Value, quality, profitability, QMJ, low-investment | Available fundamentals are point-in-time only; backtesting them injects look-ahead bias |
| Post-earnings drift, earnings premium | Data feed returns four quarters of earnings surprise, not the decade a test needs |
| Pairs / statistical arbitrage | Needs a two-instrument execution model the engine does not have |
| Carry | Needs futures term structure; only front-month series are available |
| Options strategies | No usable historical options data |

---

## 7. Questions I would put to the table

1. **Is `long_reversal` an edge or a regime?** It buys multi-year laggards over
   a decade that rewarded exactly that. What would falsify it?
2. **Why did combining twelve strategies produce the worst drawdown in the
   study?** Diversification running backwards is unusual enough to want a
   second explanation.
3. **Is a 22.9% drawdown for 22.5% a year actually preferable** to 37.8% for
   23.1%? That is a preference, not a fact, and it decides which line of the
   table matters.
4. **How much of the multi-asset result survives honest futures sizing?**
   Margin and multipliers would change both return and risk, in the same
   direction.
5. **What is the smallest number of strategies worth running?** Four beat the
   combination of all twelve.

---

## 8. The live paper book

Separate from the backtest: the rules trading forward from a standing start,
with nobody choosing which signals to take. It places no orders — there is no
broker anywhere in that code path, and a test parses every file in the package
to keep it that way.

Opened 2026-08-15 with 135,358 USD, converted once from the account's GBP at the rate on the day. The book runs in
USD from there, so its return measures the strategies rather than the strategies
plus the exchange rate.

Equity 135,344 USD · cash 13,782 · 7 positions

| Instrument | Segment | Strategy | Units | Entry | Value |
|---|---|---|---|---|---|
| `SPY` | equity_index | ts_momentum | 26 | 776.73 | 20,185 |
| `NVDA` | equity | ts_momentum | 65 | 225.27 | 14,635 |
| `LQD` | credit | high_52w | 191 | 106.17 | 20,269 |
| `BIL` | rates | ts_momentum | 221 | 91.58 | 20,228 |
| `USO` | commodity | ts_momentum | 91 | 126.66 | 11,521 |
| `GBPUSD=X` | fx | ts_momentum | 15,031 | 1.35 | 20,346 |
| `AAPL` | equity | ts_momentum | 47 | 306.08 | 14,379 |

**7 positions across 6 segments** — commodity, credit, equity, equity_index, fx, rates.

That spread is the whole point of section 4. Before those fixes the same run
produced 1,706 candidates across every segment and opened nine US equities.

The session that built this book blocked **5 candidates on duplicate exposure**
— the guard from section 4.6 doing its job. An earlier run of the same code
held `GBPUSD=X` *and* `6B=F` together; sterling now appears once.

---

## 9. The data, so you can check any of it

| File | What it is |
|---|---|
| `results.csv` | Every strategy against 21 metrics — the table in section 2 |
| `FULL-STUDY.json` | Complete raw study output, including the out-of-sample verdicts |
| `universe.csv` | The 528 instruments in the backtest, with asset class |
| `universe-live.csv` | The 1,528 instruments the live trader can now hold, with segment and exposure group |
| `strategies.csv` | All 12 strategies: source, regimes, and every tunable setting |
| `exposure-groups.csv` | The same-bet map from section 4.6 — which tickers are one exposure |
| `METHOD.md` | Method and caveats, standalone |
| `results.html` | The same results as a formatted page |

Everything is reproducible from the repository: `tools/full_study.py` runs the
backtest, `python run.py paper` runs one live session.

---

*Generated from the study output. Research and decision-support only — nothing
here was executed, and nothing here is financial advice. Past behaviour is not
predictive; a strategy that worked historically can stop working the moment
conditions change.*