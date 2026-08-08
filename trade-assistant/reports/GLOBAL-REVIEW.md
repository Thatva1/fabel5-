# Global strategy review — 10 markets, 10 years

**Test:** 93 instruments across 10 countries and 8 currencies, Aug 2016 – Aug 2026.
**7,686 trades.** Every trade in `backtest-global.csv`.
**Supersedes `STRATEGY-REVIEW.md`,** which tested 14 UK names and reached two
conclusions this run overturns.

```bash
python run.py backtest --universe reports/global-universe.txt --years 10 \
    --csv reports/backtest-global.csv
```

Research on historical data. Not advice, not a prediction.

---

## 1. Why this run exists, and what it corrected

The earlier review tested only UK large caps. Two of its conclusions were wrong,
and both were wrong in the direction of blaming the strategies for something
else.

| Claim from the UK review | Verdict globally |
|---|---|
| "Costs destroy the edge" | **Wrong.** UK-specific. The US traded *more* and lost 5% as much. |
| "Mean-reversion-longs is probably overfitted" | **Wrong — it survived.** +0.29R, PF 1.19 out-of-sample. |
| "Range trading's 3-touch filter is an improvement" | **Wrong.** Fitted to UK; hurts globally. |
| "Longs beat shorts" | **Confirmed**, and it is the largest effect in the data. |

Before this could run honestly, two bugs had to be fixed. The report summed each
trade's P&L in its own currency — dollars added to yen — and costs were
hardcoded to the UK structure, charging US trades a British tax. Both are fixed:
every trade now converts to base currency at **its own entry date's rate**, and
costs are per market.

---

## 2. Baseline: all strategies, both directions

| | Value |
|---|---|
| Trades | 7,686 |
| Win rate | 26.9% |
| Expectancy | −0.02R |
| Profit factor | 0.89 |
| Net | −£230,960 |
| Max drawdown | £259,309 |

Losing overall — but the aggregate hides everything that matters.

---

## 3. The UK is 23% of the sample and 81% of the loss

| Market | Trades | Expectancy | PF | Net |
|---|---|---|---|---|
| United States | 2,178 | −0.04R | 0.98 | −£9,571 |
| France | 735 | +0.08R | 1.05 | **+£9,903** |
| Japan | 487 | −0.10R | 0.84 | −£172 |
| Canada | 369 | +0.06R | 0.99 | −£336 |
| Netherlands | 354 | −0.04R | 0.99 | −£727 |
| Hong Kong | 315 | −0.07R | 0.93 | −£786 |
| Australia | 371 | +0.08R | 0.98 | −£1,052 |
| Switzerland | 265 | −0.07R | 0.85 | −£9,490 |
| Germany | 868 | −0.04R | 0.88 | −£31,288 |
| **United Kingdom** | 1,745 | −0.03R | **0.74** | **−£187,441** |

The UK's *expectancy* (−0.03R) is unremarkable — middle of the pack. Its profit
factor is catastrophic. The difference is 0.5% stamp duty on every purchase,
which costs roughly £75 on a £15,000 position. The US pays ~$1 commission and no
duty, traded 25% more often, and lost 5% as much money.

**The earlier "costs destroy the edge" conclusion was a finding about British
tax law, not about these strategies.**

---

## 4. The largest effect in the data: direction

| Direction | Trades | Win rate | Expectancy | PF | Net |
|---|---|---|---|---|---|
| **long** | 3,284 | 34.5% | **+0.23R** | **1.21** | **+£180,423** |
| short | 4,403 | 21.3% | −0.21R | 0.66 | −£411,384 |

Longs are profitable after every cost. The system loses overall only because it
takes 34% more shorts than longs. This held on 14 UK names and now holds on
3,284 long trades across 10 markets — it is the most robust finding here.

**Caveat you should weigh heavily:** ten years of mostly-rising equity markets
is a tailwind for longs, and testing ten countries does not escape it because
they rose together. A sustained bear market would look different. This sample
contains 2018, 2020 and 2022 drawdowns but no multi-year decline.

---

## 5. The configuration that works

**All strategies, longs only** — 3,884 trades, 35.1% win rate,
**+0.22R expectancy, profit factor 1.18, net +£181,227**, max drawdown £55,837.

| Market | Trades | Expectancy | PF | Net |
|---|---|---|---|---|
| United States | 1,086 | +0.33R | 1.41 | +£116,123 |
| France | 345 | +0.41R | 1.46 | +£44,511 |
| Australia | 175 | +0.56R | 1.80 | +£19,218 |
| Netherlands | 173 | +0.11R | 1.34 | +£15,261 |
| Canada | 184 | +0.54R | 1.62 | +£14,394 |
| Germany | 436 | +0.07R | 1.05 | +£6,487 |
| Switzerland | 144 | +0.15R | 1.16 | +£5,256 |
| Hong Kong | 191 | +0.07R | 1.16 | +£1,003 |
| Japan | 247 | +0.07R | 1.02 | +£9 |
| **United Kingdom** | 903 | +0.08R | 0.89 | −£41,036 |

Profitable in **9 of 10 markets**. The UK's expectancy is positive; it loses
only on tax.

```yaml
strategies:
  momentum:       {directions: [long]}
  mean_reversion: {directions: [long]}
  range_trading:  {directions: [long]}
```

### Other configurations tested globally

| Variant | Trades | Win% | Expectancy | PF | Net | Max DD |
|---|---|---|---|---|---|---|
| baseline (both directions) | 7,686 | 26.9% | −0.02R | 0.89 | −£230,960 | £259,309 |
| **all longs only** | 3,884 | 35.1% | **+0.22R** | **1.18** | **+£181,227** | £55,837 |
| range 3-touches + all longs | 3,205 | 35.2% | +0.21R | 1.12 | +£92,718 | £47,088 |
| mean reversion longs only | 587 | 35.3% | +0.29R | 1.19 | +£24,811 | £24,247 |
| range OFF + meanrev longs | 1,404 | 36.0% | +0.12R | 0.97 | −£8,286 | £23,015 |

Highest expectancy is mean-reversion-longs (+0.29R) but on only 587 trades.
Highest total is all-longs (+£181,227) on 3,884 — more trades, slightly lower
quality each. Which you prefer depends on whether you want the best per-trade
edge or the most total profit; the drawdowns differ by more than 2×.

---

## 6. Strategy notes, global

**Momentum** — the UK sample said it had no edge. Globally it is +0.08R in
trending-up markets over 340 trades, and −0.39R in trending-down over 141.
Its problem was never the strategy; it was that UK large caps rarely trend, and
that its short side is bad. Longs-only in uptrends is a real setup.

**Mean reversion** — the only strategy that survived out-of-sample as a whole
configuration. Longs-only: +0.29R, PF 1.19, positive in the US, UK, France,
Germany and Hong Kong. Negative in the Netherlands, Japan and Canada, all on
small samples. Still fires rarely (587 trades in a decade across 93 names).

**Range trading** — 6,262 of 7,686 baseline trades (81%). Marginally negative
expectancy, and it is the volume that generates the cost bill. Restricting it to
longs helps; the 3-touch filter that helped in the UK hurts globally.

**Squeeze** — 148 trades, +0.06R after handoff to momentum. Second-best regime
by expectancy. Too few trades to say more.

---

## 7. Regimes: where the edge actually lives

| Regime | Trades | Win% | Expectancy | PF |
|---|---|---|---|---|
| Trending up | 340 | 41.2% | **+0.08R** | 0.96 |
| Volatility squeeze | 148 | 38.5% | **+0.06R** | 0.91 |
| Sideways | 7,058 | 26.1% | −0.02R | 0.89 |
| Trending down | 141 | 23.4% | −0.39R | 0.52 |

92% of trades happen in sideways markets, which is the only regime with negative
expectancy other than downtrends. The regime router is correctly identifying
that most of the time there is no trend — but the strategies assigned to that
regime are the weak ones. Worth considering whether "no trend" should more often
mean "no trade".

---

## 8. Known issues, unfixed

**Position-slot contention.** One position per ticker means range trading — 81%
of signals — occupies the slot and blocks better strategies. In the UK test,
switching range trading off *increased* other strategies' trade count from 180
to 251. This needs a contention rule (highest expectancy wins the slot) and I
have not changed it, because it alters trading behaviour you should approve.

**Two dead config knobs.** `min_stretch_atr` never binds (the Bollinger break
already implies 3–4 ATR). Tightening `rsi_overbought` to 75 changed nothing.

**One instrument failed.** ROG.SW returned no data; 93 of 94 were tested.

---

## 9. What this still cannot tell you

- **Survivorship bias.** Only companies that still exist were tested — every
  delisting and collapse is invisible, and this flatters all results.
- **One bull decade.** The longs-only finding is partly a bet on that continuing.
- **Technicals only.** The thesis engine, news and fundamentals cannot be
  replayed, so this tests the rules, not the product.
- **Modelled fills.** Slippage and commission are estimates; a thin stock in a
  fast market is worse.
- **Selection effects.** 93 large caps chosen by me, today, with hindsight about
  which markets matter.
