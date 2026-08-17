# Slide 3 — What we built

*Presenter's notes. Not for distribution.*

## What this slide claims
Four properties: provenance, freshness, coverage, refusal.

## What each one means concretely
- **Provenance** — every position stores which data feed priced it and which
  trading session that price belongs to. Stored per position, not per portfolio,
  because a portfolio can hold one instrument the licensed feed cannot price.
- **Freshness** — measured against each market's own trading calendar. London
  and New York close at different times, so one timestamp cannot answer the
  question for both.
- **Coverage** — the system probes what the broker feed can actually price. On
  this account: 90 of 98. It names the subscription that unlocks the rest.
- **Refusal** — when data is missing the system reports a gap. It does not
  substitute a plausible number.

## Why "refusal" is the valuable one
It is the hardest to build and the easiest to explain. A system that says "I
don't know" is worth more than one that guesses convincingly, because you can
act on the first and not the second.

## The safety gates
Six independent checks sit between a research idea and any order. One requires a
human to type the ticker symbol. None can be bypassed in code. **No order has
ever been placed by this system.**

## What you will be asked
**"Is this just good software engineering?"** — Partly yes, and say so. Then:
*"In this field the engineering IS the edge, because the failure mode is a
number that looks right and isn't."*
