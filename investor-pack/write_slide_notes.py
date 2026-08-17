"""Write one explanation file per slide.

These are for the person presenting, not for the investor. Each answers three
things the deck itself cannot: where the number came from, what will be asked,
and what the honest answer is when the honest answer is unhelpful.
"""
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slide-notes")
os.makedirs(OUT, exist_ok=True)

HEADER = """# Slide {n} — {title}

*Presenter's notes. Not for distribution.*

"""

SLIDES = [
    (1, "Title", """
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
"""),

    (2, "The problem", """
## What this slide claims
That trading research systems routinely present broken or unverifiable data as
though it were sound, and that this is hard to detect precisely because a
failing system looks like a working one.

## Where these come from
All three were found **in this codebase** and are named later in the deck:

1. **Stale prices shown as live** — the dashboard displayed a stored price with
   no date beside it. On a Monday morning it showed Friday's close, which was
   correct, but there was no way to tell that from a dead feed.
2. **Mixed data sources** — `is_available()` returned a config flag rather than
   testing the connection, so with the broker gateway shut the system displayed
   its "licensed data" badge while serving free scraped data underneath.
3. **In-sample tuning** — our own study says the settings were chosen knowing
   how the decade turned out. See slide 8.

## Why this slide is second
It sets up the standard the rest of the deck is judged against. If you claim
these failures matter, you must then show your own — which is slide 8.

## What you will be asked
**"Doesn't everyone know this?"** — *"Everyone knows it can happen. Very few
systems can tell you, per number, whether it did. That's the difference."*
"""),

    (3, "What we built", """
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
"""),

    (4, "Live proof", """
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
"""),

    (5, "The study", """
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
"""),

    (6, "Results — the honest headline", """
## What this slide claims
Buy-and-hold returned more. The strategies drew down substantially less.

## The exact numbers
Source: `reports/share/results-1m.csv`, £1,000,000 portfolio, 8.94 years, net of
modelled costs.

| | Annual return | Max drawdown | Sharpe |
|---|---|---|---|
| Buy & hold (528) | **23.68%** | 37.76% | 1.03 |
| `long_reversal` | 20.18% | **22.62%** | **1.30** |
| 4-strategy blend | 19.20% | **23.67%** | **1.12** |

**−3.5 points** of annual return given up. **−15.1 points** off the worst
peak-to-trough fall.

## Do not soften this slide
The temptation is to lead with the Sharpe ratio and bury the return. Resist it.
An investor who discovers on their own that the benchmark won will discount
everything else you said. Leading with it costs you nothing you were going to
keep anyway.

## The line to say out loud
*"If you can sit through a 38% drawdown, you should buy the index — and I'd
tell you that. The question this is built for is what you do if you can't."*

## What you will be asked
**"Then why would I fund a strategy that loses to the index?"** — This is the
right question. The answer is on slide 9: you are not funding the strategy, you
are funding the measurement discipline that can tell you the strategy loses.
Most cannot.
"""),

    (7, "Risk-adjusted", """
## What this slide claims
On return-per-unit-of-risk, the strategies beat holding — and the strongest
result has an objection we agree with.

## What the ratios mean, in plain terms
- **Sharpe** — return per unit of total volatility. Higher is better.
- **Sortino** — same, but only counts downside volatility. Being up a lot is not
  a risk.
- **Calmar** — annual return divided by worst drawdown. The most intuitive:
  "how much do I earn per unit of maximum pain".

`long_reversal` scores 1.30 / 1.96 / 0.89 against buy-and-hold's 1.03 / 1.43 /
0.63 — better on all three.

## The objection, which you should raise yourself
`long_reversal` buys stocks that have fallen for three years. The decade tested
is one where exactly those names recovered hardest. **386 trades in one
favourable regime is not an established edge.** It is a hypothesis with
supporting evidence, and slide 10 makes testing it priority one.

## The second box — the most interesting finding
Running all twelve strategies together produced the **deepest drawdown in the
whole study** (40.31%), worse than four of its own components. Four decorrelated
strategies achieved nearly the same return at 23.67%.

Diversification running backwards is unusual. With a quant-literate investor,
this is the result most likely to start a real technical conversation — it
signals you measured rather than assumed.

## What you will be asked
**"Is Calmar 0.89 vs 0.63 actually meaningful with 386 trades?"** — *"Honestly,
it's suggestive rather than conclusive. That's why the walk-forward test is the
first thing the money pays for."*
"""),

    (8, "Limitations", """
## What this slide claims
Five specific reasons the favourable numbers may not hold — all found in our own
research, none by a reviewer.

## Why this slide exists at all
It is the most persuasive slide in the deck. Anyone can show good numbers.
Volunteering the reasons they might be wrong, unprompted and in detail, is the
only credible signal that the good numbers were measured to the same standard.

## The five, and what each really means
1. **The out-of-sample test did not work.** *The big one.* Every strategy scored
   BETTER out-of-sample than in-sample. That is backwards — overfitting produces
   the opposite. The split lands near the 2022 market bottom, so everything long
   made money in the "out-of-sample" half. It is a regime split wearing an
   out-of-sample label. **No strategy has yet met a period where its own style
   was out of favour.**
2. **Survivorship bias.** Only instruments that still exist can be tested. This
   flatters every row — including buy-and-hold, so it does not explain away the
   benchmark's win.
3. **Futures mis-sized.** All 20 modelled at full notional, no margin, no
   contract multiplier. Understates both return and risk.
4. **One market, one decade.** US-listed, roughly 2016–2026, a historic bull run.
5. **No live track record.** Zero closed trades.

## Do not rush this slide
Take longer here than feels comfortable. If you are asked a question you cannot
answer, say so — that behaviour is the product.

## What you will be asked
**"If the out-of-sample test failed, is any of this meaningful?"** — *"The
infrastructure results are solid — those are verifiable today. The strategy
results are provisional, and that's exactly what the raise is for."*
"""),

    (9, "Why it is defensible", """
## What this slide claims
The durable asset is not the strategy. It is the ability to find out that a
strategy does not work.

## The left column — five killed hypotheses
Each was believed, tested, and abandoned on this project's own evidence:
- *"Costs destroy the edge"* — turned out to be UK stamp duty specifically, not
  a universal truth. Overturned by testing globally.
- *"Low volatility is the alpha"* — inside its own margin of error.
- *"Winners are being capped"* — the proposed fix (a trailing exit) cut returns
  from 7.44% to 1.22%.
- *"We win on risk-adjusted return"* — as originally stated, false.
- *"More strategies diversify"* — all twelve together drew down deepest.

## The right column — eight bugs, two found by the founder
Emphasise this: **two of the eight were caught by the founder auditing
generated code.** The ~2× accidental leverage inflated every headline return in
the project until it was found. The drawdown bug understated every fall.

That is the working method being funded: machine-generated analysis, audited by
someone who checks it rather than accepting it.

## The line to say
*"Anyone can produce a backtest. The scarce skill is catching the errors that
make backtests lie — and we have a documented record of doing that to our own
work."*

## What you will be asked
**"Isn't this just admitting the strategy doesn't work?"** — *"It's admitting we
don't know yet, which is different, and it's the honest position given what
we've measured. The alternative is telling you it works and being wrong."*
"""),

    (10, "Roadmap", """
## What this slide claims
Four priorities, ordered so that the item most likely to invalidate everything
comes first.

## Why that ordering matters
Most roadmaps lead with growth. This one leads with **a test that could
disprove the product.** If the walk-forward fails, that is worth knowing before
more money goes in, not after — and an investor who understands risk will read
the ordering as a signal about how you make decisions.

## The four
1. **A real out-of-sample test.** Walk-forward across regimes, including periods
   where each style was out of favour. Until it exists, no return figure in this
   deck should be relied on. *Say this sentence exactly.*
2. **Honest futures sizing.** Margin and contract multipliers, so the
   multi-asset numbers mean something. Both return and risk will move — likely
   in the same direction.
3. **Forward track record.** Continuous paper trading on licensed data, with
   closed trades and full provenance, published.
4. **Options data.** The only route to a defined-risk hedge. Nothing currently in
   the book is negatively correlated with anything else in it.

## What you will be asked
**"How long until you know if this works?"** — Give a real answer tied to item 1.
The walk-forward is a compute-and-analysis task, not a multi-year wait; the
honest gate is whether it survives a regime it dislikes. Do not promise a date
for profitability.
"""),

    (11, "The ask", """
## BEFORE YOU SEND THIS DECK
Slide 11 contains **`£[AMOUNT]`** and **`[N]` months**. Replace both. The deck
will not make sense with the brackets still in it.

To set the number: cost out the four roadmap items on slide 10 — mostly your own
time, plus market-data subscriptions (spot FX and options), plus compute for the
walk-forward. Give a figure you can justify line by line, because you will be
asked to.

## What this slide claims
That the money buys validation, not growth.

## The three uses
1. **Research validation** — walk-forward across regimes, honest futures sizing,
   re-running the study against the open questions.
2. **Data** — subscriptions the account does not hold. Spot FX (8 instruments
   currently unpriceable on licensed data) and options data, which is the only
   route to a hedging sleeve.
3. **Forward record** — continuous paper trading, published with provenance.

## The milestone
*A walk-forward result across at least one adverse regime, published in full.*

Tie the raise to that, **not to a return target.** Promising a return number
from evidence this thin is precisely the behaviour the rest of the deck argues
against, and a sophisticated investor will notice the contradiction.

## What you will be asked
**"What do I own?"** — Have an answer ready before the meeting. This deck does
not specify equity, terms, or structure, and you should not improvise them in
the room. If you are not ready to discuss terms, say the raise is being scoped
and you are gathering feedback first — which is a legitimate reason to be
meeting.

**"What happens if the walk-forward fails?"** — *"Then we've spent a modest
amount to learn the strategy doesn't generalise, and the infrastructure is still
worth something. I'd rather find out on this budget than a larger one."*
"""),

    (12, "Close", """
## What this slide claims
A two-column split: what is proven, and what is not.

## Why end here
Ending on "not proven" is counter-intuitive and it is the strongest available
close. It tells the investor that everything in the left column was held to the
same standard as the right — which is the only reason to believe the left
column at all.

## Proven (all verifiable today)
- Runs live on licensed broker data — reproducible in a screen-share
- Every price traceable to a feed and a session
- Risk limits bind: 926 candidates were refused for exposure in a single session
- 725 automated tests; eight material bugs found and fixed

## Not proven
- That the strategies beat holding the index — **on our evidence they did not**
- That any edge survives a regime it dislikes
- Any forward performance whatsoever
- That the multi-asset result survives honest futures sizing

## The closing line
*"Every figure in this deck is reproducible from the repository. If you want to
check any of it, I'll walk you through the file it came from."*

Then stop talking. That offer is the whole pitch, and it is unusual enough to
sit in silence for a moment.

## Required disclaimer
The footer must stay on the slide: research and decision-support software only,
nothing executed automatically, not financial advice, not an offer or
solicitation, past behaviour is not predictive.
"""),
]

for n, title, body in SLIDES:
    path = os.path.join(OUT, f"slide-{n:02d}-{title.lower().replace(' ', '-').replace('—', '').replace('--', '-')}.md")
    with open(path, "w") as handle:
        handle.write(HEADER.format(n=n, title=title) + body.strip() + "\n")
    print("wrote", os.path.basename(path))
