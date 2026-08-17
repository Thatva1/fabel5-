# Slide 4 — Live proof

*Presenter's notes. Not for distribution.*

## What this slide claims
The system runs today, on licensed broker data, with verifiable provenance.

## Where the numbers come from
All verified 17 August 2026 against a live IB Gateway connection (paper account
DUR673876):

| Figure | How to reproduce |
|---|---|
| 90 / 98 priced | `python run.py coverage` |
| 24 live positions | Book tab on the dashboard |
| 100% licensed marks | `/api/paper` returns `"licensed": true` |
| 725 tests | `python -m pytest` |

## The three bugs listed
- **Symbol mismatches** — the broker writes some London listings with a trailing
  dot (`BP.` not `BP`) and names currency futures by currency (`EUR`) where the
  data source uses a floor code (`6E`). Five instruments were invisible and
  looked exactly like instruments that do not exist.
- **One fixed client ID** — the broker refuses a duplicate connection ID by
  going silent, so a second process saw a timeout indistinguishable from the
  gateway being down, and fell back to unlicensed data without saying so.
- **A demo holding in config** — a sample position shipped with the first build
  was being rendered as real, and every exposure percentage was computed against
  it.

## The right-hand box is the important one
Say it out loud rather than letting them read it: **zero closed trades.** If
someone quotes live performance after two days, that tells you about them.

## What you will be asked
**"How has it performed live?"** — *"Too early to say. Zero closed trades. What
it proves is that the plumbing is honest, not that the strategy works."*
