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

*To be filled in after the run.*
