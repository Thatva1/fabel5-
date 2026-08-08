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
from ..research import market_regime as mr
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
}


def settings(config):
    account = (config or {}).get("account", {}) or {}
    cfg = {**DEFAULTS, **((config or {}).get("portfolio") or {})}
    if cfg["starting_equity"] is None:
        cfg["starting_equity"] = account.get("portfolio_value", 100000)
    if cfg["risk_per_trade_pct"] is None:
        cfg["risk_per_trade_pct"] = account.get("risk_per_trade_pct", 1.0)
    if cfg["max_position_pct"] is None:
        cfg["max_position_pct"] = account.get("max_position_pct", 15.0)
    return cfg


def simulate(candidates, config, market_states=None, returns_lookup=None,
             price_lookup=None, calendar=None):
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

    by_entry = {}
    for candidate in candidates:
        by_entry.setdefault(candidate["entry_date"], []).append(candidate)

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

    cash = equity
    open_positions = []
    taken, skipped = [], {"exposure": 0, "max_positions": 0, "per_market": 0,
                          "risk_off": 0, "correlation": 0, "daily_cap": 0,
                          "size_too_small": 0}
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
        for candidate in sorted(by_entry.get(date, []),
                                key=lambda c: -(c.get("reward_risk") or 0)):
            cap = cfg["max_new_entries_per_day"]
            if cap is not None and entries_today >= cap:
                skipped["daily_cap"] += 1
                continue
            if len(open_positions) >= cfg["max_open_positions"]:
                skipped["max_positions"] += 1
                continue

            market = candidate.get("market")
            if sum(1 for p in open_positions if p.get("market") == market) \
                    >= cfg["max_positions_per_market"]:
                skipped["per_market"] += 1
                continue

            multiplier = _market_multiplier(candidate, market_states, config)
            if multiplier <= 0:
                skipped["risk_off"] += 1
                continue

            if _too_correlated(candidate, open_positions, cfg, returns_lookup):
                skipped["correlation"] += 1
                continue

            base_equity = equity if cfg["compound"] else start_equity
            shares = _size(candidate, base_equity, cfg, multiplier)
            if shares < 1:
                skipped["size_too_small"] += 1
                continue

            value_base = shares * candidate["entry_price"] * candidate["fx_in"]
            current = sum(p["value_base"] for p in open_positions)
            if current + value_base > base_equity * cfg["max_gross_exposure_pct"] / 100:
                skipped["exposure"] += 1
                continue

            position = _open(candidate, shares, value_base, multiplier)
            open_positions.append(position)
            cash -= value_base
            entries_today += 1

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
    """
    threshold = cfg.get("correlation_threshold")
    if not threshold or not returns_lookup or not open_positions:
        return False
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
            joined = mine.to_frame("a").join(theirs.to_frame("b"), how="inner").dropna()
            if len(joined) < 20:
                continue
            if float(joined["a"].corr(joined["b"])) >= threshold:
                return True
    except Exception:
        return False
    return False
