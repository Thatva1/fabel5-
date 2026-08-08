# Design brief — Trade Assistant dashboard

*A prompt for a design pass on an existing, working app. Copy this whole document
as the brief.*

---

## What this is

A **personal market-research and trade-idea assistant** for a single retail trader.
It scans a watchlist, gathers news/fundamentals/macro data, has an LLM write a
bull-vs-bear thesis, computes a risk-checked trade plan, and stops at a human
approval step. Optionally it can then place that order with Interactive Brokers —
but only after the user explicitly confirms each individual order.

It is **decision-support, not automation**. Nothing trades on its own.

**The user:** one non-programmer retail trader. Not a quant, not a developer.
They read plain English better than dense numeric tables. They use this on a Mac,
mostly desktop, occasionally would like to glance at it on a phone.

**Current stack:** Python/Flask backend, one Jinja template
(`assistant/web/templates/dashboard.html`) with inline CSS and vanilla JS that
polls a single JSON endpoint (`GET /api/state`). No build step, no framework, no
external requests (fully offline/local).

---

## ⚠️ The constraint that makes this different from normal product design

This app can spend real money. Two states must be **impossible to confuse**:

| State | Meaning |
|---|---|
| **PAPER** | Simulated money. Mistakes are free. |
| **LIVE** | Real money. Mistakes are permanent. |

A user who believes they're in paper mode while actually in live mode is the
single worst outcome this design can produce. This has already nearly happened:
the app previously inferred paper-vs-live from a port number convention, and the
user's live account was briefly labelled "PAPER".

**Therefore, unusually for a product design: friction is a feature on the order
path.** Do not optimise the "place order" flow for speed or delight. It should
feel deliberate, slightly effortful, and unambiguous. Meanwhile, *turning
execution off* and *rejecting an idea* should be effortless — the safe direction
is never obstructed.

Please don't design anything that:
- makes placing an order a single click,
- hides or de-emphasises the paper/live indicator,
- uses celebratory/gamified styling around trading actions (no confetti, streaks,
  "you're on fire" — this is somebody's savings),
- implies the tool predicts outcomes or gives advice.

A legally-required disclaimer ("research only, not financial advice") must remain
visible, but it currently eats a lot of prime screen space — making it present but
proportionate is a genuine design problem worth solving.

---

## Verified problems with the current UI

Measured, not guessed:

1. **The page is 31,639px tall — roughly 80 screens of scrolling.** All 49
   journalled ideas render as full expanded cards, oldest to newest, with no
   pagination, filtering, or collapsing. This is the biggest single problem.
2. **Zero responsive design.** No media queries at all. It's usable on mobile only
   by accident (flex-wrap), and the data tables are unreadable there.
3. **Zero semantic HTML or accessibility affordances.** No `<main>`, `<nav>`,
   `<section>`, no ARIA roles, no focus states. Status is encoded in colour alone
   (red/amber/green), which fails for colour-blind users.
4. **The header is a junk drawer.** App title, "Run Scan", a ticker input,
   "Analyze", an AI-status pill, a scan-status message, provider pills, an
   execution pill and an execution on/off toggle all compete in one strip.
5. **No information hierarchy.** The disclaimer, provider health, portfolio
   exposure, and a $100k account balance all have roughly equal visual weight.
6. **Weak operation feedback.** A watchlist scan takes several minutes across 27
   tickers with only a static "Scan in progress…" string — no per-ticker progress,
   no ETA, no cancel. (Two bugs in this session were literally "the user can't
   tell whether anything happened.")
7. **Idea cards are walls of text.** Each contains a 5-sentence thesis paragraph,
   a 7-field numeric grid, a bulleted list of risk flags, a collapsed section with
   four more lists, decision buttons, and outcome logging — all at one visual level.
8. **The journal is buried and analytically thin.** The idea journal is the
   product's long-term value (it's how the user learns whether their theses
   actually work), but it sits at the bottom of an 80-screen page and surfaces a
   single win-rate number with no charts or trend.

---

## What the interface actually has to express

Design against these real states — all of them occur.

**Global status**
- AI thesis engine: on / off / failing (e.g. "API credits exhausted")
- Three data providers, each: `active` (verified working) / `ready` (configured,
  unused) / `degraded` (last request failed, with reason) / `off` (no API key)
- Execution: disabled / enabled-but-broker-unreachable / connected-PAPER /
  connected-LIVE
- Portfolio: value, current exposure % vs cap, open position count, risk-per-trade %

**Watchlist** (currently 27 tickers, user can add/remove any global symbol)
- Per ticker: price, % change, volume vs 20-day average, RSI, flagged/quiet, and a
  list of triggered signals ("Unusual volume: 3.0x", "Breakdown below 20-day low",
  "RSI oversold at 29", "Gap down -8.8%")

**Ideas** (49 and growing; each is a research artifact)
- Ticker, direction (long/short), timestamp
- Risk-gate verdict: `approved_for_review` / `needs_more_research` / `rejected`
- Plain-English thesis (3–6 sentences, with inline data citations)
- Bull case / bear case (parallel lists), catalysts up/down, valuation view,
  balance-sheet risk, data gaps
- Trade plan: entry zone, stop, target, reward:risk, share count, position value,
  max loss, confidence score (0–100) + the reasoning behind that score
- Risk flags: hard failures (blocking) and soft flags (advisory)
- **User decision:** approved / needs-research / rejected / pending
- **Outcome logging:** open / win / loss / scratch, plus exit price
- Optional execution panel: prepare ticket → review → type ticker to confirm

**Two-step order flow** (only on approved ideas, only when broker connected)
1. "Prepare order ticket" → re-checks live portfolio risk and current price
2. A ticket showing side, quantity, limit entry, protective stop, max loss, and
   PAPER/LIVE mode → user types the ticker symbol exactly → order is placed

---

## What we'd like from you

1. **A structural rethink, not a reskin.** Most likely: separate the four concerns
   (status / watchlist / idea review queue / journal-analytics) into distinct
   views or a workspace layout, rather than one infinite scroll. Propose the
   navigation model you think fits — tabs, sidebar, master-detail, something else.
2. **A triage-first idea experience.** The user's real job is "which of these 49
   ideas deserve my attention right now?" — currently there is no filtering,
   sorting, grouping, or summarising. Design the queue *and* the detail view.
3. **A real design system**: type scale, spacing, semantic colour tokens (status
   must not rely on hue alone), component patterns for cards/tables/pills/buttons,
   and empty/loading/error states.
4. **Unmissable, non-alarmist safety states.** PAPER vs LIVE, and
   execution-on vs execution-off, should be legible in half a second — without the
   whole UI screaming permanently (alarm fatigue is a real failure mode here).
5. **Progress and feedback patterns** for multi-minute background operations.
6. **Responsive behaviour** for desktop-first, phone-glanceable.
7. **Accessibility**: keyboard navigation, focus states, contrast, screen-reader
   semantics, no colour-only encoding.
8. **Journal analytics**: make thesis-accuracy-over-time a first-class view —
   win rate, by verdict, by confidence bucket, over time.

## Constraints to respect

- **Self-contained**: no CDN fonts/scripts/images. Everything inline or local.
- **No build step preferred.** Vanilla HTML/CSS/JS is ideal. If a framework is
  genuinely warranted, say so explicitly and justify it — the owner is a
  non-programmer who has to be able to run this.
- The backend already returns everything needed from `GET /api/state`; new API
  fields are possible but flag them so they can be implemented.
- Dark theme is the current default and is preferred, but a light mode is welcome.
- All figures come with `source`, `timestamp`, and a `live`/`delayed`/`eod`
  freshness tag — surfacing data provenance/staleness is encouraged, because
  trusting a stale number is a real hazard.

## Deliverables we'd find most useful

1. A recommended information architecture / navigation model with rationale.
2. Hi-fi mockups of: the main dashboard, the idea queue, an idea detail view, and
   the order-confirmation flow (PAPER and LIVE variants).
3. A component/token spec we can implement directly.
4. Notes on anything in the current flow you think is *unsafe* rather than merely
   ugly — you may be seeing something we've missed.

---

*Context for whoever picks this up: the backend is well covered by 123 automated
tests, including ~30 dedicated to preventing accidental or duplicate orders. The
logic is solid; the interface is the weak layer. Nothing in the visual design
should weaken the safety guarantees described above.*
