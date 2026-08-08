# Strategy review — 10-year UK backtest

> **SUPERSEDED — read `GLOBAL-REVIEW.md` instead.**
>
> This document tested 14 UK names only. A later 93-instrument, 10-market run
> overturned two of its conclusions:
>
> - "Costs destroy the edge" was **UK stamp duty**, not the strategies. The US
>   traded more often and lost 5% as much.
> - "Mean-reversion-longs is probably overfitted" was wrong — it **survived**
>   out-of-sample at +0.29R, PF 1.19.
> - The 3-touch range filter that helped here **hurts globally**; it was fitted
>   to this sample.
>
> What survived: longs beat shorts, confirmed on 3,884 global long trades.
> Kept for the record and for the method, not for its conclusions.


**Test:** 14 FTSE 100 names, 10 years (Aug 2016 – Aug 2026), 1,058 trades.
**Files:** every trade is in `backtest-trades.csv` next to this document.
**Reproduce:** `python run.py backtest <tickers> --years 10 --csv reports/backtest-trades.csv`

This is research on historical data. It is not advice, and it is not a promise
about the future.

---

## 1. The headline

| | Value |
|---|---|
| Trades | 1,058 |
| Win rate | 26.9% |
| Expectancy | +0.01R |
| Profit factor | 0.77 |
| **Net P&L** | **−£95,318** |
| Costs | £91,445 |
| Max drawdown | £106,088 |

Read those last two lines together. **Costs are 96% of the loss.** Before costs
the whole system is roughly flat (−£3,873 across 1,058 trades). It is not that
the rules are wildly wrong; it is that they trade far too often for the friction
they carry.

---

## 2. The one number that explains almost everything

Cost is essentially **fixed at ~£86 per trade** regardless of strategy:
£12 commission (£6 each way), ~£75 UK stamp duty at 0.5% of a ~£15,000
position, plus slippage.

So the only question that matters is: does a strategy make more than £86 per
trade, gross?

| Strategy | Trades | Gross per trade | Cost per trade | **Net per trade** |
|---|---|---|---|---|
| Mean reversion | 106 | **+£71.0** | £85.6 | −£14.6 |
| Range trading | 878 | −£10.6 | £86.6 | −£97.3 |
| Momentum | 74 | −£27.7 | £85.2 | −£112.9 |

Mean reversion is the only rule set with a real edge, and it still loses
because £71 of edge cannot pay an £86 toll. It misses by £15 a trade.

---

## 3. Why the hit rate matters less than it looks

All three strategies win big and lose small — the risk management is working:

| Strategy | Avg win | Avg loss | Win/loss ratio | Breakeven hit rate | Actual |
|---|---|---|---|---|---|
| Range trading | +3.01R | −1.10R | 2.75 | 26.8% | **25.9%** ✗ |
| Mean reversion | +3.09R | −1.11R | 2.79 | 26.4% | **31.1%** ✓ |
| Momentum | +2.02R | −1.22R | 1.66 | 37.7% | **33.8%** ✗ |

A 26% win rate is not a problem in itself — with a 2.75:1 payoff you only need
26.8% to break even. Range trading sits *just* under its own breakeven line, and
mean reversion sits comfortably above its own. Momentum needs a much higher hit
rate because its payoff is worse, and it doesn't get there.

**This is the most useful frame for your own tuning:** you can improve a
strategy by raising the hit rate *or* by widening the win/loss ratio. Both move
you above the line. Cutting trade count doesn't change the line at all — but it
does cut the cost bill, which is a separate and currently larger problem.

---

## 4. Longs work. Shorts don't.

This is the clearest signal in the entire dataset:

| Strategy | Side | Trades | Gross | Costs | **Net** |
|---|---|---|---|---|---|
| Mean reversion | **long** | 44 | +£7,774 | £3,701 | **+£4,074** ✓ |
| Mean reversion | short | 62 | −£246 | £5,370 | −£5,616 |
| Range trading | long | 393 | +£22,169 | £33,979 | −£11,810 |
| Range trading | short | 485 | −£31,519 | £42,088 | −£73,608 |

**Mean-reversion longs are the only combination that is profitable after all
costs.** Range-trading longs have a genuine gross edge (+£22k) destroyed purely
by cost. Shorts lose gross *and* net, in both strategies.

Two likely reasons, and they're worth you thinking about rather than taking from
me:

1. **Structural drift.** Equity indices rise over long periods. Shorting fights
   that; buying dips rides it. Ten years of FTSE is a long tailwind for longs.
2. **Shorts pay stamp duty too.** Closing a short means *buying* shares, so the
   0.5% applies on the way out. Shorts get no cost relief for being shorts.

---

## 5. Strategy by strategy

### Mean reversion — keep it, this is your best asset

**How good:** the only positive gross edge (+£71/trade), the best expectancy
(+0.24R), and the only sub-strategy that is net profitable (longs, +£4,074).
Hit rate 31.1% against a 26.4% breakeven — a genuine margin.

**Problems:**
- Only 106 trades in 10 years across 14 names. That's ~10 a year, too few to be
  confident and too few to matter financially.
- The short side is dead weight: −£5,616 net on 62 trades.
- Edge per trade (£71) sits just under the cost per trade (£86).

**Correctable?** Yes, and it's the most correctable of the three:
- Dropping shorts turns it net positive immediately on this sample.
- Sample size is fixable by widening the universe rather than loosening the
  rules — the same strictness applied to 100 names instead of 14.
- The cost gap is closable from either end: bigger positions (fixed £86 on a
  £30k position is 0.29% instead of 0.57%) or a cheaper wrapper.

### Range trading — the churn problem

**How good:** it isn't. 878 trades (83% of all activity) producing −£10.6 gross
per trade and −£85,417 net. It generates the volume that pays for the entire
cost bill.

**Problems:**
- **Fires far too often.** Entering anywhere within 25% of a band edge means
  price loitering near support re-triggers it repeatedly.
- Hit rate 25.9% against a 26.8% breakeven — it is *marginally* the wrong side
  of a line it nearly clears.
- 72.1% of its trades are stopped out. The stop sits just outside a band that
  price frequently pokes through before reversing.
- Shorts are catastrophic: −£73,608.

**Correctable?** Partly, and honestly I'd want evidence before believing it. Its
gross loss is small (−£9,350 over 878 trades) — it is close to breakeven, not
broken. But "close to breakeven before costs" is a bad place to be, because it
means the edge has to come almost entirely from trading less. See §6.

### Momentum — no edge on this sample

**How good:** it isn't, here. −£27.7 gross per trade, −0.12R expectancy, and the
worst payoff ratio (1.66) meaning it needs a 37.7% hit rate and gets 33.8%.

**Problems:**
- Only 74 trades — too few to conclude much either way.
- Its win/loss ratio is materially worse than the other two. Its 2.5× reward
  target is being reached less often than the stop.
- Trending regimes were rare in this sample (63 of 1,058 trades). Large-cap UK
  index constituents spend most of their time going sideways.

**Correctable?** Unproven either way. The honest answer is that this sample
can't tell you — 74 trades is noise. Momentum is a trend strategy tested on a
decade of range-bound UK large caps; that's close to a worst case for it. I
would not cut it on this evidence, but I would not fund it either. Test it on
instruments that actually trend (indices, commodities, US growth names) before
judging it.

---

## 6. My tuning proposals — and what I'd expect from each

I ran these as real backtests rather than guessing. Results in §7.

**A. Restrict both sideways strategies to longs only.**
The single highest-confidence change. Evidence is direct: mean-reversion longs
+£4,074 net, shorts −£5,616. Now supported via config:
```yaml
strategies:
  mean_reversion: { directions: [long] }
  range_trading:  { directions: [long] }
```
Caveat: this is a decade with a rising market underneath it. You are partly
fitting to that. It will look worse in a bear market — that is the trade-off you
are accepting, and you should accept it knowingly.

**B. Make range trading trade far less, and only at real extremes.**
`entry_zone_pct` 25 → 10, `min_touches` 2 → 3, `min_reward_risk` 1.5 → 2.5.
Rationale: cost per trade is fixed, so fewer and better trades is the only route.
Demanding a 2.5:1 payoff also lifts the win/loss ratio, dropping the breakeven
hit rate. Risk: it may cut the winners along with the losers — the gross loss is
small and spread thin, so there may be no fat to trim.

**C. Shorten the holding period, 40 bars → 20.**
10.7% of range trades time out after two months. Capital tied up in a trade
going nowhere can't be used elsewhere. Low confidence — this mostly reallocates
rather than adds edge.

**D. Size up rather than trade more.**
Not a strategy change. £86 on a £15,000 position is 0.57%; on £30,000 it's
0.29%. That halving alone would have turned mean reversion net positive. This
means raising `max_position_pct` (currently 15%) or `risk_per_trade_pct`, and it
raises your risk per idea in exchange. It is a real decision, not a free win.

**E. Reconsider the instrument, not just the rules.**
Stamp duty is 0.5% on every UK share purchase and it is 82% of your cost bill.
Spread bets and CFDs don't attract it. That changes the tax treatment, the
counterparty risk, and the leverage profile — it is a significant decision I'm
flagging, not recommending. The backtest can model it: set `stamp_duty_pct: 0`
and re-run. On this sample that alone moves the system from −£95,318 to
−£16,569.

**What I would NOT do:** loosen entry rules to get more trades. Every strategy
here already trades more than its edge can pay for. More activity is the
problem, not the solution.

---

## 7. Measured results — I tested every proposal

Same 14 instruments, same 10 years, one change at a time.

| Variant | Trades | Win% | Expectancy | PF | Net | Max DD |
|---|---|---|---|---|---|---|
| baseline | 1,058 | 26.9% | +0.01R | 0.77 | −£94,915 | £106,088 |
| range: enter within 10% of edge | 845 | 22.4% | −0.03R | 0.68 | −£96,982 | — |
| range: require 3 touches | 886 | 27.1% | +0.02R | 0.78 | −£73,502 | £80,934 |
| range: demand 2.5:1 reward | 1,004 | 24.4% | −0.05R | 0.73 | −£104,640 | — |
| range: longs only | 646 | 30.2% | +0.09R | 0.86 | −£34,628 | £52,641 |
| range: OFF | 251 | 30.7% | +0.02R | 0.76 | −£18,841 | £20,333 |
| range: all three tightened | 707 | 24.2% | +0.05R | 0.76 | −£55,619 | — |
| hold 20 bars not 40 | 1,193 | 31.0% | −0.04R | 0.72 | −£120,702 | — |
| meanrev: stretch 1.5 ATR | 1,058 | 26.9% | +0.01R | 0.77 | −£94,915 | — |
| meanrev: RSI 75/25 | 1,034 | 26.8% | −0.00R | 0.77 | −£95,021 | — |
| meanrev: longs only | 1,021 | 26.8% | +0.01R | 0.76 | −£95,074 | — |
| **A: range OFF + meanrev longs** | 159 | 33.3% | +0.09R | 0.82 | −£9,220 | £15,589 |
| **B: range longs+3touch + meanrev longs** | 488 | 30.9% | +0.12R | 0.87 | −£23,245 | £38,743 |
| **C: mean reversion, longs only** | **69** | **36.2%** | **+0.47R** | **1.15** | **+£3,166** | **£5,674** |
| **D: C with 25% position cap** | 69 | 36.2% | +0.47R | 1.18 | **+£6,117** | £9,028 |

### My scorecard: 2 right, 3 wrong, 1 blind spot

- ✗ **Tighter entry zone** — worse. Entering closer to the band edge means buying
  things still falling. Hit rate dropped 26.9% → 22.4%.
- ✓ **Require 3 touches** — better by £21,413. Validating that the level is real
  beat every filter aimed at making the setup look more attractive.
- ✗ **Demand 2.5:1 reward** — worse by £9,725. Pushing the target further away
  meant reaching it less often; the payoff gain didn't cover the strike-rate loss.
- ✗ **Shorter holds** — much worse, −£120,702. Win rate rose to 31.0% while
  expectancy went negative: it cut winners short and generated 135 extra trades
  to pay for. The clearest lesson in the whole exercise that **hit rate is the
  wrong thing to optimise.**
- ✓ **Longs only** — the biggest single effect, £60,287 better for range trading.
- ✗ **Stacking all three range changes** — worse than the one that worked alone.
  These parameters are not independent.

**And the blind spot:** `range: OFF` produced 251 trades from strategies that had
produced only 180 in the baseline. Switching a strategy off *created* 71 trades
for the others. The engine allows one position per ticker, so range trading —
firing 878 times and holding ~12 days — was sitting on the position slot and
blocking the only strategy with a real edge from entering. That is a flaw in my
engine design as much as in the strategy: **one-position-per-ticker silently
prioritises whichever strategy trades most, not whichever trades best.** It needs
a contention rule (highest expectancy wins the slot, or per-strategy budgets).
I have not changed it, because it alters behaviour you should approve first.

### Two dead knobs

- `min_stretch_atr` does **nothing**. Setting it to 1.5 produced a result
  identical to baseline down to the last pound. The strategy already requires a
  close outside the Bollinger band, which on daily data is 3–4 ATR from the
  20-day mean, so a 1.0–1.5 threshold never rejects anything. To make mean
  reversion more selective you would need to push it above ~3.0, or tighten
  `bollinger_std` instead.
- `rsi_overbought/oversold` at 75/25 did bind (24 fewer trades) but changed
  nothing (−£106). The trades it removed were average ones. Mean reversion's
  edge is not in its entry extremes — it is most likely in the stall
  confirmation and the mean-reverting target.

### The one profitable configuration, and why I don't trust it much

**C — mean reversion, longs only, everything else off: +£3,166, profit factor
1.15, expectancy +0.47R, max drawdown £5,674.**

Genuinely the best risk profile in the table: it survives a £5,674 worst case
rather than £106,088. But be clear-eyed about it:

1. **+£3,166 over ten years on a £100,000 book is +0.03% a year.** It is
   statistically positive and financially irrelevant. This is proof of an edge,
   not a living.
2. **69 trades in a decade** — about 7 a year. Far too few to be confident.
3. **I selected this from ~15 configurations tested on one dataset.** That is
   textbook overfitting. The best of fifteen tries on a single sample will look
   good by chance alone. **This result is a hypothesis, not a conclusion.**

Variant D matters more than its size suggests: identical trades, larger
positions, nearly double the profit (+£6,117) — because the ~£86 cost is fixed
per trade regardless of size. That is the clearest lever you have, and it is a
real risk decision, not a free win.

### What I would actually conclude

The strategies do not currently have enough edge to overcome UK equity costs at
your position size. The three routes that follow from the evidence:

1. **Widen the universe, don't loosen the rules.** Mean-reversion longs work but
   fire 7 times a year on 14 names. The same strictness across 100–200 names is
   the only way to get a meaningful number of good trades. This is the change I
   would make first, and it needs no new logic.
2. **Fix the position-slot contention**, or a high-frequency strategy will keep
   crowding out a better one.
3. **Address costs structurally** — larger positions, or an instrument without
   0.5% stamp duty on every purchase.

Before believing any of it: re-run C on **different instruments and a different
decade**. If it survives out-of-sample, you have something. If it doesn't, I
fitted it to this sample and you should discard it. I would genuinely expect
that test to be the most informative thing you could do next.

---

## 8. How to hand this back to me

Change what you want in `config.yaml` and re-run:

```bash
python run.py backtest <your tickers> --years 10 --csv reports/my-version.csv
```

Send me the config and the CSV and I'll read both. What I'd find most useful:
which changes you made and *why* you expected them to work — if your reasoning
and the numbers disagree, that disagreement is usually where the real insight is.

Things worth testing that I haven't:
- A different universe. 14 large-cap UK names is a narrow and unusually
  range-bound sample.
- A different period. Try 2016–2021 and 2021–2026 separately; a rule that only
  worked in one half is fitted to that half.
- Your own strategies, which still aren't in the library.

---

## 9. What this test cannot tell you

- **Survivorship bias.** Only companies that still exist were tested. Every
  delisting and collapse is invisible, which flatters all results above.
- **Technicals only.** The thesis engine, news and fundamentals can't be
  replayed historically. This tests the rules, not the full product.
- **One sample.** 14 instruments, one market, one decade. Findings that don't
  survive a change of universe or period were never findings.
- **Modelled fills.** Slippage, commission and duty are estimates. A thin stock
  in a fast market is worse than modelled.
- **The past is not the future.** A strategy that worked can stop working the
  day conditions change.
