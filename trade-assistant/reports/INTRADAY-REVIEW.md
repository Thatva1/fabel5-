# Does an intraday edge exist on this universe?

**No.** Not one this account can trade, and not one that survives being measured
properly. 22 sessions, 1,554 instruments, 125,617 trades.

Run `python run.py intraday --engine --universe --bars 5m --duration "1 M"`.
Raw output is not committed — it is 1.3 MB of per-instrument detail, and the
bars are cached, so re-deriving it costs about two minutes of CPU rather than
the seven hours of broker time it originally took.

## The answer

| | trades | gross edge | t / session | t / trade |
|---|---:|---:|---:|---:|
| intraday_momentum | 5,709 | +1.9 bp | +0.82 | +3.69 |
| opening_range_break | 42,218 | **−2.7 bp** | −1.34 | −6.49 |
| vwap_reversion | 77,690 | +1.4 bp | +1.17 | +4.80 |
| **all rules** | **125,617** | **+0.1 bp** | **+0.22** | +0.23 |

Costs charged: **12.2 bp** a round trip, trade-weighted, against measured
spreads with a median of 5.49 bp.

A gross edge of 0.1 bp at t = +0.22 is nothing. It is not a small edge eaten by
costs — there is no edge to eat. The opening-range break is *negative* before
costs, which is a finding in its own right: it is not that the rule is too
expensive to trade, it is that it is wrong.

## Why the two t columns disagree, and why it matters

`t / trade` treats 125,617 trades as independent draws. They are not. Fifteen
hundred instruments traded through the same 22 sessions move together — when
the market drops at 13:30, several hundred VWAP-reversion longs are wrong
simultaneously and for one reason. The independent unit is close to the
session.

Read per trade, vwap_reversion looks like a real effect at t = +4.80. Clustered
by session it is +1.17, which is nothing. **The per-trade figure is the one that
would have justified spending money, and it is the wrong one.**

## The five-day run said the opposite, and it was noise

An earlier sweep over 5 sessions reported a gross edge of +2.2 bp at a
per-trade t of +4.63, and vwap_reversion at +3.3 bp / +5.45. Clustered by
session that same result was t = +1.86 — already not significant — and I had
reported it as "a real edge, that is not noise" before correcting it.

22 sessions settle it. The +3.3 bp became +1.4 bp; the +5.45 became +1.17.

There was also an apparent time-of-day pattern in the five-day data — entry-hour
gross edges of +8.1, −1.6, +7.1, −4.5, +7.1, −1.0 bp, with four hours clearing
t = 2. Each of those buckets held five realisations of that hour. It was five
market moves being read as six.

## What this rules out, and what it does not

**Rules out:** buying market data to trade these three rules at five-minute
bars on this universe. Costs would have to fall to nothing AND an edge would
have to appear; today there is only the first problem's absence of a second.

**Does not rule out:** a different bar size, a different universe, rules with a
hypothesis behind them rather than three standard ones, or the same rules
measured over a year rather than a month. 22 sessions is one regime.

The cheapest next question is bar size. A 15- or 30-minute bar pays the spread
a third as often, and the fetch is faster, so it is hours rather than days of
work — and the engine takes `--bars 15m` today.

## What is trustworthy here

Measured spreads by Corwin-Schultz on the intraday bars themselves — median
5.49 bp, 10th-90th percentile 2.16-12.48 bp — which is credible for this
universe and a world away from the 63 bp the same estimator returns on *daily*
bars. Every instrument pays its own spread, not an average.

Costs are charged both sides. Entries fill at the next bar's open, never the
signal bar's close. A bar covering both stop and target resolves as the stop.
Nothing is held past its own venue's bell.
