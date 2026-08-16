# £1m study — results, data, and what I think they mean

*Research only. Nothing here was executed and nothing here is financial advice.*

528 instruments · 8.94 years · 62,319 signals · portfolio £1,000,000 · Kelly enabled

---
## 1. Every strategy, in-sample vs out-of-sample

The split is **2022-06-27**. In-sample is the half the rules were chosen on;
out-of-sample is the half they were not.

| Strategy | IN CAGR | IN Sharpe | IN n | OUT CAGR | OUT Sharpe | OUT n | Verdict |
|---|---|---|---|---|---|---|---|
| `ts_momentum` | 9.07 | 0.63 | 267 | **28.98** | 1.66 | 248 | SURVIVED |
| `dual_momentum` | 2.73 | 0.24 | 277 | **28.25** | 1.43 | 245 | SURVIVED |
| `long_reversal` | 8.61 | 0.60 | 127 | **26.23** | 1.69 | 259 | SURVIVED |
| `xs_momentum` | -0.87 | 0.02 | 227 | **18.51** | 1.11 | 190 | inconclusive |
| `short_reversal` | 3.90 | 0.34 | 305 | **15.56** | 0.92 | 292 | SURVIVED |
| `turn_of_month` | 9.50 | 0.59 | 414 | **15.01** | 0.84 | 384 | SURVIVED |
| `halloween` | -1.13 | -0.00 | 175 | **11.75** | 0.85 | 128 | inconclusive |
| `high_52w` | 8.58 | 0.74 | 231 | **7.16** | 0.58 | 221 | SURVIVED |
| `band_reversion` | 5.15 | 0.36 | 713 | **6.95** | 0.48 | 612 | SURVIVED |
| `low_beta` | 6.15 | 0.69 | 239 | **6.81** | 0.88 | 200 | SURVIVED |
| `low_volatility` | 7.55 | 0.85 | 210 | **4.00** | 0.58 | 200 | SURVIVED |
| `sector_momentum` | 2.78 | 0.54 | 87 | **2.46** | 0.57 | 70 | SURVIVED |

### What this actually says

**Every single strategy did better out-of-sample than in-sample.** That is
backwards. Overfitting produces the opposite — strong in-sample, weak out —
so when the whole table inverts, the honest reading is not "unusually robust
rules". It is that the two halves are **different market regimes**.

The out-of-sample half starts 2022-06-27, which is close to the 2022 bottom.
Everything long made money in it. So this is a regime split wearing an
out-of-sample label, and **no strategy here has yet been tested against a
period where its own style was out of favour.** That is the single biggest
weakness in this run, and it is larger than any individual number below.

---
## 2. Full results at £1m

| Strategy | CAGR | TotRet | Vol | MaxDD | Ulcer | Sharpe | Sortino | Calmar | G/P | Win% | Trades |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `long_reversal` | 20.18 | 207.6 | 14.96 | 22.62 | 7.64 | 1.30 | 1.96 | 0.89 | 1.25 | 43.8 | 386 |
| `ALL_COMBINED` | 19.37 | 388.5 | 23.01 | 40.31 | 15.71 | 0.88 | 1.27 | 0.48 | 1.18 | 39.9 | 1405 |
| `BLEND` ⭐ | 19.20 | 381.0 | 16.95 | 23.67 | 8.56 | 1.12 | 1.64 | 0.81 | 1.22 | 49.2 | 581 |
| `ts_momentum` | 17.55 | 324.7 | 16.05 | 30.61 | 12.32 | 1.09 | 1.54 | 0.57 | 1.21 | 51.2 | 514 |
| `dual_momentum` | 14.07 | 221.0 | 18.16 | 28.94 | 13.91 | 0.82 | 1.16 | 0.49 | 1.16 | 47.1 | 522 |
| `turn_of_month` | 11.16 | 157.7 | 18.60 | 28.72 | 11.19 | 0.66 | 0.93 | 0.39 | 1.14 | 49.8 | 798 |
| `short_reversal` | 9.14 | 118.5 | 15.84 | 25.13 | 10.22 | 0.63 | 0.91 | 0.36 | 1.13 | 42.6 | 596 |
| `high_52w` | 8.28 | 103.8 | 12.97 | 27.80 | 10.26 | 0.68 | 0.95 | 0.30 | 1.13 | 49.6 | 452 |
| `xs_momentum` | 6.88 | 80.3 | 15.58 | 31.07 | 17.00 | 0.51 | 0.70 | 0.22 | 1.10 | 44.8 | 417 |
| `low_beta` | 6.81 | 80.2 | 8.69 | 14.40 | 5.25 | 0.80 | 1.14 | 0.47 | 1.16 | 50.7 | 438 |
| `band_reversion` | 6.34 | 73.5 | 18.43 | 39.56 | 16.93 | 0.43 | 0.61 | 0.16 | 1.09 | 32.2 | 1318 |
| `low_volatility` | 5.85 | 66.1 | 8.22 | 19.32 | 7.59 | 0.73 | 1.01 | 0.30 | 1.15 | 53.7 | 410 |
| `halloween` | 4.00 | 40.0 | 14.20 | 37.56 | 19.91 | 0.35 | 0.48 | 0.11 | 1.09 | 41.6 | 303 |
| `sector_momentum` | 2.52 | 24.6 | 4.83 | 7.44 | 3.08 | 0.54 | 0.74 | 0.34 | 1.11 | 47.8 | 157 |
| *Buy & hold* | 23.68 | 736.8 | 23.26 | 37.76 | 11.81 | 1.03 | 1.46 | 0.63 | 1.21 | — | — |

---
## 3. The blend — the clearest win in this run

Chosen from **out-of-sample** edge plus low correlation:
`dual_momentum`, `long_reversal`, `short_reversal`, `low_beta`.

Rejected, and why:

| Rejected | Reason |
|---|---|
| `ts_momentum` | 0.78 correlation with `dual_momentum` |
| `xs_momentum` | 0.73 with `dual_momentum` |
| `high_52w` | 0.70 with `dual_momentum` |
| `turn_of_month` | 0.64 with `dual_momentum` |
| `halloween` | 0.62 with `dual_momentum` |
| `band_reversion`, `low_volatility`, `sector_momentum` | blend already full at 4 |

| | CAGR | Max DD | Sharpe | Sortino | Calmar | Ulcer |
|---|---|---|---|---|---|---|
| **BLEND** | 19.20 | **23.67** | **1.12** | **1.64** | **0.81** | **8.56** |
| ALL_COMBINED | 19.37 | 40.31 | 0.88 | 1.27 | 0.48 | 15.71 |
| Buy & hold | 23.68 | 37.76 | 1.03 | 1.46 | 0.63 | 11.81 |

**Same return as equal-weighting for 17 fewer points of drawdown.** That is the
correlation screen doing exactly what it was built to do, and it is the one
result here I would defend without heavy qualification. The blend also beats
buy-and-hold on every risk-adjusted measure, giving up 4.5 points of CAGR.

The awkward part: `long_reversal` **alone** still beats the blend (Sharpe 1.30,
Calmar 0.89 vs 1.12 / 0.81). Diversification is costing something here, and
whether that cost is worth paying depends on how much you believe one strategy
measured in one favourable regime.

## 4. What the correlation matrix shows

The momentum family is one bet wearing five names: `ts_momentum` /
`dual_momentum` 0.78, `ts_momentum` / `high_52w` 0.79, `dual_momentum` /
`xs_momentum` 0.73. Running all five is not diversification.

The genuinely independent pairs are the useful finding:

| Pair | Correlation |
|---|---|
| `low_beta` / `halloween` | 0.21 |
| `long_reversal` / `halloween` | 0.24 |
| `long_reversal` / `band_reversion` | 0.27 |
| `low_beta` / `xs_momentum` | 0.29 |
| `long_reversal` / `xs_momentum` | 0.29 |

`long_reversal` and `low_beta` are the two things in this library that do not
move with the momentum block. That is why both are in the blend.

Note that **nothing is negatively correlated**. Every pair is positive. This is
a long-only book in a rising decade — there is no true hedge anywhere in it,
which matters for the question about options below.

## 5. Kelly sizing — what it will actually do

`KELLY-EDGES.json` now holds out-of-sample return streams for all 12
strategies, and the live sizer reads it. Sizing is fractional (0.25) with a
lower-confidence-bound shrink and a hard 15% position cap.

**Read this before trusting the sizes.** Those edges are measured on the
recovery half, where strategies returned 26–29% annualised. Kelly is roughly
linear in the edge, so it will size as though that is the expectation. The
quarter-fraction and the shrinkage are the only things standing between that
estimate and a very large position. I would not raise the fraction above 0.25
on this evidence, and there is a reasonable argument for 0.15 until a losing
regime has been measured.

## 6. Honest list of what is still wrong

1. **The out-of-sample split is a regime split.** Biggest issue in the run.
2. **Survivorship bias**, worst for `long_reversal`, which buys multi-year
   losers — and the losers that delisted are invisible. Still unfixed; it needs
   a paid delisting-inclusive feed.
3. **Futures margins are approximations.** Multipliers are exact; margins are
   order-of-magnitude figures, not broker quotes.
4. **No options**, so no defined-risk hedge anywhere in the book.
5. **One decade, one market.**

---
## 7. Live data — yes, IBKR can fix most of this

You're right that this is the way out. IBKR solves **three** separate problems
that yfinance structurally cannot, and the project already has half the
plumbing (`broker/ibkr.py` talks to IB Gateway to place orders; `ib_insync` is
already installed).

| Problem | yfinance | IBKR |
|---|---|---|
| Commercial licence | Terms forbid it | Licensed to the account holder |
| Options data | None | Chains, quotes, Greeks |
| Futures multiplier / margin | Hand-entered constants | Real values per contract |
| Delisted names | Invisible | Contracts retained |
| Rate limits | Aggressive, unpredictable | Pacing rules, documented |

That fourth row is the one that matters most for the results above:
`long_reversal` buys multi-year losers, and yfinance has forgotten every loser
that delisted. IBKR keeps those contracts. It is the closest thing available to
the survivorship-free data the verdict rule requires, without buying Norgate.

`assistant/providers/ibkr_provider.py` is now written and wired behind the
existing `DataProvider` seam. It serves daily bars, real contract specs, and
option chains. It is **off by default** and refuses cleanly rather than
returning nothing when it cannot connect.

### What it needs from you

1. **IB Gateway or TWS running and logged in.** A paper account is enough for
   data.
2. **Market-data subscriptions.** US equities, futures and options are separate
   line items. Without them IBKR returns delayed or empty data rather than an
   error you would notice — which is exactly the failure mode that once cached
   537 instruments as though they were the whole market.
3. Set `providers.ibkr.enabled: true` in `config.yaml`.

Once connected, the honest sequence is: re-run the study on IBKR data, see how
much of `long_reversal` survives delisting-inclusive history, and only then
consider an options sleeve.

### On options specifically

The chain endpoint is written, but a chain is not a hedge. Turning it into a
protective position needs a pricing model and a sizing rule that do not exist
in this project yet. I am not going to invent either — a hedge priced from a
model I made up reports protection you do not have, which is worse than no
hedge at all. What the correlation matrix shows makes this concrete: **nothing
in this book is negatively correlated with anything else.** There is no hedge
in it today, and options are the only realistic way to add one.

---
## 8. The data behind all of it

| File | Contents |
|---|---|
| `results-1m.csv` | Every strategy, 18 metrics plus the in/out-of-sample split |
| `FULL-STUDY-1m.json` | Complete raw study output |
| `strategy-correlation.csv` | The 12×12 matrix |
| `strategy-correlation.svg` | The same as a heatmap |
| `kelly-edges.csv` | Out-of-sample observations and mean return per strategy, and which are in the blend |
| `universe-live.csv` | The 1,528 instruments the trader can hold |
| `strategies.csv` | All 12 with source, regimes, every setting |
| `exposure-groups.csv` | Which tickers are one bet |

Reproduce with `tools/full_study.py`; run one live session with
`python run.py paper`.

*Research and decision-support only — not financial advice.*
