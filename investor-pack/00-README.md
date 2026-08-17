# Investor pack

Built 2026-08-17. Everything here is derived from measured results in
`trade-assistant/reports/`. No figure was estimated, rounded up, or illustrated.

## What's in here

| File | What it is | Send to investors? |
|---|---|---|
| `Trade-Assistant-Investor-Deck.pptx` | 12-slide deck, with speaker notes on every slide | **Yes** — after they reply, not cold |
| `01-BACKTEST-RESULTS.md` | The full results record, including limitations | **Yes** — attach with the deck |
| `02-EMAIL-TEMPLATES.md` | Five email templates plus phrases to avoid | No — for you |
| `slide-notes/` | One explanation file per slide: sources, likely questions, answers | No — for you |
| `build_deck.js` | Regenerates the deck | No |
| `write_slide_notes.py` | Regenerates the slide notes | No |

## Before you send anything

**1. Fill in slide 11.** It contains `£[AMOUNT]` and `[N] months`. The deck does
not make sense with the brackets still in it. See `slide-notes/slide-11-the-ask.md`
for how to arrive at a number you can defend.

**2. Read `slide-notes/slide-06-...` and `slide-08-...` first.** Those two cover
the material that will decide the meeting: that buy-and-hold beat the strategies
on return, and that the out-of-sample test did not work as intended.

**3. Have an answer ready for "what do I own?"** This pack contains no equity,
valuation or deal terms by design. Do not improvise them in a meeting.

## To regenerate

```bash
cd "investor-pack"
node build_deck.js          # rebuilds the .pptx
python3 write_slide_notes.py # rebuilds slide-notes/
```

## The position this pack takes, and why

The deck leads with the fact that **buy-and-hold outperformed every strategy
tested on absolute return** (23.68% vs 20.18% a year). It states five specific
limitations, including that the out-of-sample test produced a result that runs
backwards and cannot be relied on. It says plainly that there is no live track
record.

That is deliberate, for three reasons:

1. **It is what the evidence says.** `reports/share/results-1m.csv` and
   `reports/share/RESULTS-1M.md` are in the repository and say so.
2. **It survives due diligence.** Any investor who funds this will read the
   study. Finding the benchmark comparison themselves, after a deck that omitted
   it, ends the conversation.
3. **It is the actual product.** The defensible asset is a research process that
   catches its own errors — five hypotheses killed, eight material bugs found,
   two of them by the founder auditing generated analysis. A deck that hid the
   unfavourable result would be evidence against the thing being sold.

The genuinely strong claims are still in there: better Sharpe, Sortino and
Calmar than the benchmark; roughly 60% of the drawdown; a live system running on
fully licensed, provenance-stamped data; 725 passing tests.

## What this pack is not

It is not a financial promotion prepared by a regulated person, and it is not
legal advice. If you intend to solicit investment in the UK at any scale, the
financial-promotion rules apply to how these materials may be distributed and to
whom. Speak to a solicitor before sending them widely — the exemptions for
high-net-worth and sophisticated investors carry specific conditions.

---

*Research and decision-support software. Nothing described here was executed
automatically, nothing here is financial advice, and this pack is not an offer or
solicitation to buy or sell any security. Past behaviour is not predictive.*
