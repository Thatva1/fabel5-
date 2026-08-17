# Slide 1 — Title

*Presenter's notes. Not for distribution.*

## What this slide claims
That this is research infrastructure for systematic trading, and that its
distinguishing property is honesty about its own limits.

## Where the numbers come from
| Figure | Source |
|---|---|
| 12 strategies | `reports/share/strategies.csv` — each names a published paper |
| 528 instruments | `reports/share/universe.csv` |
| 8.94 years | Study period in `reports/share/FULL-STUDY-1m.json` |
| 62,319 signals | `candidates` field, same file |
| 725 tests | `python -m pytest` in `trade-assistant/` |

## How to open
Say what the company is in one sentence, then stop. Do **not** open with
returns — the returns slide is deliberately sixth, and it is not a win.

Suggested: *"We build systematic trading research that's designed to be
checked. The unusual part is that it reports what it doesn't know."*

## What you will be asked
**"So do you make money?"** — Answer straight: *"Over the decade we tested,
holding the index made more. Our best configuration made about 3.5 points a
year less with 15 points less drawdown. I'll show you the exact table."*

Answering that honestly in the first minute buys you the rest of the meeting.
