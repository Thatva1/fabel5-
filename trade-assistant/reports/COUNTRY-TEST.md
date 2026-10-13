# Which strategies work in which country

*Research only. Nothing here was executed and nothing here is financial advice.*

## The plan, written before the run

**Committed on 13 October 2026 before any country-level result existed.** Thatva asked whether the library should be judged country by country, with the best strategies chosen for each market, rather than across everything at once. This test answers the first half of that question using trades that already exist, so nothing can be re-run or adjusted to suit a country.

| | |
|---|---|
| **Trades** | The US study (`FULL-STUDY-OCT02`, 528 instruments) and the European library study (`FULL-STUDY-EUROPE-LIBRARY`, 811 companies). Exactly as they ran; no parameter changed, nothing re-run. |
| **Groups** | United States, United Kingdom, France, Germany, Switzerland, Nordics (Sweden, Finland, Denmark, Norway), Southern Europe (Italy, Spain, Portugal), Benelux and other (Netherlands, Belgium, Luxembourg, Austria, Ireland, Channel Islands, Isle of Man). Countries with fewer than about 45 companies are pooled, because alone they cannot reach 200 signals. |
| **Baseline** | `macro_sector_rotation`'s trades **in the same group**: buying anything in that country while the region's index is rising. A country that simply went up more does not lift every strategy in it. |
| **Measure, split, pass mark** | Unchanged from `tools/region_test.py`: monthly lead over the baseline in R, split at 12 April 2022, PASS needs t ≥ 2.0, ahead in both halves and positive after costs. |
| **Scored by** | `tools/country_test.py`, committed with this plan. |

### How many lucky passes to expect

Fifteen strategies across eight groups is 120 verdicts. At a t of 2.0 about one in forty clears by luck, so **about three PASSes are expected even if no strategy works anywhere**. A PASS in only one country is therefore weak evidence. The groups are also not independent: the European groups share one ranking and one regional index.

### What would count as a country-specific choice

Decided now:

- **Use a strategy in a country** only if it PASSes in that country. Promoting a country PASS over the global ranking also needs it to PASS or be INCONCLUSIVE-and-ahead in its region as a whole, so that one lucky country is not enough.
- **Switch a strategy off in a country** only if it FAILs there with the lead below zero in **both** halves. One bad half is not enough to drop something that works elsewhere.
- **Everything else follows the global ranking.** Absence of evidence in a small country is not evidence of a difference.
- **Nothing changes in the paper book or `config.yaml` as a result of this test.** Any change is proposed to Thatva and, if accepted, tested forward first.

### Expected in advance

- UK results after costs sit below the rest, because of 0.5% stamp duty on each purchase.
- `sector_momentum` holds only the eleven US sector funds, so it is UNTESTABLE outside the US.
- The smaller groups will mostly be INCONCLUSIVE for lack of signals. That is a finding about the sample size, not about the country.

### Known limits, accepted in advance

- Today's list of companies, so survivorship bias applies.
- European strategies that rank shares ranked them against all of Europe, not within their own country. A within-country ranking would be a different test.
- Market segments (sectors) are not part of this test. Splitting 120 verdicts again by eleven sectors would produce about 30 lucky passes. Sectors come later, as a risk limit first.

## Results

*Filled in after the run, below this line, without changing anything above it.*

Run on 13 October 2026, after the plan above was committed (`87e6615`). Full numbers in `reports/COUNTRY-TEST.json`; reproduce with `python tools/country_test.py` (needs the two candidates files, which are too large to commit).

### Verdicts by country

P = PASS, i = inconclusive, F = FAIL, — = untestable.

| Strategy | US | UK | France | Germany | Switzerland | Nordics | Southern Europe | Benelux & other |
|---|---|---|---|---|---|---|---|---|
| `ts_momentum` | **P** | F (costs) | **P** | **P** | i | **P** | **P** | **P** |
| `band_reversion` | **P** | F (costs) | **P** | i | **P** | **P** | i | F |
| `high_52w` | **P** | F (costs) | i | i | i | **P** | **P** | i |
| `halloween` | **P** | **P** | F | **P** | i | i | F | i |
| `low_volatility` | i | F | F | F | **P** | i | **P** | i |
| `long_reversal` | **P** | i | F | i | i | F | F | **P** |
| `dual_momentum` | **P** | F (costs) | F | i | F | i | i | i |
| `short_reversal` | F | F | i | F | F | **P** | F | F |
| `turn_of_month` | F | F | F | **P** | F | F | F | i |
| `pead_drift` | F | **P** | F | i | F | i | F | F |
| `low_beta` | i | F | i | i | i | i | i | i |
| `activist_13d_tracking` | **P** | i | i | i | i | i | i | i |
| `xs_momentum` | F | i | i | F | i | i | i | i |
| `qmj_factor` | F | F | F | i | F | F | F | F |
| `sector_momentum` | i | — | — | — | — | — | — | — |

"F (costs)" means the strategy beat the baseline before costs — `high_52w` by t 2.8, `dual_momentum` 2.2, `band_reversion` 2.0 — and lost money after them.

**26 PASSes against about 3 expected by luck.** There is real signal here, but any single country's PASS can still be one of the lucky three, and the European groups share one ranking and one index, so they are not independent tests.

### What the plan's rules select

**Country choices that meet the rule** (PASS in the country, and PASS or ahead-but-inconclusive across its region):

| Group | Strategies |
|---|---|
| United States | `ts_momentum`, `dual_momentum`, `high_52w`, `long_reversal`, `band_reversion`, `halloween`, `activist_13d_tracking` |
| United Kingdom | `halloween`, `pead_drift` |
| France | `ts_momentum`, `band_reversion` |
| Germany | `ts_momentum`, `turn_of_month`, `halloween` |
| Switzerland | `low_volatility`, `band_reversion` |
| Nordics | `ts_momentum`, `high_52w`, `short_reversal`, `band_reversion` |
| Southern Europe | `ts_momentum`, `low_volatility`, `high_52w` |
| Benelux & other | `ts_momentum`, `long_reversal` |

**Switch-offs that meet the rule** (FAIL with the lead below zero in both halves): only `qmj_factor` in the US, which is already off. **No strategy meets the bar to be switched off in any European country.**

### What it means

1. **`ts_momentum` is the one strategy that works almost everywhere.** It passes in six of eight groups, is ahead but short of the line in Switzerland, and fails in the UK only after costs. That makes it the best candidate for a default in every country.
2. **The UK's problem is costs, not strategy.** Nearly every strategy beats the UK baseline before costs and loses after them. 0.5% stamp duty on each purchase is the whole story. Choosing a different UK strategy will not fix that. Trading less often, or trading only what carries no stamp duty (ETFs, and AIM shares), would.
3. **Some differences look like real market character.** Low volatility passes in Switzerland and Southern Europe, two famously defensive markets, and fails in the UK, France and Germany. Band reversion is strong in Switzerland, the Nordics and France.
4. **`long_reversal` is first in the live book's priority list, but passes only in the US and Benelux.** It fails in France, the Nordics and Southern Europe. It deserves the most scrutiny before any non-US book gives it first call.
5. **Single-country PASSes for proxies need care.** `pead_drift` in the UK and `turn_of_month` in Germany pass in one country and nowhere else, which is the profile of a lucky pass. `pead_drift` is also a price proxy, not the earnings-based published strategy.

### What changes

**Nothing changes in the paper book or `config.yaml`.** The paper book currently trades US shares only. Using these results needs one book per currency (USD, GBP, EUR, CHF, SEK…), each with its own strategy list. That is the next engineering step, and each country's list would be tested forward on paper before being trusted.

### Not done, and why

- **Sectors.** Splitting these 120 verdicts again by eleven sectors would give about 30 lucky PASSes. Sectors should be added first as a risk limit (no more than a set share of the book in one sector), not as a way to choose strategies.
- **Ranking within each country.** European rankings here are Europe-wide. Re-running with each country ranked on its own is a separate, larger test.
