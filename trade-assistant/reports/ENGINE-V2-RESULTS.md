# Full backtest under the corrected engine

*93 instruments, 10 markets, 10 years, longs only, compounded, exposure-capped,
mark-to-market drawdown. £100,000 start. Research only — not advice.*

**Supersedes every earlier result in this folder.** Previous runs sized each
instrument against the whole book independently, which produced ~2x accidental
leverage, and did not compound.

---

## 1. The benchmark, first

| | CAGR | Max DD | Calmar | £100k becomes |
|---|---|---|---|---|
| **Buy & hold, same 93 instruments** | **20.86%** | 31.73% | **0.66** | **£665,389** |
| Best strategy config | 7.44% | 22.71% | 0.33 | £191,100 |

**Buy-and-hold wins on return (2.8x) and on risk-adjusted return (Calmar 0.66
vs 0.33).** The strategy's lower drawdown does not come close to compensating.

This contradicts the earlier "we win on risk-adjusted return" conclusion, and
the reason is the benchmark. That claim compared against MSCI World (12.2%).
Measured against *the actual instruments the strategy traded*, the bar is
20.86% — because this universe is loaded with US mega-caps that had an
extraordinary decade.

**Caveat that cuts the other way:** these 93 names were chosen today, with
hindsight, and include names that rose 50-100x. Buy-and-hold benefits from that
selection bias more than the strategy does, because it holds the winners for the
full decade while the strategy exits at a target after ~12 days. The gap is
large enough that selection bias alone is unlikely to explain it, but it is real
and unquantified.

---

## 2. Exposure sweep — the answer to "why not 100%?"

| Exposure | Trades | CAGR | Max DD | Calmar |
|---|---|---|---|---|
| 50% | 814 | 3.76% | 15.85% | 0.24 |
| 60% | 969 | 4.55% | 17.46% | 0.26 |
| 70% | 1,136 | 5.04% | 18.34% | 0.27 |
| 80% | 1,290 | 6.51% | 21.01% | 0.31 |
| **90%** | **1,405** | **7.44%** | **22.71%** | **0.33** |
| 100% | 1,560 | 7.55% | 25.78% | 0.29 |

Return rises all the way to 100%, drawdown rises faster, and **risk-adjusted
return peaks at 90%.** The last step from 90% to 100% buys **0.11 points of
CAGR for 3.07 points of drawdown** — a bad trade at any risk appetite.

Config now set to 90%.

---

## 3. Per-market cap — 4 was too tight, as suspected

| Cap | CAGR | Max DD | Calmar |
|---|---|---|---|
| 2 | 2.87% | 22.69% | 0.13 |
| 4 | 6.78% | 21.72% | 0.31 |
| 6 | 6.10% | 22.27% | 0.27 |
| **8** | **7.44%** | 22.71% | **0.33** |
| 12 | 7.16% | 22.71% | 0.32 |

A cap of 4 cost 0.66 points of CAGR by blocking good trades; 2 was ruinous.
Above 8 adds nothing. **Set to 8.**

---

## 4. What worked and what didn't

| Change | CAGR | Max DD | Verdict |
|---|---|---|---|
| Market-trend filter, half-size | 7.44% | 22.71% | **KEEP** |
| Market filter off | 5.24% | 27.91% | — |
| Fixed profit target | 7.44% | 22.71% | **KEEP** |
| ATR trailing exit | 1.22% | 33.09% | **REJECT** |
| No correlation cap | 7.44% | 22.71% | **KEEP** |
| Correlation cap at 0.8 | 6.63% | 23.50% | **REJECT** |

**The market-trend filter is the one unambiguous win** — it improves return
*and* drawdown simultaneously (+2.20 points of CAGR, −5.20 points of drawdown).
That confirms the finding independently, on a corrected engine.

**The trailing exit fails decisively.** 7.44% → 1.22% with drawdown rising to
33%. The "winners are being capped" hypothesis is dead: the 92%-of-winners-hit-
target statistic was an artefact of how exits are defined, not evidence that
price kept running. Letting winners run gives back more than it captures here.

**The correlation cap costs return without reducing drawdown.** Rejected on its
own evidence.

---

## 5. Out-of-sample: SURVIVED

Split at 2021-08-01.

| | Trades | CAGR | Max DD |
|---|---|---|---|
| Development (2017–2021) | 673 | 8.89% | 22.71% |
| Validation (2021–2026) | 757 | 6.77% | 21.07% |

Return decayed 24% — consistent enough to take seriously. **The strategy is not
overfitted.** It is reliably, repeatably worse than buy-and-hold.

---

## 6. Sequence sensitivity

300 shuffles of trade order. Final equity is identical by construction (the sum
is the sum), but the *path* varies enormously:

- Max drawdown, median shuffle: **13.56%**
- Max drawdown, worst 5% of shuffles: **23.13%**

The actual 22.71% sits near the bad end. **Your realised drawdown was close to
a worst-case ordering** — a different sequence of the same trades would more
typically have produced ~14%. Do not treat 22.71% as the expected experience,
in either direction.

---

## 7. What this means

The strategy is **internally sound and externally uncompetitive**. It survives
out-of-sample, its risk controls work, its filter genuinely helps — and it still
returns a third of what holding the same shares would have.

The likely reason is a **mismatch between the strategy and the universe.** A
long-only mean-reversion system that exits at a fixed target after ~12 days is
structurally incapable of capturing a multi-year 50x run. It was run on
mega-cap growth names during the largest bull market in decades — the worst
possible sample for fading extremes.

Two honest paths:

1. **Change the universe, not the rules.** Test mean-reversion where it should
   work: lower-beta, genuinely range-bound instruments. If it beats buy-and-hold
   *there*, the rules were never the problem.
2. **Accept it isn't a market-beater and reposition.** The research tool is
   valuable regardless of whether the strategy is. Pitching decision-support
   software backed by unusually rigorous testing is defensible; pitching returns
   is not.

---

## 8. Known gaps

- **Survivorship and selection bias** — 93 names picked today, favouring
  buy-and-hold more than the strategy.
- **One bull decade.** A long-only system has never been tested in a sustained
  bear market.
- **Entry timing is `next_open`** (market orders) in the current config, while
  the engine default is `limit`. Limit fills miss some trades but get better
  prices — untested, and worth one run.
- **Technicals only.** The thesis engine cannot be replayed historically.
