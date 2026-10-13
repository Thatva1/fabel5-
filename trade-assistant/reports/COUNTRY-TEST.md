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
