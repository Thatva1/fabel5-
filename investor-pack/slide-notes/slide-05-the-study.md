# Slide 5 — The study

*Presenter's notes. Not for distribution.*

## What this slide claims
The backtest was constructed to be hard to pass, not easy.

## The four design choices that matter
1. **Costs on both sides of every trade.** Commission and slippage, entry and
   exit. An earlier UK-only test showed costs were 96% of the loss — that is how
   much this matters.
2. **The same limits as the live system.** Gross exposure cap, position limits,
   correlation cap, compounding equity. The backtest is not allowed to do
   anything the live system would refuse.
3. **Pessimistic fills.** Where a single day's range covers both the stop and
   the target, the stop is always assumed. Assuming the target instead turns
   every ambiguous day into a winner.
4. **A hard benchmark.** Equal-weight buy-and-hold of *the same 528
   instruments*. Not an index chosen to be easy to beat.

## Why the benchmark choice is worth emphasising
Most decks compare against something convenient. Comparing against the identical
instrument set, equal-weighted, is the fairest available test — and, as slide 6
shows, we lose it on absolute return. Point that out before they do.

## What you will be asked
**"Why only 8.94 years?"** — *"That's the daily data we have for the full
universe. It's one decade and one market, and it was a bull run. That's
limitation four on slide 8."*
