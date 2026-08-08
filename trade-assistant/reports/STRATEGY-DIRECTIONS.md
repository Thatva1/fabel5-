# Where to take this next — a blunt assessment

*Written after 10 years × 93 instruments × ~45 configurations. Research only —
not financial advice. Read `ALL-RESULTS.md` for the evidence behind every claim.*

> **Updated after testing.** Two of the three "structural constraints" below
> were hypotheses, and both **failed when measured** — details in §2. The
> section is kept with the results attached rather than quietly rewritten, so
> the reasoning that turned out wrong stays visible. The headline finding of
> that run was something else entirely: **dropping UK instruments adds 2.82
> points of CAGR** (§0).

---

## 0. The biggest available win: drop the UK allocation ★

Same rules, same engine, same decade, same 10-market universe — the only change
is removing the 23 UK listings:

| Config | Trades | CAGR | Max DD | Calmar | Final |
|---|---|---|---|---|---|
| All 93 (incl. UK) | 1,405 | 7.44% | 22.71% | 0.33 | £191,100 |
| **Ex-UK 70 only** | 1,335 | **10.26%** | 24.43% | **0.42** | £241,054 |

**+2.82 points of CAGR for deleting names from a list.** No new code, no new
strategy, no risk of overfitting — it is the removal of a 0.5% purchase tax that
these rules trade too often to absorb.

This also resolves a comparability problem that confused the whole project: your
V-series ran **ex-UK (70 names)** and mine ran **including UK (93)**. Your 12.8%
and my 7.44% were never measuring the same thing. Most of that gap was the UK,
not the engine.

**Caveat:** you live in the UK and prefer trading UK/European markets. This is a
real trade-off between preference and 2.8 points a year, not a free win. UK
exposure via LSE-listed ETFs is stamp-duty exempt and worth testing separately —
but ETFs mean-revert differently from single names, so that needs its own
backtest rather than an assumption.

---

## The diagnosis, in one paragraph

Buy-and-hold on your own universe returned **20.86% a year**. Those returns came
from **multi-year trends**. Your system is built to **fade moves and exit within
about 12 days**. Every change that helped was trend-aligned — the market filter,
longs-only. Every component that lost money was mean-reversion-aligned — range
trading, shorts, fading extremes. **The strategy and the market are mismatched.**

I originally wrote that the mismatch was "structural rather than a matter of
tuning" and named three structural causes. Two of them were then measured and
were not causes at all (§2). The mismatch is real — the benchmark gap is too
large to be anything else — but I could not identify its mechanism, and the two
mechanisms I was most confident about were wrong.

That is not a failure of the rules. Mean reversion is a real edge in the right
conditions. It was tested on mega-cap growth names during the largest bull
market in decades, which is close to the worst possible sample for it.

---

## Three constraints I proposed — two rejected on measurement

### 1. `max_holding_bars: 40` — TESTED, HYPOTHESIS REJECTED

I argued the 2-month cap made trend capture impossible. Raising it to 250 bars
(one year) made things **worse**:

| Variant | Trades | CAGR | Max DD | Calmar |
|---|---|---|---|---|
| Baseline (40 bars) | 1,405 | 7.44% | 22.71% | 0.33 |
| **Hold up to 250 bars** | 1,238 | **6.31%** | **29.62%** | 0.21 |

The trade count explains it: 1,405 → 1,238. Under a fixed exposure cap, holding
longer means each position **occupies its slot for longer**, so the portfolio
takes fewer trades and is less diversified at any moment — and it sits through
the full drawdown of each. Drawdown rose 7 points.

**The holding cap was not the constraint.** My reasoning was about individual
trades; the binding limit is portfolio slots.

### 2. Mean reversion banned from uptrends — TESTED, NEGLIGIBLE

`mean_reversion.regimes: [SIDEWAYS]`. So "buy the dip in an uptrend" — the most
reliable form of mean reversion there is — cannot fire. The rule was written to
stop the strategy fading a strong trend, which is right for **shorts**. For
**longs** it forbids the good version: buying a dip in an uptrend isn't fighting
the trend, it's joining it at a better price.

One line: `regimes: [SIDEWAYS, TRENDING_UP]`. **Tested — barely moved anything:**

| Variant | CAGR | Max DD | Calmar |
|---|---|---|---|
| Long hold, sideways only | 6.31% | 29.62% | 0.21 |
| **Long hold + dips in uptrends** | **6.39%** | 28.60% | 0.22 |

+0.08 points. The permission was never the binding constraint — the regime
router simply doesn't classify enough as TRENDING_UP for it to matter. Which
points at constraint 3, not this one.

### 3. 92% of trades land in SIDEWAYS — UNTESTED, STILL STANDING

ADX ≥ 25 is a high bar that large caps rarely sustain, so the router labels
nearly everything sideways and sends nearly all capital to the two weakest
strategies — while momentum, the only one with positive expectancy in trends
(+0.08R), fires 340 times in a decade and is starved.

Fix: lower `adx_trend_min` to ~18–20 and re-measure the regime mix. Cheap to test.

**This is the one of the three still standing.** Constraints 1 and 2 were
measured and rejected; the dips-in-uptrends result actively points here, since
granting permission changed nothing when almost nothing is ever labelled a
trend.

---

## Four new directions, ranked

### 1. Cross-sectional momentum rotation ★ build this first

Rank the universe by 6–12 month relative strength each month. Hold the top
10–15. Rebalance monthly. Gate entries on the market-trend filter you already
have.

**Why:** it directly harvests what this universe actually did, and it has the
strongest and longest-running academic support of anything here (Jegadeesh &
Titman; Asness; the AQR literature). It is also **structurally cheap** —
monthly rebalancing on 15 names is roughly 180 trades a year against your
current 1,400, which makes your stamp-duty problem nearly disappear.

**What it needs:** a portfolio-level strategy type. Your `Strategy` interface is
per-ticker (`detect(ctx) -> ideas`); rotation needs to see all instruments at
once to rank them. That's a genuine extension — a `PortfolioStrategy` interface
alongside the existing one, sharing the same router and journal.

**How to falsify it:** if it doesn't beat buy-and-hold on the same 93 names,
out-of-sample, it isn't worth keeping.

### 2. Post-earnings announcement drift

Buy after a large positive earnings surprise, hold 30–60 days.

**Why:** among the most robust documented anomalies, and **you already pull
earnings data from Finnhub and use none of it for signals.** Event-driven means
naturally low frequency — a few trades per name per year — so costs stay small.
It is also genuinely uncorrelated with everything you currently run, which
matters more than raw return.

**What it needs:** a historical surprise series. Finnhub's free tier may not go
back ten years; check before committing. If it doesn't, this is blocked on data,
not on logic.

### 3. Volatility-managed exposure

Scale total exposure continuously with inverse realised volatility, rather than
the binary risk-on/off you have now.

**Why:** it reliably improves Sharpe across almost any long equity strategy
(Moreira & Muir), and your binary version of the same idea already produced the
single biggest measured win here (+2.20 CAGR *and* −5.20 drawdown). A continuous
version should do more.

**What it needs:** very little. `portfolio.py` already computes exposure per
day; this multiplies the cap by a volatility ratio. Perhaps 30 lines.

### 4. Trend-following with realistic holding periods

Loosen ADX, raise the holding cap to a year, trail wide (4–6 ATR, not 2.5).

**Why:** momentum was the only strategy with positive expectancy in trending
markets. It failed on volume, not on quality — 340 trades in a decade.

**Now partly tested, and weaker than I claimed.** Raising the holding cap alone
made things worse (§2.1). What has *not* been tested is the combination this
direction actually calls for: loose ADX **and** long holds **and** a wide trail,
on momentum specifically. That is a different experiment, but the one component
already measured went the wrong way, so treat this as speculative.

**On the failed trailing test:** it lost badly (7.44% → 1.22%), but it was
applied to *mean-reversion* setups capped at 40 bars. A trailing stop on a trade
designed to revert to its mean, force-closed after two months, is the wrong tool
in the wrong context. Retest it where it belongs before writing it off.

---

## What to abandon

**Range trading.** 6,262 of 7,686 trades, negative expectancy, and it
monopolises the position slot — switching it off *increased* other strategies'
trade count from 180 to 251. It is the largest single destroyer of value here.

**Shorts.** Lost in every cut of every dataset: −£411,384 against longs'
+£180,423. A decade of rising markets explains part of it, but not enough to
keep them.

**The low-volatility screen.** The +0.33R vs +0.28R difference is inside its own
margin of error (SE ≈ 0.074 on a 0.05 gap), and applied alone it was *worse*
than baseline on a risk-adjusted basis. It is not the alpha.

**Anything tuned only on the UK sample.** The 3-touch range filter looked like
+£21,413 there and cost £88,509 globally.

---

## Method rules worth keeping

These came out of getting things wrong, repeatedly:

1. **Benchmark against your own universe**, not a broad index. Comparing to MSCI
   World hid a 20.86% bar behind a 12.2% one.
2. **Change one thing at a time.** Stacking three range-trading "improvements"
   was worse than the single one that worked.
3. **Out-of-sample before believing anything.** Every finding here was chosen on
   one decade.
4. **Check statistical significance before naming something the alpha.** Two of
   the biggest claimed findings were inside their own error bars.
5. **Hit rate is the wrong thing to optimise.** Shorter holds raised the win rate
   to 31% and lost £25,787 more.
6. **A confident mechanism is still a hypothesis.** I named three structural
   causes for the underperformance and was sure about two of them. Both were
   wrong when measured, and one of them (longer holds) made things materially
   worse. Test the mechanism, not just the outcome.

---

## On raising money

**Do not pitch these returns.** On the like-for-like ex-UK universe the gap is
wider, not narrower: **10.89% against a 23.28% benchmark** (buy & hold ex-UK,
£810,416 vs £253,782, Calmar 0.74 vs 0.45). Every time the comparison has been
made fairer, it has moved against the strategy. That will not survive the first
question from a quant fund.

**Pitch what you actually built.** A research platform rigorous enough to
repeatedly disprove its own strategy, and to surface eight real bugs — three of
which your team found in my code, and three of which I found in yours. That
discipline is rare, demonstrable, and it is the asset. The strategy is a work in
progress; the apparatus that can tell you the strategy doesn't work is the
product.

Still outstanding from earlier reviews, and both will be asked about early:
**the UK regulatory position** (providing trade ideas to others is likely a
regulated activity under FSMA — get counsel before pitching), and **a
commercially licensed data feed** (yfinance's terms don't permit commercial use;
your provider seam makes this one new class, which is a good answer).

---

## Suggested order

*Reordered after the long-hold and dips tests failed.*

1. **Decide the UK question** (§0). +2.82 points of CAGR, available today, no
   code. Nothing else on this list comes close for effort spent.
2. **Lower `adx_trend_min`** — the last surviving constraint, and the cheapest
   remaining experiment. One config value, one re-run.
3. **Build cross-sectional momentum rotation.** Still the primary new direction.
   The failures in §2 were all about *how long to hold an individual
   mean-reversion trade*; rotation ranks across instruments instead, so it is a
   different mechanism and is not refuted by those results.
4. **Add volatility-managed exposure.** Small, well-supported, low risk.
5. **Then PEAD**, if the earnings history exists.
6. **Re-benchmark everything** unlevered, out-of-sample, against buy-and-hold of
   the same universe — ex-UK, so the comparison is like-for-like.

**Dropped from this list:** "unshackle the current system." Its two components
were tested and both failed. The core does not need unshackling; it needs
replacing or accepting.

If cross-sectional momentum beats buy-and-hold out-of-sample, you have something
worth showing investors. If it doesn't, the honest position is that the tool is
the product and the strategy is research — which is still a real business, just
a different pitch.
