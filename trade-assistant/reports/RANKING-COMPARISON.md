# Ranking by edge, breaking ties on strength, and the composite scorer

*4 October 2026. Research only. Nothing here was executed and nothing here is financial advice.*

Three changes to how the book chooses between strategies, measured on the US universe (528 instruments, about nine years, 130,057 signals). The strategy ranking and the composite's weights came from the European run, so nothing below was fitted to the data it is scored on.

All variants use the live paper book's limits: 1% positions, up to 120, 90% invested, no strategy above 25% of the budget, one position per instrument, and no per-market cap (the live session applies none).

## The answer

1. **Ranking by edge per trade changed almost nothing in returns** (about -0.3 points a year) and removed the hand-made "ts_momentum last" exception. Kept.
2. **Breaking ties on signal strength removed a size bet.** The old list-order tie-break put 31.5% of capital in the 50 largest, most liquid US names; strength puts 10.1% there, about their share of the universe. That cost 0.5 points a year over the decade, all of it in 2022–2026 when large companies led, and it gained in the first half. Kept: it is a measurement fix, and the return it gave up came from an accidental bet on company size, not from the rules.
3. **The composite scorer did worse than the ranked list when both used the same evidence.** Not switched on.

Every variant is still below simply holding the shares on return (22.6% a year), and level with it on return per unit of risk (Sharpe about 0.95–1.0) with a much smaller worst fall (about 22% against 37%).

## Side by side — entering on every signal day

| | Whole period | | | First half | Second half |
|---|---|---|---|---|---|
| | **Return a year** | **Worst fall** | **Sharpe** | Return / Sharpe | Return / Sharpe |
| Before (2 October) | 15.6% | 21.8% | 1.03 | 11.7% / 0.82 | 18.8% / 1.18 |
| **Ranked: edge order + strength** | **14.8%** | **21.7%** | **0.95** | **12.7% / 0.89** | **15.8% / 0.96** |
| Composite, same European evidence | 11.7% | 22.1% | 0.85 | 12.8% / 0.93 | 9.9% / 0.73 |
| Composite as shipped | 12.9% | 20.1% | 0.95 | 13.7% / 1.02 | 11.5% / 0.87 |
| *Buy and hold* | *22.6%* | *37.3%* | *1.00* | | |

## Side by side — entering only on the first trading day of each month

Closer to how the paper book enters.

| | Return a year | Worst fall | Sharpe | First half | Second half |
|---|---|---|---|---|---|
| Before (2 October) | 10.8% | 23.3% | 0.89 | 4.8% / 0.45 | 17.2% / 1.29 |
| **Ranked: edge order + strength** | **10.4%** | **23.0%** | **0.84** | **5.5% / 0.50** | **15.3% / 1.13** |
| Composite, same European evidence | 7.1% | 20.2% | 0.63 | 5.0% / 0.47 | 8.9% / 0.76 |
| Composite as shipped | 10.6% | 17.4% | 0.91 | 6.3% / 0.60 | 14.8% / 1.18 |

## Which change did what

Entering on every signal day, each change on its own:

| | Return a year | Sharpe | Capital in the 50 largest names | First half | Second half |
|---|---|---|---|---|---|
| Old order, list tie-break (before) | 15.6% | 1.03 | 31.5% | 11.7% | 18.8% |
| Old order, **strength** tie-break | 15.1% | 0.98 | 10.1% | 13.0% | 16.8% |
| **Edge** order, list tie-break | 15.4% | 1.02 | 31.6% | 12.0% | 18.7% |
| **Edge** order, **strength** tie-break | 14.8% | 0.95 | 10.5% | 12.7% | 15.8% |

The ranking moves the result by about 0.3 points either way. The tie-break moves it by 0.5 over the whole period, and in opposite directions in the two halves. The list tie-break is a three-times overweight in the largest companies. That bet paid in a decade the largest companies won, and nothing in the rules asked for it.

## The composite scorer

Run two ways:

- **With the same out-of-sample evidence as the ranked list** (edges and correlations from the European run): worst of the four in both entry patterns, and behind the ranked list by about 3 points a year. In the second half it fell furthest behind.
- **As shipped**, reading `reports/KELLY-EDGES.json` and `STRATEGY-CORRELATION.csv`: it looks better (the smallest falls and, with monthly entry, the best return per unit of drawdown). But those files were measured on the August US study's second half, which is the same 2022–2026 US data this comparison scores. Part of that result is the scorer being marked on the data its weights came from.

On the clean comparison the ranked list wins, so the composite stays off. Its design (agreement counted, discounted for correlation) may still be right, but it needs weights measured somewhere it is not then scored before it can be judged fairly.

## Still open

- The backtest's own settings (`max_positions_per_market: 25`) cap the book at about a quarter in shares, while the live book has no per-market cap. This comparison used the live book's limits. The two should be made the same.
- `band_reversion` ranks first by edge and earns about +0.01R a trade after costs. Its first place has not been changed by hand.

## How to reproduce

```
STUDY_UNIVERSE=reports/share/universe.csv \
STUDY_ENABLE=pead_drift,qmj_factor,macro_sector_rotation,activist_13d_tracking \
STUDY_TAG=OCT04 STUDY_FEED=yfinance STUDY_WORKERS=8 python tools/full_study.py

python tools/compare_ranking.py reports/FULL-STUDY-OCT04-candidates.json
```

Results in `reports/RANKING-COMPARISON.json`. The candidates file (108 MB) is not committed.
