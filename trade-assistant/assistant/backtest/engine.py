"""The replay loop: walk history one bar at a time and run the real strategies.

The whole value of this file rests on one property — at bar `i`, nothing may
see bar `i+1`. That is enforced structurally rather than by care: the strategies
are handed `df.iloc[start:i+1]`, a slice that physically cannot contain the
future. A backtest with look-ahead does not merely overstate returns, it
reliably turns losing rules into winners, so the guarantee has to be structural.

The same scanner, regime classifier, router and sizing code the live scan uses
runs here. Nothing is reimplemented, so the backtest cannot drift from live
behaviour.
"""
from ..research import market_regime, scanner
from ..risk import sizing
from ..strategies import router as strategy_router
from . import simulator

DEFAULTS = {
    # 400 bars is enough for every indicator in the library (the 200-day average
    # plus 21 bars of slope, and 120 bars of band-width percentile). Capping the
    # window keeps each bar's work constant instead of growing with history,
    # and every indicator is backward-looking, so the numbers are identical to
    # using all history — proven by test_backtest.py.
    "history_window_bars": 400,
    "warmup_bars": 260,          # no signals until the 200-day average is real
    "max_holding_bars": 40,      # give up on a trade that goes nowhere
    # limit       — a limit order at the plan's entry, live for one bar. Fills at
    #               the planned price or better, or not at all. This is what you
    #               would actually do with a plan that names an entry price.
    # next_open   — a market order at the next open. Models chasing.
    # signal_close— fills at the signal bar's close. Impossible in reality;
    #               included only to show how much that assumption flatters.
    "entry_timing": "limit",
    "one_position_per_ticker": True,
    # WHO GETS THE SLOT when several strategies want the same instrument.
    #   per_ticker    — one position per instrument (realistic, but whichever
    #                   strategy fires most often monopolises it)
    #   per_strategy  — each strategy gets its own slot on that instrument, so a
    #                   rare high-quality signal is never blocked by a frequent
    #                   low-quality one. Raises exposure; the live risk gate caps
    #                   that, the backtest does not.
    "position_slots": "per_ticker",
    # Order of preference when two strategies signal on the SAME bar and only
    # one slot exists. Measured global expectancy, best first — not alphabetical
    # and not registry order, which is what it silently used before.
    "strategy_priority": ["mean_reversion", "momentum", "squeeze", "range_trading"],
    # A market-order fill that drifts toward the stop shrinks the risk it was
    # sized against. Below this fraction of the planned risk the setup is no
    # longer the one that was approved, so it is skipped rather than entered
    # with a stop a few pence away — which is what manufactures phantom 30R
    # winners out of a collapsed denominator.
    "min_risk_fraction": 0.5,
    # Exit management. "fixed" takes the plan's target; "trailing" replaces it
    # with an ATR stop that follows price, to test whether winners are capped.
    "exit_style": "fixed",
    "trail_atr": 2.5,
    "trail_activate_at_r": 1.0,
    "scale_out_fraction": 0.0,      # e.g. 0.5 = bank half at target, trail the rest
}


def _per_share_economics(ticker, idea, trade, entry_price, costs, config):
    """Split a trade's economics into share-independent parts.

    Gross P&L is linear in share count, and so is the percentage part of costs
    (stamp duty, slippage). Only commission is fixed. Separating them lets the
    portfolio layer size a trade against live equity without replaying the
    price path, so one expensive replay can serve many portfolio settings.
    """
    shares = trade["shares"] or 1
    duty_rate = 0.0
    suffixes = tuple(s.upper() for s in (costs.get("stamp_duty_suffixes") or ()))
    if suffixes and str(ticker).upper().endswith(suffixes):
        purchase = (trade["entry_price"] if idea.direction == "long"
                    else trade["exit_price"])
        duty_rate = abs(purchase) * costs["stamp_duty_pct"] / 100
    return {
        "gross_per_share": round(trade["gross_pnl"] / shares, 6),
        "cost_fixed": costs["commission_per_trade"] * 2,
        "cost_rate": round(duty_rate, 6),
        "market": _market_of(ticker),
    }


def _market_of(ticker):
    name = str(ticker or "").upper()
    return "US" if "." not in name else "." + name.rsplit(".", 1)[-1]


def resolve_entry(direction, planned_entry, bar, timing):
    """What price this trade actually gets, or None if it never fills.

    A limit order is the honest model for a plan that names its entry: you get
    the planned price or better, and sometimes you get nothing because the
    market left without you. Missed fills are a real cost of limit orders, and
    a backtest that quietly converts them into perfect fills is inventing
    trades that could not have happened.
    """
    open_, high, low = float(bar["Open"]), float(bar["High"]), float(bar["Low"])
    if timing == "signal_close":
        return planned_entry
    if timing == "next_open":
        return open_

    if direction == "long":
        if open_ <= planned_entry:
            return open_                      # gapped in your favour
        return planned_entry if low <= planned_entry else None
    if open_ >= planned_entry:
        return open_
    return planned_entry if high >= planned_entry else None


def settings(config):
    return {**DEFAULTS, **((config or {}).get("backtest") or {})}


def backtest_ticker(ticker, df, config, benchmark_closes=None, progress_cb=None,
                    unsized=False):
    """Replay one instrument. Returns {ticker, trades, bars_tested, skipped}.

    unsized=True emits per-share CANDIDATES for the portfolio layer to size
    against live equity, rather than sizing each instrument in isolation
    against the whole book — which is what produced the accidental leverage.
    """
    cfg = settings(config)
    costs = simulator.cost_config_for(ticker, config)
    scan_cfg = config.get("scanner", {}) or {}

    result = {"ticker": ticker, "trades": [], "bars_tested": 0,
              "signals": 0, "watch_items": 0, "missed_fills": 0,
              "skipped_risk_collapsed": 0, "blocked_by_open_position": 0,
              "note": None}

    if df is None or len(df) < cfg["warmup_bars"] + 5:
        result["note"] = (f"Not enough history: {0 if df is None else len(df)} bars, "
                          f"need at least {cfg['warmup_bars'] + 5}.")
        return result

    window_bars = int(cfg["history_window_bars"])
    max_hold = int(cfg["max_holding_bars"])
    timing = cfg["entry_timing"]
    min_risk_fraction = float(cfg["min_risk_fraction"])
    per_strategy_slots = cfg["position_slots"] == "per_strategy"
    priority = {name: rank for rank, name in enumerate(cfg["strategy_priority"])}
    # Bar index each slot frees up. One shared slot per ticker, or one per
    # strategy — the choice that decides whether a frequent strategy can block
    # a rare one for a decade.
    busy_until = {}

    for i in range(int(cfg["warmup_bars"]), len(df) - 1):
        result["bars_tested"] += 1
        if progress_cb and result["bars_tested"] % 250 == 0:
            progress_cb(ticker, result["bars_tested"], len(df))

        if (cfg["one_position_per_ticker"] and not per_strategy_slots
                and i <= busy_until.get("*", -1)):
            continue

        # THE guarantee: this slice ends at i. Nothing downstream can see later.
        start = max(0, i + 1 - window_bars)
        window = df.iloc[start:i + 1]
        bench = None
        if benchmark_closes is not None:
            bench = benchmark_closes.loc[:window.index[-1]]

        snapshot = scanner.scan_ticker(ticker, window, scan_cfg, benchmark_closes=bench)
        if snapshot.get("error"):
            continue

        routed = strategy_router.route(ticker, window, snapshot, config)

        # Contention: rank the candidates before choosing, so the slot goes to
        # the best available setup rather than to whichever strategy the
        # registry happened to list first. Ties break on reward:risk.
        candidates = sorted(
            routed["ideas"],
            key=lambda idea: (priority.get(idea.strategy, len(priority)),
                              -(idea.reward_risk or 0)))

        for idea in candidates:
            if idea.status != "actionable":
                result["watch_items"] += 1
                continue
            result["signals"] += 1

            slot = idea.strategy if per_strategy_slots else "*"
            if cfg["one_position_per_ticker"] and i <= busy_until.get(slot, -1):
                result["blocked_by_open_position"] += 1
                continue

            entry_index = i if timing == "signal_close" else i + 1
            if entry_index >= len(df):
                continue
            entry_price = resolve_entry(idea.direction, idea.entry,
                                        df.iloc[entry_index], timing)
            if entry_price is None:
                result["missed_fills"] += 1
                continue

            # The plan's risk is what the position was sized and judged against.
            # If the fill moved toward the stop, this is a different trade.
            planned_risk = abs(idea.entry - idea.stop)
            actual_risk = abs(entry_price - idea.stop)
            if planned_risk <= 0 or actual_risk < planned_risk * min_risk_fraction:
                result["skipped_risk_collapsed"] += 1
                continue

            # Sized with a nominal 1 share: share count scales gross P&L and the
            # percentage part of costs linearly, so the portfolio layer can size
            # against live equity later without re-running the price path. This
            # is what lets one replay serve many portfolio configurations.
            shares = 1 if unsized else _size(idea, entry_price, config)
            if shares < 1:
                continue

            trade = simulator.simulate_trade(
                ticker, idea.direction, entry_price, idea.stop, idea.target, shares,
                df.iloc[entry_index + 1:], costs, max_holding_bars=max_hold,
                exit_style=cfg["exit_style"], atr=snapshot.get("atr"),
                trail_atr=cfg["trail_atr"], activate_at_r=cfg["trail_activate_at_r"],
                scale_out_fraction=cfg["scale_out_fraction"])
            if trade is None:
                continue

            if unsized:
                trade.update(_per_share_economics(
                    ticker, idea, trade, entry_price, costs, config))

            trade.update({
                "ticker": ticker,
                "strategy": idea.strategy,
                "strategy_label": idea.strategy_label,
                "regime": idea.regime,
                "signal_date": str(window.index[-1])[:10],
                "entry_date": str(df.index[entry_index])[:10],
                # The date capital is released. The portfolio layer needs it to
                # know when the slot frees up and the cash comes back.
                "exit_date": str(df.index[min(entry_index + trade["bars_held"],
                                              len(df) - 1)])[:10],
                "index_symbol": market_regime.index_for(ticker, config),
                "signal_price": idea.entry,
                "headline": idea.headline,
            })
            result["trades"].append(trade)
            busy_until[slot] = entry_index + trade["bars_held"]
            if not per_strategy_slots:
                break   # the single shared slot is now taken

    return result


def _size(idea, entry_price, config):
    """Position size from the SAME deterministic sizer the live pipeline uses.

    Sized against a fixed notional book rather than a compounding equity curve:
    the question here is "do these rules have an edge", and compounding answers
    a different question while making early trades dominate the result.
    """
    account = config.get("account", {}) or {}
    portfolio = account.get("portfolio_value", 0) or 0
    risk_budget = portfolio * account.get("risk_per_trade_pct", 1.0) / 100
    max_position = portfolio * account.get("max_position_pct", 15.0) / 100

    risk_per_share = abs(entry_price - idea.stop)
    if risk_per_share <= 0:
        return 0
    return sizing.position_size(
        risk_budget, risk_per_share,
        max_position_value=(max_position / entry_price) if entry_price else None)


def convert_trades(trades, instrument_ccy, base_ccy, fx_series, notes):
    """Attach a base-currency P&L to every trade.

    Without this, a global backtest sums dollars, euros, yen and pounds into one
    meaningless total. The rate used is the one from the trade's own ENTRY DATE,
    not today's — converting a 2017 trade at a 2026 rate silently rewrites
    history every time the currency moved, and over a decade that is a large
    number quietly masquerading as strategy performance.
    """
    if instrument_ccy == base_ccy:
        for trade in trades:
            trade["currency"] = instrument_ccy
            trade["fx_rate"] = trade["fx_in"] = trade["fx_out"] = 1.0
            trade["pnl_base"] = trade["pnl"]
            trade["costs_base"] = trade["costs"]
        return

    if fx_series is None or len(fx_series) == 0:
        notes.append(f"No {instrument_ccy}->{base_ccy} rate history; those trades are "
                     f"excluded from base-currency totals.")
        for trade in trades:
            trade["currency"] = instrument_ccy
            trade["fx_rate"] = trade["fx_in"] = trade["fx_out"] = None
            trade["pnl_base"] = None
            trade["costs_base"] = None
        return

    for trade in trades:
        trade["currency"] = instrument_ccy
        rate = _rate_on(fx_series, trade.get("entry_date"))
        exit_rate = _rate_on(fx_series, trade.get("exit_date")) or rate
        trade["fx_rate"] = round(rate, 6) if rate else None
        # Separate entry and exit rates: entry converts POSITION SIZE (how much
        # of the book this consumes), exit converts REALISED P&L. Using one rate
        # for both silently books a currency gain or loss as strategy return.
        trade["fx_in"] = round(rate, 6) if rate else None
        trade["fx_out"] = round(exit_rate, 6) if exit_rate else None
        trade["pnl_base"] = round(trade["pnl"] * exit_rate, 2) if exit_rate else None
        trade["costs_base"] = round(trade["costs"] * exit_rate, 2) if exit_rate else None


def _rate_on(series, date_str):
    """The FX rate as of a date, using the last rate at or before it."""
    if not date_str:
        return None
    try:
        window = series.loc[:date_str]
        if len(window) == 0:
            return float(series.iloc[0])
        return float(window.iloc[-1])
    except Exception:
        try:
            return float(series.iloc[-1])
        except Exception:
            return None


def run_backtest(tickers, config, price_fn, benchmark_fn=None, progress_cb=None,
                 should_cancel=None, currency_fn=None, fx_series_fn=None,
                 unsized=False):
    """Replay every ticker. price_fn(ticker) -> DataFrame, injected so the engine
    stays testable with synthetic data and no network.

    currency_fn(ticker) -> currency code, and fx_series_fn(from, to) -> a dated
    Series of rates, are what make a multi-market run add up to a real number.
    Omit them and everything is assumed to already be in the base currency.
    """
    benchmark_closes = benchmark_fn() if benchmark_fn else None
    base_ccy = config.get("base_currency", "USD")
    per_ticker, trades, notes = [], [], []
    fx_cache = {}

    for ticker in tickers:
        if should_cancel and should_cancel():
            notes.append("Cancelled before finishing.")
            break
        try:
            df = price_fn(ticker)
        except Exception as exc:
            notes.append(f"{ticker}: price data unavailable ({type(exc).__name__}: {exc})")
            continue
        outcome = backtest_ticker(ticker, df, config, benchmark_closes, progress_cb,
                                  unsized=unsized)

        instrument_ccy = base_ccy
        if currency_fn:
            try:
                instrument_ccy = currency_fn(ticker) or base_ccy
            except Exception:
                instrument_ccy = base_ccy
        series = None
        if instrument_ccy != base_ccy and fx_series_fn:
            if instrument_ccy not in fx_cache:
                try:
                    fx_cache[instrument_ccy] = fx_series_fn(instrument_ccy, base_ccy)
                except Exception:
                    fx_cache[instrument_ccy] = None
            series = fx_cache[instrument_ccy]
        convert_trades(outcome["trades"], instrument_ccy, base_ccy, series, notes)

        per_ticker.append(outcome)
        trades.extend(outcome["trades"])
        if outcome["note"]:
            notes.append(f"{ticker}: {outcome['note']}")

    return {"trades": trades, "per_ticker": per_ticker, "notes": notes,
            "tickers_tested": len([p for p in per_ticker if p["bars_tested"]]),
            "signals": sum(p["signals"] for p in per_ticker),
            "missed_fills": sum(p["missed_fills"] for p in per_ticker),
            "skipped_risk_collapsed": sum(p["skipped_risk_collapsed"] for p in per_ticker),
            "blocked_by_open_position": sum(p["blocked_by_open_position"]
                                            for p in per_ticker)}
