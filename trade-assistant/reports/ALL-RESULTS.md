# Every test run to date — labelled, with the data each used

*Research only — not financial advice.*

---

## 1. Which data did each test use?

**Short answer: two universes, and one important mismatch.**

| Universe | Instruments | Where defined | Used by |
|---|---|---|---|
| **UK-only** | 14 FTSE 100 names | (ad hoc, first test) | Test A |
| **Global** | 93 across 10 markets | `reports/global-universe.txt` | Tests B–F |
| **Global ex-UK** | 70 (the same 93 minus 23 UK) | derived | Your V0–V3, alpha-hunt |

So everything after the first test drew from **the same 93-instrument file**. Your
V-series used a subset of it — the same names with the 23 UK listings removed.

**The mismatch that matters:** your V1 "unlevered 12.8% CAGR" was measured on
**70 ex-UK** names. My engine-v2 "7.44%" was measured on **all 93, including UK**
— and UK is the one market that loses money on these rules, at −£187k in the
earlier global run. Those two numbers were never comparable. A like-for-like
ex-UK comparison is in §4.

**Data source:** yfinance, 10 years of daily bars (Aug 2016 – Aug 2026), split
and dividend adjusted, converted to GBP at each trade's own dated FX rate.

---

## 2. The tests, in order

| # | Test | Universe | Trades | What it was for |
|---|---|---|---|---|
| **A** | UK-only baseline | 14 UK | 1,058 | First backtest |
| **B** | UK parameter sweep | 14 UK | ~1,000 ea. | 15 config variants |
| **C** | Global baseline | 93 global | 7,686 | Was A a UK artefact? |
| **D** | Global variants | 93 global | 587–3,884 | Longs-only, mean-rev-only |
| **E** | Your V0–V3 + alpha-hunt | 70 ex-UK | 1,190–2,493 | Leverage, filters, compounding |
| **F** | Engine v2 full run | 93 global | 814–1,608 | Corrected engine, exposure sweep |

---

## 3. Results by test

### Test A — UK-only baseline *(superseded)*
1,058 trades, −£95,318. Costs £91,445 (96% of the loss).
Gross edge per trade: mean-reversion +£71, range −£11, momentum −£28.
**Conclusion at the time — later shown wrong:** "costs destroy the edge."

### Test B — UK parameter sweep *(superseded)*
| Variant | Net |
|---|---|
| baseline | −£94,915 |
| range: 3 touches | −£73,502 |
| range: longs only | −£34,628 |
| range: OFF | −£18,841 |
| **mean reversion longs only** | **+£3,166** |
| shorter holds (20 bars) | −£120,702 |

My scorecard: 2 right, 3 wrong, 1 blind spot (position-slot contention).

### Test C — Global baseline
7,686 trades, −£230,960. **UK was 23% of instruments and 81% of the loss.**

| Market | PF | Net |
|---|---|---|
| United States | 0.98 | −£9,571 |
| France | 1.05 | +£9,903 |
| **United Kingdom** | **0.74** | **−£187,441** |

Longs +£180,423 (PF 1.21) vs shorts −£411,384 (PF 0.66).
**Overturned Test A: "costs destroy the edge" was UK stamp duty, not the rules.**

### Test D — Global variants
| Variant | Trades | Expectancy | Net |
|---|---|---|---|
| **all longs only** | 3,884 | +0.22R | **+£181,227** |
| mean reversion longs only | 587 | +0.29R | +£24,811 |
| range 3-touch + longs | 3,205 | +0.21R | +£92,718 |

Profitable in **9 of 10 markets**. *All leveraged ~2× — see Test E.*

### Test E — Your V-series *(70 ex-UK, compounded)*
| Config | CAGR | Max DD | Final |
|---|---|---|---|
| V0 leveraged | 33.4% | 50% | £1,325,227 |
| **V1 unlevered** | **12.8%** | 27% | £295,949 |
| V2 + market filter | 10.1% | 12% | £221,415 |
| Low-vol + filter | 9.0% | 9% | £203,941 |

**Found the flaw in my engine: ~2× accidental leverage.** Also corrected a
drawdown that divided by final peak instead of the running peak.

### Test F — Engine v2 *(93 global, corrected, compounded, capped)*

**Exposure sweep:**
| Exposure | CAGR | Max DD | Calmar |
|---|---|---|---|
| 50% | 3.76% | 15.85% | 0.24 |
| 70% | 5.04% | 18.34% | 0.27 |
| **90%** | **7.44%** | 22.71% | **0.33** |
| 100% | 7.55% | 25.78% | 0.29 |

**Per-market cap:** 2 → 2.87%, 4 → 6.78%, **8 → 7.44%**, 12 → 7.16%

**Levers:**
| Change | CAGR | DD | Verdict |
|---|---|---|---|
| Market filter, half-size | 7.44% | 22.71% | KEEP |
| Market filter off | 5.24% | 27.91% | — |
| ATR trailing exit | 1.22% | 33.09% | REJECT |
| Correlation cap 0.8 | 6.63% | 23.50% | REJECT |

**Benchmark:** buy & hold same 93 = **20.86% CAGR, 31.73% DD, £665,389**
**Out-of-sample:** SURVIVED (8.89% → 6.77%)
**Sequence:** median drawdown 13.56%, worst 5% 23.13%

---

## 4. Bugs found, and by whom

| Bug | Impact | Found by |
|---|---|---|
| yfinance pence/pounds in fundamentals | Thesis saw a 99% phantom crash | Me (AI flagged it) |
| Entry drift collapsing R denominator | Phantom +183R trades | Me |
| Summing P&L across currencies | Meaningless global totals | Me |
| **~2× accidental leverage** | **Every headline inflated** | **You** |
| **Drawdown ÷ final peak** | **All DDs understated** | **You** |
| Drawdown compared fraction vs % | Reported *first* DD, not worst (0.28%) | Me, after your fix |
| `index_for` defaulting to S&P | Wrong index per market | Me |
| Validator judging on 1 trade | False confidence | Me |

---

## 5. What survived everything

1. **Longs beat shorts** — every cut, every universe, 3,884 trades.
2. **The market-trend filter works** — the only change improving return *and*
   drawdown at once (+2.20 CAGR, −5.20 DD).
3. **Leverage was the drawdown villain** — 50% → 27% on removal.
4. **Not overfitted** — survived out-of-sample.

## What died

1. **Costs destroy the edge** — UK-specific, not universal.
2. **Low-volatility screen is the alpha** — inside its own margin of error, and
   worse than baseline on a risk-adjusted basis when applied alone.
3. **Winners are being capped** — trailing exit made it dramatically worse.
4. **We win on risk-adjusted return** — buy & hold Calmar 0.66 vs 0.33.
5. **Range trading's 3-touch filter** — helped in the UK, hurt globally.
