# Backtest results — the full record

**Prepared 2026-08-17. Research only. Nothing in this document was executed, and
nothing in it is financial advice or an offer of any security.**

This is the document to hand to anyone who asks "show me the numbers". It
contains the results that flatter the project and the results that do not,
because a reader who finds the second kind on their own stops believing the
first kind.

Every figure is reproducible from the repository: `tools/full_study.py` runs the
study, and the raw output is in `reports/share/`.

---

## 1. What was tested

| | |
|---|---|
| Period | 8.94 years of daily bars (≈2016–2026) |
| Instruments | 528 — 500 US equities, 8 FX pairs, 20 futures |
| Strategies | 12, each implementing a published academic paper |
| Signals generated | 62,319 |
| Portfolio | £1,000,000, compounding |
| Costs | Commission and slippage charged on **both sides of every trade** |
| Benchmark | Equal-weight buy-and-hold of the **same 528 instruments** |

Portfolio-level throughout: a gross exposure cap, limits on open positions and
per asset class, a correlation cap, and compounding equity. Each strategy got its
own position slot, so its results are its own rather than a by-product of losing
a contention fight to a strategy that fires more often.

---

## 2. Headline result — stated plainly

**Buy-and-hold returned more than every strategy tested.**

| | Best strategy (`long_reversal`) | 4-strategy blend | Buy & hold |
|---|---|---|---|
| **CAGR** | 20.18% | 19.20% | **23.68%** |
| **Max drawdown** | **22.62%** | **23.67%** | 37.76% |
| **Sharpe** | **1.30** | **1.12** | 1.03 |
| **Sortino** | **1.96** | **1.64** | 1.46 |
| **Calmar** | **0.89** | **0.81** | 0.63 |
| Trades | 386 | 581 | — |

The honest summary in one sentence: **the strategies produced less money than
simply holding the same instruments, but lost noticeably less of it on the way
down.**

Whether that trade is worth making is a preference, not a fact. Roughly 3–4
points of annual return were given up to cut the worst peak-to-trough fall by
about 14 points. An investor who can sit through a 38% drawdown should prefer
buy-and-hold on this evidence. One who cannot, or who is levered, or who may
need the money at a bad moment, may not.

---

## 3. Every strategy, ranked

£1,000,000 portfolio, 8.94 years, Kelly sizing enabled.

| Strategy | Source | CAGR % | MaxDD % | Sharpe | Sortino | Calmar | Trades |
|---|---|---|---|---|---|---|---|
| *Buy & hold (equities only)* | equal weight | **23.75** | 38.48 | 1.01 | 1.43 | 0.62 | 500 |
| *Buy & hold (all 528)* | equal weight | **23.68** | 37.76 | 1.03 | 1.46 | 0.63 | 528 |
| `long_reversal` | De Bondt & Thaler 1985 | 20.18 | **22.62** | **1.30** | **1.96** | **0.89** | 386 |
| `ALL_COMBINED` | all twelve together | 19.37 | 40.31 | 0.88 | 1.27 | 0.48 | 1,405 |
| `BLEND` | best four, decorrelated | 19.20 | 23.67 | 1.12 | 1.64 | 0.81 | 581 |
| `ts_momentum` | Moskowitz, Ooi & Pedersen 2012 | 17.55 | 30.61 | 1.09 | 1.54 | 0.57 | 514 |
| `dual_momentum` | Antonacci 2014 | 14.07 | 28.94 | 0.82 | 1.16 | 0.49 | 522 |
| `turn_of_month` | Ariel 1987 | 11.16 | 28.72 | 0.66 | 0.93 | 0.39 | 798 |
| `short_reversal` | Jegadeesh 1990 | 9.14 | 25.13 | 0.63 | 0.91 | 0.36 | 596 |
| `high_52w` | George & Hwang 2004 | 8.28 | 27.80 | 0.68 | 0.95 | 0.30 | 452 |
| `xs_momentum` | Jegadeesh & Titman 1993 | 6.88 | 31.07 | 0.51 | 0.70 | 0.22 | 417 |
| `low_beta` | Frazzini & Pedersen 2014 | 6.81 | 14.40 | 0.80 | 1.14 | 0.47 | 438 |
| `band_reversion` | house rules | 6.34 | 39.56 | 0.43 | 0.61 | 0.16 | 1,318 |
| `low_volatility` | Baker, Bradley & Wurgler 2011 | 5.85 | 19.32 | 0.73 | 1.01 | 0.30 | 410 |
| `halloween` | Bouman & Jacobsen 2002 | 4.00 | 37.56 | 0.35 | 0.48 | 0.11 | 303 |
| `sector_momentum` | Moskowitz & Grinblatt 1999 | 2.52 | 7.44 | 0.54 | 0.74 | 0.34 | 157 |

Source: `reports/share/results-1m.csv`.

---

## 4. Three findings worth arguing with

### 4.1 One genuine risk-adjusted win

`long_reversal` earned about 3.5 points a year less than holding, with roughly
60% of the drawdown, and beats buy-and-hold on Sharpe, Sortino and Calmar
simultaneously.

**The obvious objection, and it is a good one.** This strategy buys three-year
laggards. The decade tested was one in which beaten-down names came roaring
back — precisely the trade it is built to make. **386 trades in one favourable
regime is not an edge yet.** It is a hypothesis with supporting evidence.

### 4.2 Combining everything made it worse

Running all twelve strategies together returned 19.37% with a **40.31%
drawdown** — the deepest in the study, worse than four of its own components.
Blending strong strategies with weak ones diluted rather than diversified.

Selecting four decorrelated strategies (`BLEND`) produced nearly the same return
for a 23.67% drawdown — 17 points shallower than running all twelve. **Fewer,
less correlated strategies beat more strategies.**

### 4.3 Turnover bought nothing

`band_reversion` traded 1,318 times for 6.34% a year and a 39.56% drawdown.
`long_reversal` traded 386 times for 20.18%. Trading more was not trading better.

---

## 5. Limitations — read this before quoting any number above

These are not boilerplate. Each one could change the conclusions.

1. **The out-of-sample test did not work as intended.** A split at 2022-06-27
   returned "SURVIVED" for 10 of 12 strategies. But **every single strategy
   scored better out-of-sample than in-sample**, which is backwards —
   overfitting produces the opposite. The out-of-sample half begins near the
   2022 market bottom, so everything long made money in it. This is a **regime
   split wearing an out-of-sample label**, and no strategy here has yet been
   tested against a period where its own style was out of favour. *This is the
   single biggest weakness in the entire study.*

2. **Survivorship bias.** Only instruments that still exist today can be tested.
   Companies that delisted or went bust are invisible. This flatters every row —
   including buy-and-hold.

3. **The futures are mis-sized.** All 20 are modelled as though their full
   notional were paid in cash, with no margin and no contract multiplier. Both
   return and risk are understated versus a real futures account. Treat them as
   price-series research, not as a futures book.

4. **One market, one decade.** US-listed instruments over roughly 2016–2026 — a
   period dominated by one of the strongest equity bull runs on record.

5. **No options.** No usable historical options data reaches this system. A
   proxy would be worse than the gap, so there is no hedging sleeve.

6. **Modelled fills.** Where a single bar's range covers both stop and target,
   the stop is always assumed — deliberately pessimistic. Slippage and
   commission are estimates, not fills.

7. **Live forward testing is days old, not years.** The paper book began
   2026-08-15 and has recorded **zero closed trades**. It has no track record
   yet and should not be presented as one.

---

## 6. What the live system has actually done

Separate from the backtest: the rules trading forward from a standing start,
with nobody choosing which signals to take. It places no orders — there is no
broker anywhere in that code path, and a test parses every file in the package
to keep it that way.

| | |
|---|---|
| Started | 2026-08-15 (reset 2026-08-17 for a sizing change) |
| Positions | 24, across equities, credit, rates, commodities and futures |
| Closed trades | **0** |
| Data | 100% licensed IBKR, verified per position |
| Instruments priceable on licensed data | 90 of 98 on the watchlist |

**This is infrastructure evidence, not performance evidence.** It demonstrates
the system runs, prices honestly and respects its risk limits. It says nothing
yet about whether it makes money.

---

## 7. Bugs found and fixed — why this section exists

Eight material bugs were found and corrected during development. They are listed
because *the ability to find them* is the substantive claim this project can
make, and because each one silently changed results before it was caught.

| Bug | Effect on results | Found by |
|---|---|---|
| ~2× accidental leverage | Every headline return inflated | **The founder** |
| Drawdown ÷ final peak, not running peak | All drawdowns understated | **The founder** |
| Cross-market timezone mismatch | Relative strength silently returned nothing for years | Code review |
| Ranking pooled asset classes | 6 of 12 strategies structurally could never pick FX or futures | Code review |
| Every instrument counted as one "market" | Diversification rule rejected 8,371 candidates | Code review |
| Rate-limited data cached as a finding | A universe scan that lost SPY was stored as valid | Code review |
| Same bet bought twice (spot FX + FX future) | One position, two tickers, double size | Code review |
| Entry drift collapsing the R denominator | Phantom +183R trades | Code review |

Five of the project's own hypotheses were tested and **killed** by its own
evidence — including "costs destroy the edge" and "we win on risk-adjusted
return" as originally stated.

---

## 8. Where to verify every number

| File | Contents |
|---|---|
| `reports/share/results-1m.csv` | Every strategy against 21 metrics — the table in §3 |
| `reports/share/FULL-STUDY-1m.json` | Complete raw study output |
| `reports/share/RESULTS-1M.md` | In-sample vs out-of-sample analysis |
| `reports/share/DISCUSSION.md` | Full methodology, bugs and open questions |
| `reports/share/universe.csv` | The 528 instruments tested |
| `reports/share/strategies.csv` | All 12 strategies: source, regimes, settings |
| `reports/STRATEGY-CORRELATION.csv` | Cross-strategy correlation matrix |

---

*Past behaviour is not predictive. A strategy that worked historically can stop
working the moment conditions change. This document describes research software
and is not an offer, solicitation, or recommendation to buy or sell any
security.*
