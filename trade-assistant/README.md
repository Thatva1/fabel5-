# Trade Assistant (v2) — Layered Research & Trade-Idea Tool

AI-assisted market research and trade-idea assistant, built in two layers.
**This is a research/decision-support tool — it never trades on its own, and
none of its output is financial advice.** Layer 1 (research) runs by default and
places no orders at all. Layer 2 (Interactive Brokers execution) ships disabled;
when you enable it, every single order still requires your explicit approval and
a typed confirmation. Nothing is ever automated end to end.

## Architecture

```
Layer 1 (v1 product, complete)                          Layer 2 (built, OFF)
┌─────────────────────────────────────────────────┐     ┌──────────────────┐
│ Scanner → Regime → Strategies → Context →       │     │ IBKRBroker       │
│                       ↓          Thesis → Plan  │     │ (ib_async +      │
│                                    → Gate       │ ──► │  IB Gateway,     │
│              ↓                                  │seam │  per-order human │
│   Human approval → Journal → Dashboard          │     │  confirmation)   │
│                                                 │     └──────────────────┘
│ Data: yfinance + FRED + Finnhub                 │
└─────────────────────────────────────────────────┘
```

The scanner **measures**; it no longer decides. A ticker's regime is classified
first, only the strategies suited to that regime run, and an idea exists only
when a strategy's full rule set fires — not when a single indicator twitches.

Design principles (enforced in code, not just documented):

- **Deterministic math vs LLM** — every number that drives a decision
  (sizing, stops, exposure, risk checks) is plain Python in `assistant/risk/`,
  unit-tested in `tests/`. The LLM (`assistant/research/thesis.py`) writes
  narrative only, must cite (source, date) for every figure, and is forbidden
  from stating figures not in its input bundle.
- **Provider-agnostic data layer** — all market data flows through the
  `DataProvider` interface (`assistant/providers/base.py`) and the router:
  prices/fundamentals/FX → yfinance · macro → FRED (fallback yfinance) ·
  news/earnings → Finnhub (fallback yfinance). Caching + rate limiting built in.
  Every figure carries `source`, `timestamp`, and a `live/delayed/eod` freshness tag.
- **Broker seam** — `assistant/broker/base.py` defines the `Broker` interface;
  the only Layer 1 implementation is `PaperBroker`, whose `place_order` refuses
  by design. Layer 2 = one new class, zero pipeline changes.
- **Multi-currency** — all exposure/risk math normalizes to `base_currency`
  (config.yaml, default USD) via deterministic FX conversion (`assistant/core/fx.py`).
- **Config-driven risk rules** — everything in `config.yaml`; nothing hard-coded.
- **Pluggable strategies** — each strategy is one module behind the `Strategy`
  interface (`assistant/strategies/base.py`); a regime router decides which may
  run. Strategies decide *what* to flag and at what levels; the LLM only
  explains *why*. Adding one touches no existing code.

## Strategy library & regime router

Momentum says "buy strength", mean-reversion says "sell strength". Both are
right, in different markets — run together blindly they cancel out. So each
ticker is labelled first, and only the matching strategies see the data.

**Regime classifier** (`assistant/research/regime.py`) — deterministic, from
ADX (trend strength), price vs a *rising or falling* 200-day MA (direction), and
Bollinger band width percentile (volatility state):

| Regime | Meaning | Strategies that run |
|---|---|---|
| `TRENDING_UP` | strong move higher | Momentum (long) |
| `TRENDING_DOWN` | strong move lower | Momentum (short) |
| `SIDEWAYS` | no trend worth following | Mean reversion, Range trading |
| `VOLATILITY_SQUEEZE` | bands compressed to a multi-week low | Squeeze |
| `UNKNOWN` | not enough history | none |

A strong ADX where price and its own 200-day MA disagree is deliberately called
`SIDEWAYS`. That is a turn, not a trend, and trading it as momentum is how you
buy the top.

**The four strategies** (`assistant/strategies/`):

- **Momentum** — breakout above the 20-day high in a confirmed uptrend, with
  volume confirmation, an overextension reject (RSI > 80, or price already more
  than 1 ATR past the level), and an optional relative-strength requirement.
  Mirror rules for shorts in a downtrend.
- **Mean reversion** — fades RSI extremes stretched outside the Bollinger band
  back toward the 20-day average, but only once the move has *stalled*. Never
  runs inside a strong trend: "overbought" can stay overbought for months.
- **Range trading** — finds a horizontal band that price has actually respected
  (minimum touches at both edges, and a width that is neither noise nor a
  disguised trend), buys the floor, sells the ceiling, targets the far side.
- **Squeeze** — refuses to guess direction. While price is inside the bands it
  produces a *watch item*, never a plan. On a confirmed break it hands off to
  Momentum's own filters; if those reject it, it stays a watch item.

**Tuning** — every threshold is in `config.yaml` under `regime:` and
`strategies:`. Any strategy can be switched off (`enabled: false`), retuned (set
any of its own settings), or moved to different conditions (`regimes: [...]`).
Deleting a block restores that strategy's defaults.

### Adding your own strategy

1. Write `assistant/strategies/your_strategy.py`, subclassing `Strategy`. Set
   `name`, `label`, `description`, `regimes`, and `defaults`, then implement
   `detect(ctx)` returning `self.build(...)` ideas.
2. Add the class to `BUILTIN` in `assistant/strategies/registry.py`.
3. Optionally add a `strategies.your_strategy:` block to `config.yaml`.

That's it — the router, pipeline, risk gate, journal and dashboard pick it up
automatically. Rules a strategy must follow: it computes no money figures (it
proposes price *levels*; `assistant/risk/` sizes them), it does no I/O, and an
idea without a valid stop is dropped, because undefined risk cannot be sized.

## Short-selling rules

Shorts clear a higher bar than longs, in `assistant/risk/gate.py` and
`borrow.py`. Hard failures: no stop, a stop on the wrong side of the entry, or a
name confirmed unborrowable. Borrow that *cannot* be verified returns `unknown`
and flags rather than blocks (set `block_if_borrow_unknown: true` to refuse
those too) — a broker error can never read as permission. Separate, tighter
caps apply: `max_short_position_pct` (8% vs 15% for longs),
`max_total_short_exposure_pct`, and `max_open_shorts`. Crowded shorts are
flagged with their short interest as a squeeze warning.

Once you know from your broker whether something can be shorted, record it:

```yaml
short_rules:
  shortable_overrides:
    TSCO.L: true
```

## Journal — which strategy actually works

Every idea is tagged with the strategy and regime that produced it, so the
journal reports performance per strategy, per market, and per combination:

- **Hit rate** — wins over closed trades.
- **P&L** — cash, converted to your base currency at the rate on the day you
  closed, with that rate stored so old trades are never silently rewritten.
- **Expectancy (R)** — average profit per trade in units of the risk taken.
  This is the number that decides a strategy's future: it is size-independent,
  so a small-sizing strategy is judged fairly against a large-sizing one.
  Positive expectancy at a 40% hit rate beats negative expectancy at 70%.
- **Profit factor** — gross profit over gross loss; above 1.0 makes money.

P&L and expectancy need the **exit price** you actually got, entered when you
mark an idea won or lost. Without it the trade still counts toward your hit
rate, but is excluded from P&L and the totals are labelled *partial* rather
than quietly treating the missing trade as zero.

## Backtesting

```bash
python run.py backtest                      # your watchlist, 10 years
python run.py backtest TSCO.L VOD.L --years 5
```

Replays the **same** scanner, regime classifier, strategies and sizer the live
scan uses — not a reimplementation — over history, and reports hit rate,
expectancy, profit factor and max drawdown per strategy per regime.

Every modelling assumption is deliberately pessimistic, because a backtest that
flatters itself is worse than none:

- **No look-ahead.** At bar *i* the strategies receive `df[:i+1]`, a slice that
  cannot contain the future. `test_backtest.py` proves appending later bars
  never changes an earlier signal.
- **Limit entries.** A plan names an entry price, so the engine models a limit
  order live for one bar: you get that price or better, or no trade. Missed
  fills are counted and reported.
- **Fills that drift onto the stop are skipped.** A market fill landing near the
  stop shrinks the risk the position was sized against and inflates every
  R-multiple derived from it. Configurable via `min_risk_fraction`.
- **Gaps through the stop fill at the open**, not the stop — which is how a
  "-1R" trade becomes -3R in reality.
- **Ambiguous bars are scored as losses.** When one bar's range covers both the
  stop and the target there is no intrabar data to say which came first, so the
  stop is always assumed.
- **Costs come off every trade**: commission, two-way slippage, and UK stamp
  duty on purchases. All in `config.yaml → backtest.costs`.

What it cannot test: the thesis engine. Yahoo serves today's fundamentals and
news, not what was known years ago, so only the strategy rules are replayed.
Since the strategies decide the trades and the LLM only explains them, that is
still a fair test of the part that takes risk. See `HONEST_LIMITATIONS` in
`assistant/backtest/__init__.py` — survivorship bias in particular.

Backtest and journal report identical metric names, so historical and live
results can be compared directly.

## Setup

```bash
python3 -m venv venv            # or reuse an existing one
./venv/bin/pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add keys (all optional — the app degrades
gracefully and the dashboard shows which providers are active):

| Key | What it enables | Where to get it (free) |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI-written theses | console.anthropic.com (needs credits) |
| `FRED_API_KEY` | Authoritative macro: rates, CPI, unemployment | fred.stlouisfed.org → My Account → API Keys |
| `FINNHUB_API_KEY` | Timely news + earnings calendar | finnhub.io → register |

## Run

```bash
python run.py serve             # dashboard at http://127.0.0.1:5002
python run.py scan              # CLI watchlist scan
python run.py analyze tesla     # one ticker (company names auto-resolve)
python run.py selftest          # unit tests for all deterministic math
```

## Global by default — not US-only

Macro is pulled per region (`macro_regions` in config.yaml, default `GB, EU, US`):

| Region | Source | Series | Key needed |
|---|---|---|---|
| **UK** | Bank of England IADB | Bank Rate, 10y gilt, SONIA | none |
| **Euro area** | ECB Data Portal | MRO rate, deposit rate, HICP | none |
| **US** | FRED | Fed funds, Treasuries, CPI, unemployment, VIX | free key |
| **Anywhere else** | World Bank | CPI, unemployment, GDP growth | none |

`macro_indicators.yaml` holds the full indicator taxonomy across all 7
categories and 9 countries, each entry tagged `live` / `planned` / `paid` so
nothing is silently dropped. Historical series are largely free; a *forward
calendar* of release times outside the US is the main thing behind a paywall.

Not used: **OECD** (its SDMX endpoint returns HTTP 403 from servers) and **IMF**
(host unreachable). Both were tested; the BoE/ECB/World Bank routes above are
the reliable equivalents.

Search ranks your home markets first via `preferred_exchanges`, so "tesco"
gives `TSCO.L` and "shell" gives `SHEL.L` rather than a Frankfurt or NYSE
cross-listing. UK/EU stocks scan natively in GBP/EUR and the risk gate converts
to your `base_currency`.

## Symbol universe vs scan list

Search covers **every US-listed symbol** (~28,000, pulled once from Finnhub's
free symbol endpoint, cached to `data/symbols_us.json`, refreshed weekly).
Type a ticker *or a company name* anywhere in the app and autocomplete ranks the
obvious answer first — "goldman sachs" gives GS, not its gold ETF; "jp morgan"
gives JPM even though the listing reads "JPMORGAN".

The **scan list** (`watchlist` in config.yaml) is separate and deliberately
small: only those symbols are scanned, so you control what costs time and API
credits. Add to it from the Watchlist view; searching is never limited to it.

Class shares are stored in Yahoo's format (`BRK-B`, not `BRK.B`) — the dotted
form silently returns no price data.

## Layer 2 — Interactive Brokers execution (built, OFF by default)

Execution is **disabled** until you deliberately turn it on. Even then, nothing
is ever sent automatically: the scanner, the LLM, and the pipeline have no path
to `place_order`. Orders originate only from your click plus a typed confirmation.

**Every order must clear all six gates** (`assistant/execution.py`):

1. `execution.enabled: true` in config.yaml
2. You marked the idea **Approved** in the dashboard
3. The idea produced a real trade plan
4. Its risk-gate verdict was not `rejected`
5. Preparing a ticket **re-runs the risk math against your live IBKR portfolio**
   and **re-checks the current price** — an idea that breaches your caps today,
   or whose price has moved past the plan, is refused even if it passed yesterday
6. Confirming requires **typing the ticker exactly**; tickets are single-use and
   expire after 15 minutes so a stale limit price can't be sent

If you un-approve the idea between ticket and confirmation, the order is blocked.
Confirmation atomically claims the ticket in SQLite before contacting IBKR, so
concurrent confirms (double click, retry, two tabs) can never place two orders.
Order state records what IBKR actually reported — `accepted`, `filled`,
`rejected`, `cancelled` — never a blanket "submitted".

With more than one IBKR account, `execution.account` must name the exact account;
otherwise balances and orders could target different accounts and the app refuses
to trade. Paper vs live is determined from the **account id** (`DU`/`DF` = paper),
never the port number, and anything unverified is treated as live.

### Turning it on

1. Install IB Gateway or TWS and log into a **paper account** first.
2. In TWS/Gateway: Global Configuration → API → Settings → tick **Enable ActiveX
   and Socket Clients**, and note the socket port.
3. Set `execution.port` in `config.yaml` to the socket port shown in Gateway
   (conventionally `7497` TWS paper · `4002` Gateway paper · `7496` TWS live ·
   `4001` Gateway live — but Gateway lets you change it, so trust the app's
   `ibkr-check` output over the convention).
4. Turn execution on from the **dashboard header**: type `ENABLE` and click
   **Turn execution ON**. No restart needed. Turning it **OFF** is a single
   click with no confirmation — the safe direction is never obstructed.
   (`execution.enabled` in `config.yaml` is the same switch, if you prefer.)

The header pill shows `IBKR PAPER` (amber) or `★ LIVE MONEY ★` (red) so the mode
is never ambiguous. Verify your setup any time with:

```bash
python run.py ibkr-check
```

Approved ideas then show **Prepare order ticket…** → review the ticket → type the
ticker → the order is placed as an IBKR **bracket**: a limit entry with an
**attached protective stop**, so the stop is held by the broker and goes live the
instant the entry fills. An order with no stop is refused outright — the displayed
max loss is only meaningful if the broker is actually holding the stop.

The button is hidden entirely while IBKR is unreachable, and a ticket is refused
if the price has drifted more than 2% from the plan or has already broken the
stop. Run in paper for a good while before considering live.

### Why yfinance is still used even with IBKR connected

IBKR market data needs paid subscriptions per exchange. Tested against this
account, IBKR serves **historical daily bars** (what the scanner needs) but
returns *no* live quotes and refuses fundamentals (`Error 10358: Fundamentals
data is not allowed`). It also provides no macro series and no usable news feed.
So yfinance + FRED + Finnhub still do real work, and research keeps running when
Gateway is closed. Adding an `IBKRProvider` for prices is a sensible option once
you hold market-data subscriptions — it's one new class against `DataProvider`.

## Swapping providers

Implement `DataProvider` (`assistant/providers/base.py`) in a new file and
register it in `router.py`'s routing lists — nothing else changes. Same pattern
for another broker: implement `Broker` (`assistant/broker/base.py`).

## Notes

- The journal DB (`data/journal.db`) carries over your v1 idea history. Ideas
  logged before the strategy library existed show as **untagged** in the
  per-strategy tables — they are kept, never back-filled with a guess.
- `config.yaml → scanner.benchmark` sets the index used for relative strength
  (`^FTSE` by default). Blank switches that check off.
- Watchlist: edit from the dashboard (validated) or `config.yaml` by hand.
- Finnhub's economic calendar is paid-tier; upcoming macro events stay manual
  in `config.yaml → macro_events` on the free plan.
- **Disclaimer:** outputs are automated research summaries, not financial
  advice and not recommendations to buy or sell any security. Markets involve
  risk of loss. Verify everything independently and make your own decisions.
