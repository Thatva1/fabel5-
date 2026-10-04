"""Portfolio-level simulation — the layer that makes the numbers achievable.

The per-ticker engine answers "did this rule set find good setups?". It cannot
answer "could I actually have traded them", because it sizes every instrument in
isolation against the full book. Run 93 instruments that way and you quietly
hold 14 positions at once on a £100k account — roughly 2x leverage that the live
risk gate would have refused. Every headline figure this project produced before
this module existed was inflated that way.

What this adds, all of which the live system already assumes:

  * **Compounding.** Position size scales with CURRENT equity, not the starting
    balance. Without it, a strategy that doubled the account was still sizing
    every trade off the original £100k, understating a compounding system's
    returns and making the benchmark comparison unfair.
  * **A gross exposure cap.** Total open exposure cannot exceed the configured
    limit. Leverage becomes a decision instead of an accident.
  * **Contention.** When more candidates arrive than there is room for, the best
    ones by reward:risk are taken and the rest are recorded as skipped.
  * **Correlation.** Seven longs in correlated large caps is one bet with seven
    tickets, and modelled drawdown is understated accordingly.
  * **A market filter hook.** Risk-off can skip or half-size new entries.

The look-ahead guarantee is inherited: candidates are produced by the per-ticker
replay, which can only ever see bars up to the signal date.
"""
from ..research import indicators, market_regime as mr
from . import metrics

DEFAULTS = {
    "starting_equity": None,          # defaults to account.portfolio_value
    "compound": True,
    "max_gross_exposure_pct": 60.0,   # matches the live risk gate's own cap
    "max_open_positions": 8,
    "max_positions_per_market": 4,
    "risk_per_trade_pct": None,       # defaults to account.risk_per_trade_pct
    "max_position_pct": None,         # defaults to account.max_position_pct
    "max_new_entries_per_day": None,  # None = unlimited
    "correlation_threshold": None,    # e.g. 0.8 to reject correlated candidates
    "correlation_lookback": 60,
    # The most of the exposure budget any ONE strategy may hold, as a percentage
    # of it. None = no cap, which is how a strategy that fires on almost
    # everything ends up being the whole book. `strategy_share_pct` overrides
    # it for named strategies.
    "max_strategy_share_pct": None,
    "strategy_share_pct": {},
    # One position per instrument, claimed in strategy_priority order — what the
    # live book does. Off by default because the per-strategy study wants each
    # strategy measured on its own, including on instruments another holds.
    "one_position_per_ticker": False,
    # How candidates that the rules cannot otherwise separate are ordered.
    #   strength — by how strongly each candidate's own rule rates it (its 12-1
    #              return, the depth of its fall, its beta), as a percentile
    #              among that strategy's candidates the same day; then by a
    #              fixed, size-blind hash. The rule picks the stock.
    #   arrival  — reward:risk, then the order instruments were listed in. The
    #              old behaviour. Reward:risk is a fixed multiple per strategy,
    #              so in practice the list order decided, and the list is most
    #              liquid first: the same trades scored alphabetically moved
    #              ts_momentum from 18.5% a year to 10.2%.
    "tie_break": "strength",
    # list      — strategy_priority decides which strategy claims an instrument
    # composite — strategies.composite scores the agreement between them
    "ranking": "list",
    # On which days new positions may open. "paper" reads the live book's own
    # paper.rebalance_every, so the two cannot drift apart; monthly, weekly,
    # daily and half_day may also be named directly.
    "entry_schedule": "paper",
    # Smallest position worth opening, % of equity. None = the live book's
    # paper.min_position_pct. Below it a position cannot move the result but
    # still pays commission and takes a slot.
    "min_position_pct": None,
    # Refuse an instrument whose exposure the book already holds under another
    # name (spot sterling and the sterling future), as the live book does.
    "duplicate_exposure": True,
}

# The live book's floor when paper.min_position_pct is not set. Must equal
# paper/session.py DEFAULTS["min_position_pct"]; a test holds them together.
PAPER_MIN_POSITION_PCT = 0.5


def settings(config):
    account = (config or {}).get("account", {}) or {}
    cfg = {**DEFAULTS, **((config or {}).get("portfolio") or {})}
    if cfg["starting_equity"] is None:
        cfg["starting_equity"] = account.get("portfolio_value", 100000)
    if cfg["risk_per_trade_pct"] is None:
        cfg["risk_per_trade_pct"] = account.get("risk_per_trade_pct", 1.0)
    if cfg["max_position_pct"] is None:
        cfg["max_position_pct"] = account.get("max_position_pct", 15.0)
    paper = (config or {}).get("paper") or {}
    if cfg["min_position_pct"] is None:
        cfg["min_position_pct"] = float(paper.get("min_position_pct", PAPER_MIN_POSITION_PCT))
    if cfg["entry_schedule"] == "paper":
        # A config with no live book has no schedule to copy: every day.
        cfg["entry_schedule"] = str(paper.get("rebalance_every") or "daily").lower()
    return cfg


class ReturnsMatrix:
    """Daily returns for a whole universe on one date grid, for the correlation cap.

    The cap compares every candidate with every open position over the last
    sixty trading days. Done pair by pair with a pandas join, a six-month
    backtest did not finish in half an hour: tens of thousands of candidates
    against up to 120 positions. This holds the returns as one array and
    answers each candidate in a single vectorised pass, with the same rule —
    the last `window` trading days up to the entry date, compared only on days
    both traded, at least 20 of them, correlation at or above the threshold.

    Callable as a returns lookup too, so anything expecting fn(ticker) -> Series
    still works.
    """

    def __init__(self, frames):
        import numpy as np
        import pandas as pd

        columns = {}
        for ticker, df in (frames or {}).items():
            series = df["Close"].pct_change().dropna()
            series.index = [str(d)[:10] for d in series.index]
            columns[ticker] = series[~series.index.duplicated(keep="last")]
        table = pd.DataFrame(columns).sort_index()
        self.dates = list(table.index)
        self.columns = {t: j for j, t in enumerate(table.columns)}
        self.values = table.to_numpy(dtype=float)
        self._np = np

    def __call__(self, ticker):
        import pandas as pd
        j = self.columns.get(ticker)
        if j is None:
            return None
        return pd.Series(self.values[:, j], index=self.dates).dropna()

    def too_correlated(self, ticker, date, others, threshold, window):
        np = self._np
        j = self.columns.get(ticker)
        cols = [self.columns[o] for o in others if o in self.columns]
        if j is None or not cols:
            return False
        import bisect
        end = bisect.bisect_right(self.dates, str(date)[:10])
        # The candidate's own last `window` trading days, as the live rule takes.
        mine = self.values[:end, j]
        rows = np.flatnonzero(~np.isnan(mine))[-int(window):]
        if len(rows) < 20:
            return False
        y = mine[rows][:, None]
        x = self.values[rows][:, cols]
        both = ~np.isnan(x)
        n = both.sum(axis=0)
        ys, xs = np.where(both, y, 0.0), np.where(both, x, 0.0)
        sy, sx = ys.sum(axis=0), xs.sum(axis=0)
        syy, sxx, sxy = (ys * ys).sum(axis=0), (xs * xs).sum(axis=0), (xs * ys).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = (n * sxy - sx * sy) / np.sqrt((n * syy - sy * sy) * (n * sxx - sx * sx))
        corr = np.where(n >= 20, corr, np.nan)
        return bool(np.nanmax(corr, initial=-1.0) >= threshold)


def returns_lookup_from(frames):
    """Returns for the correlation cap, built from the same closes the paper
    session uses. A ReturnsMatrix, so a backtest can afford the check."""
    return ReturnsMatrix(frames)


def entry_days(dates, schedule):
    """The dates on which new positions may open, or None for every day.

    The live book opens positions only when a rebalance is due. Daily bars are
    the backtest's finest unit, so `half_day` and `daily` both mean every
    trading day; `weekly` and `monthly` mean the first trading day of each.
    """
    if schedule in ("daily", "half_day", "any"):
        return None
    seen, allowed = set(), set()
    for date in dates:
        if schedule == "weekly":
            from datetime import date as _date
            year, week, _ = _date.fromisoformat(date[:10]).isocalendar()
            window = (year, week)
        else:
            window = date[:7]
        if window not in seen:
            seen.add(window)
            allowed.add(date)
    return allowed


def interleave_segments(items, ticker_of):
    """Offer the slots to each asset class — and each equity venue — in turn.

    The live book's rule, shared so the backtest applies it too. The order
    within a segment is kept; segments take turns in the order their best item
    appears. A segment with nothing to offer drops out, so this never forces a
    trade to fill a quota; it only stops one segment taking every slot because
    it happened to be listed first.
    """
    from ..core.market_clock import venue_for
    from ..markets import asset_class_of

    def bucket_key(ticker):
        asset_class = asset_class_of(ticker)
        return asset_class if asset_class != "equity" else f"equity:{venue_for(ticker)}"

    buckets = {}
    for item in items:
        buckets.setdefault(bucket_key(ticker_of(item)), []).append(item)
    order = list(buckets)
    out = []
    while any(buckets[k] for k in order):
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
    return out


def strategy_cap(strategy, cfg, equity):
    """The most notional one strategy may hold, in base currency. None = uncapped.

    A share of the EXPOSURE BUDGET rather than of the position count: the count
    means nothing on the first trade of a new book, and positions are different
    sizes. Shared with the live book so the two cannot disagree about it.
    """
    share = (cfg.get("strategy_share_pct") or {}).get(
        strategy, cfg.get("max_strategy_share_pct"))
    if share is None:
        return None
    return equity * float(cfg["max_gross_exposure_pct"]) / 100 * float(share) / 100


def neutral_order(ticker, date):
    """A fixed order that knows nothing about the instrument.

    The last tie-break. Anything derived from the instrument itself — its
    listing order, its size, its name — carries a bias into which names get
    held; a hash of the name and the date does not, and gives the same answer
    every time it is asked.
    """
    import hashlib
    return hashlib.md5(f"{ticker}|{date}".encode()).hexdigest()


def strength_percentiles(items, strategy_of, strength_of):
    """{id(item): 0..1}, each item's strength within its own strategy's items.

    1.0 is the strongest signal that strategy gave that day. A percentile, not
    the raw number, because a 40% twelve-month return and a beta of 0.6 are
    not comparable; "the best this rule saw today" is. An item without a
    strength sits at 0.5.
    """
    groups = {}
    for item in items:
        groups.setdefault(strategy_of(item), []).append(item)
    out = {}
    for group in groups.values():
        known = sorted((i for i in group if strength_of(i) is not None), key=strength_of)
        for rank, item in enumerate(known, start=1):
            out[id(item)] = rank / len(known)
        for item in group:
            out.setdefault(id(item), 0.5)
    return out


def _claims(candidates, one_per_ticker, priority, tie_break="arrival"):
    """The day's candidates as groups to try in order; at most one per group opens.

    Without one_position_per_ticker every candidate is its own group. With it,
    a group is everything that fired on one instrument, in the order strategies
    may claim it, so that when the first strategy has no budget left the
    instrument passes to the next one instead of being dropped.

    tie_break decides the order across instruments: "strength" by each
    candidate's own rule, "arrival" by reward:risk and then list order.
    """
    last = len(priority)
    if tie_break == "strength":
        pct = strength_percentiles(candidates, lambda c: c.get("strategy"),
                                   lambda c: c.get("strength"))
        def key(c):
            return (-pct[id(c)], priority.get(c.get("strategy"), last),
                    neutral_order(c.get("ticker"), c.get("entry_date")))
        def claim(c):
            return (priority.get(c.get("strategy"), last), -pct[id(c)])
    else:
        def key(c):
            return -(c.get("reward_risk") or 0)
        def claim(c):
            return (priority.get(c.get("strategy"), last), -(c.get("reward_risk") or 0))

    ordered = sorted(candidates, key=key)
    if not one_per_ticker:
        return [[c] for c in ordered]
    groups = {}
    for candidate in ordered:
        groups.setdefault(candidate["ticker"], []).append(candidate)
    claimed = [sorted(group, key=claim) for group in groups.values()]
    if tie_break == "strength":
        # Instruments in the order of the strategy that claims them first.
        claimed.sort(key=lambda group: key(group[0]))
    # Then the segments take turns, as the live book's ranking does.
    return interleave_segments(claimed, lambda group: group[0]["ticker"])


def _composite_claims(candidates, edges, matrix, default_correlation, floor):
    """The day's candidates grouped by instrument and ordered by agreement.

    strategies.composite.combine scores the votes on each instrument; the
    groups come out best score first, each holding the trades that point the
    agreed way, strongest measured edge first, so a full strategy still hands
    the instrument to the next contributor. Disagreement or a score at the
    floor drops the instrument.
    """
    from ..strategies import composite

    pct = strength_percentiles(candidates, lambda c: c.get("strategy"),
                               lambda c: c.get("strength"))
    by_ticker = {}
    for candidate in candidates:
        by_ticker.setdefault(candidate["ticker"], []).append(candidate)
    scored = []
    for ticker, group in by_ticker.items():
        votes = {c["strategy"]: (1 if c.get("direction") == "long" else -1) for c in group}
        verdict = composite.combine(votes, edges, matrix,
                                    default_correlation=default_correlation)
        if not verdict["direction"] or verdict["score"] <= floor:
            continue
        aligned = sorted((c for c in group if c.get("direction") == verdict["direction"]),
                         key=lambda c: -(edges.get(c["strategy"]) or 0))
        if not aligned:
            continue
        best = aligned[0]
        scored.append(((-verdict["score"], -pct[id(best)],
                        neutral_order(ticker, best.get("entry_date"))), aligned))
    scored.sort(key=lambda pair: pair[0])
    return [aligned for _, aligned in scored]


def simulate(candidates, config, market_states=None, returns_lookup=None,
             price_lookup=None, calendar=None, composite_inputs=None):
    """Walk the calendar, opening and closing positions under portfolio limits.

    candidates: per-share trade descriptions from the engine. Each needs
      ticker, market, currency, entry_date, exit_date, entry_price, stop,
      exit_price, fx_in, fx_out, reward_risk, and per-share economics.
    market_states: {(index_symbol, date): "RISK_ON"|"RISK_OFF"} for the filter.
    returns_lookup: fn(ticker) -> dated return Series, for the correlation check.

    Returns {equity_curve, dates, trades, skipped, metrics}.
    """
    cfg = settings(config)
    equity = float(cfg["starting_equity"])
    start_equity = equity
    one_per_ticker = bool(cfg["one_position_per_ticker"])
    # A share cap is a statement about a MIX. A run holding one strategy has no
    # mix to balance, and capping it would only throttle the measurement of
    # that strategy on its own — every per-strategy study and sweep is one.
    if len({c.get("strategy") for c in candidates}) <= 1:
        cfg = {**cfg, "max_strategy_share_pct": None, "strategy_share_pct": {}}
    from . import engine          # here, not at the top: engine imports nothing of ours
    priority = {name: rank for rank, name in
                enumerate(engine.settings(config)["strategy_priority"])}
    tie_break = cfg["tie_break"]
    by_composite = cfg["ranking"] == "composite"
    if by_composite:
        # The composite chooses one strategy per instrument by construction.
        one_per_ticker = True
        from ..strategies import composite
        edges, matrix = composite_inputs or (composite.load_edges()[0],
                                             composite.load_correlations())
        composite_cfg = (config or {}).get("composite") or {}
        default_corr = float(composite_cfg.get("unknown_correlation",
                                               composite.UNKNOWN_CORRELATION))
        floor = float(composite_cfg.get("min_score", 0.0))

    by_entry = {}
    for candidate in candidates:
        by_entry.setdefault(candidate["entry_date"], []).append(candidate)
    per_market_cap = cfg["max_positions_per_market"]
    from ..markets import exposure_group

    # The date axis. A full trading calendar when one is supplied, so open
    # positions can be marked to market every day; otherwise only event days,
    # which makes drawdown REALISED-only and therefore understated — an open
    # loser stays invisible until it closes.
    event_dates = sorted({c["entry_date"] for c in candidates}
                         | {c["exit_date"] for c in candidates if c.get("exit_date")})
    if not event_dates:
        return {"equity_curve": [start_equity], "dates": [], "trades": [],
                "skipped": {}, "final_equity": start_equity,
                "marked_to_market": bool(calendar),
                "metrics": metrics.summarise_curve([start_equity])}
    dates = ([d for d in calendar if event_dates[0] <= d <= event_dates[-1]]
             if calendar else event_dates)
    allowed_entry = entry_days(dates, cfg["entry_schedule"])

    cash = equity
    open_positions = []
    taken, skipped = [], {"exposure": 0, "max_positions": 0, "per_market": 0,
                          "risk_off": 0, "correlation": 0, "daily_cap": 0,
                          "size_too_small": 0, "strategy_share": 0,
                          "ticker_held": 0, "not_a_rebalance_day": 0,
                          "duplicate_exposure": 0}
    curve, curve_dates = [], []

    for date in dates:
        # 1. Close first. Capital released today is available today — the
        #    alternative silently forbids rolling one position into another.
        still_open = []
        for position in open_positions:
            if position["exit_date"] and position["exit_date"] <= date:
                cash += position["value_base"] + position["pnl_base"]
                taken.append(position)
            else:
                still_open.append(position)
        open_positions = still_open
        equity = cash + sum(p["value_base"] for p in open_positions)

        # 2. Open new ones, best reward:risk first — the scarce resource is
        #    exposure, so it should go to the best available setup.
        entries_today = 0
        today = by_entry.get(date, [])
        if allowed_entry is not None and date not in allowed_entry:
            skipped["not_a_rebalance_day"] += len(today)
            today = []
        groups = (_composite_claims(today, edges, matrix, default_corr, floor)
                  if by_composite else
                  _claims(today, one_per_ticker, priority, tie_break))
        for claims in groups:
            for candidate in claims:
                cap = cfg["max_new_entries_per_day"]
                if cap is not None and entries_today >= cap:
                    skipped["daily_cap"] += 1
                    continue
                if len(open_positions) >= cfg["max_open_positions"]:
                    skipped["max_positions"] += 1
                    continue
                if one_per_ticker and any(p["ticker"] == candidate["ticker"]
                                          for p in open_positions):
                    skipped["ticker_held"] += 1
                    continue

                market = candidate.get("market")
                if per_market_cap is not None and sum(
                        1 for p in open_positions if p.get("market") == market) \
                        >= per_market_cap:
                    skipped["per_market"] += 1
                    continue

                # The checks below run in the live book's order: a full book
                # first (cheap), then the same exposure under another name,
                # then correlation (the expensive one).
                base_equity = equity if cfg["compound"] else start_equity
                if sum(p["value_base"] for p in open_positions) \
                        >= base_equity * cfg["max_gross_exposure_pct"] / 100:
                    skipped["exposure"] += 1
                    continue
                if cfg["duplicate_exposure"]:
                    group = exposure_group(candidate["ticker"])
                    if group is not None and any(
                            exposure_group(p["ticker"]) == group for p in open_positions):
                        skipped["duplicate_exposure"] += 1
                        continue

                multiplier = _market_multiplier(candidate, market_states, config)
                if multiplier <= 0:
                    skipped["risk_off"] += 1
                    continue

                if _too_correlated(candidate, open_positions, cfg, returns_lookup):
                    skipped["correlation"] += 1
                    continue

                shares = _size(candidate, base_equity, cfg, multiplier)
                if shares < 1:
                    skipped["size_too_small"] += 1
                    continue

                value_base = shares * candidate["entry_price"] * candidate["fx_in"]
                if value_base < base_equity * float(cfg["min_position_pct"]) / 100:
                    skipped["size_too_small"] += 1
                    continue
                current = sum(p["value_base"] for p in open_positions)
                if current + value_base > base_equity * cfg["max_gross_exposure_pct"] / 100:
                    skipped["exposure"] += 1
                    continue

                # The strategy's own budget, checked last so that a strategy
                # refused here hands the instrument to the next claimant.
                limit = strategy_cap(candidate.get("strategy"), cfg, base_equity)
                if limit is not None:
                    held = sum(p["value_base"] for p in open_positions
                               if p.get("strategy") == candidate.get("strategy"))
                    if held + value_base > limit:
                        skipped["strategy_share"] += 1
                        continue

                position = _open(candidate, shares, value_base, multiplier)
                open_positions.append(position)
                cash -= value_base
                entries_today += 1
                break

        # 3. Mark to market. Open positions are valued at TODAY's price, so a
        #    position that is 20% underwater shows in the drawdown now rather
        #    than only on the day it closes.
        marked = 0.0
        for position in open_positions:
            marked += _mark(position, date, price_lookup)
        curve.append(round(cash + marked, 2))
        curve_dates.append(date)

    # Anything still open at the end closes at its last known exit.
    for position in open_positions:
        cash += position["value_base"] + position["pnl_base"]
        taken.append(position)
    equity = cash
    if curve:
        curve[-1] = round(equity, 2)

    return {
        "equity_curve": curve,
        "dates": curve_dates,
        "trades": sorted(taken, key=lambda t: t.get("entry_date") or ""),
        "skipped": skipped,
        "final_equity": round(equity, 2),
        # Without a calendar and price lookup the curve only moves when trades
        # close, so drawdown is realised-only and understated. Say so rather
        # than let the figure be read as mark-to-market.
        "marked_to_market": bool(calendar and price_lookup),
        "metrics": metrics.summarise_curve(curve, curve_dates,
                                           periods_per_year=_periods_per_year(curve_dates)),
    }


def _mark(position, date, price_lookup):
    """Today's value of an open position, in base currency.

    Falls back to cost basis when no price is available — that flattens the
    curve rather than inventing a move, and `marked_to_market` reports whether
    it happened at all. FX is held at the entry rate: currency moves are
    second-order against price moves for drawdown, and marking both would
    need a full daily rate history per position.
    """
    if price_lookup is None:
        return position["value_base"]
    try:
        price = price_lookup(position["ticker"], date)
    except Exception:
        price = None
    if price is None or price <= 0:
        return position["value_base"]

    fx = position.get("fx_in") or 1.0
    if position.get("direction") == "short":
        # A short gains as price falls: value = entry proceeds + unrealised move.
        move = (position["entry_price"] - price) * position["shares"] * fx
        return position["value_base"] + move
    return position["shares"] * price * fx


def _periods_per_year(dates):
    """Equity points here are event days, not calendar days, so annualising on
    252 would overstate the period. Derive it from the actual span."""
    if len(dates) < 2:
        return 252
    try:
        from datetime import date as _date
        first = _date.fromisoformat(str(dates[0])[:10])
        last = _date.fromisoformat(str(dates[-1])[:10])
        years = max((last - first).days / 365.25, 1e-6)
        return max(len(dates) / years, 1.0)
    except Exception:
        return 252


def _size(candidate, equity, cfg, multiplier):
    """Whole shares from risk budget and concentration cap, both scaled to the
    CURRENT book — this is what makes the run compound."""
    risk_per_share = abs(candidate["entry_price"] - candidate["stop"])
    if risk_per_share <= 0 or candidate["entry_price"] <= 0:
        return 0
    fx_in = candidate.get("fx_in") or 1.0

    risk_budget_base = equity * cfg["risk_per_trade_pct"] / 100 * multiplier
    max_value_base = equity * cfg["max_position_pct"] / 100 * multiplier

    by_risk = (risk_budget_base / fx_in) / risk_per_share
    by_cap = (max_value_base / fx_in) / candidate["entry_price"]
    return int(min(by_risk, by_cap))


def _open(candidate, shares, value_base, multiplier):
    per_share = candidate["gross_per_share"]
    gross = per_share * shares
    costs = candidate["cost_fixed"] + candidate["cost_rate"] * shares
    pnl = gross - costs
    return {
        **{k: candidate.get(k) for k in
           ("ticker", "market", "currency", "strategy", "strategy_label", "regime",
            "direction", "signal_date", "entry_date", "exit_date", "entry_price",
            "stop", "target", "exit_price", "exit_reason", "bars_held",
            "r_multiple", "reward_risk", "fx_in", "fx_out", "exit_style")},
        "shares": shares,
        "size_multiplier": multiplier,
        "value_base": round(value_base, 2),
        "gross_pnl": round(gross, 2),
        "costs": round(costs, 2),
        "pnl": round(pnl, 2),
        "pnl_base": round(pnl * (candidate.get("fx_out") or 1.0), 2),
    }


def _market_multiplier(candidate, market_states, config):
    if not market_states:
        return 1.0
    index = candidate.get("index_symbol")
    if not index:
        return 1.0
    state = market_states.get((index, candidate["entry_date"]))
    if state is None:
        return 1.0
    return mr.size_multiplier(state, config)


def _too_correlated(candidate, open_positions, cfg, returns_lookup):
    """Reject a candidate that moves with what is already held.

    Position COUNT is a poor proxy for risk: four UK banks bought on the same
    day are one bet, and a drawdown model that treats them as four independent
    positions understates the real hole.

    Note what a missing `returns_lookup` means: the check cannot run, so every
    candidate passes. That is the correct behaviour — inventing a correlation
    would be worse — but it does mean turning `correlation_threshold` on in
    config does nothing unless the caller supplies the returns. Callers that
    care must pass it.
    """
    threshold = cfg.get("correlation_threshold")
    if not threshold or not returns_lookup or not open_positions:
        return False
    if hasattr(returns_lookup, "too_correlated"):
        return returns_lookup.too_correlated(
            candidate["ticker"], candidate["entry_date"],
            [p["ticker"] for p in open_positions], threshold,
            int(cfg["correlation_lookback"]))
    try:
        mine = returns_lookup(candidate["ticker"])
        if mine is None:
            return False
        window = int(cfg["correlation_lookback"])
        mine = mine.loc[:candidate["entry_date"]].tail(window)
        if len(mine) < 20:
            return False
        for position in open_positions:
            theirs = returns_lookup(position["ticker"])
            if theirs is None:
                continue
            theirs = theirs.loc[:candidate["entry_date"]].tail(window)
            # Paired on the trading day, not the raw index. Two instruments on
            # different exchanges carry different timezone stamps, and joining
            # those directly matches nothing — so a London name and a New York
            # name would read as uncorrelated no matter how they actually move,
            # which is the one case this check most needs to catch.
            joined = indicators.align(mine, theirs)
            if joined is None or len(joined) < 20:
                continue
            if float(joined["t"].corr(joined["b"])) >= threshold:
                return True
    except Exception:
        return False
    return False
