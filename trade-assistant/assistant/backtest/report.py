"""Aggregate backtest trades into the same shape the journal produces.

Deliberately identical keys to `journal.stats()` — wins, losses, win_rate_pct,
pnl, expectancy_r, profit_factor — so the dashboard renders backtest and live
results with one table component, and so the two can be compared honestly side
by side. A strategy that backtests at +0.6R and paper-trades at -0.2R is telling
you something important, and that comparison is only possible if both are
measured the same way.
"""
from ..risk import pnl as pnl_math

MARKET_NAMES = {
    "US": "United States", ".L": "United Kingdom", ".DE": "Germany",
    ".PA": "France", ".AS": "Netherlands", ".SW": "Switzerland",
    ".MI": "Italy", ".T": "Japan", ".HK": "Hong Kong", ".AX": "Australia",
    ".TO": "Canada", ".MC": "Spain", ".ST": "Sweden",
}


def market_of(ticker):
    """Ticker suffix as a market key. Yahoo uses no suffix for US listings."""
    name = str(ticker or "").upper()
    if "." not in name:
        return "US"
    return "." + name.rsplit(".", 1)[-1]


def summarise(trades, group_of=None, label_of=None):
    groups = {}
    for trade in trades:
        key = (group_of(trade) if group_of else "all") or "untagged"
        entry = groups.setdefault(key, {
            "label": (label_of(trade) if label_of else None) or str(key),
            "wins": 0, "losses": 0, "scratches": 0, "trades": 0,
            "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
            "costs": 0.0, "r_values": [], "bars_held": [],
            "stopped": 0, "targeted": 0, "timed_out": 0,
        })
        # Base currency always, never the instrument's. Summing raw pnl across a
        # global run adds dollars to yen to pounds and produces a number that
        # looks precise and means nothing.
        value = trade.get("pnl_base")
        if value is None:
            value = trade["pnl"] if trade.get("currency") in (None, "") else None
        if value is None:
            entry["unconverted"] = entry.get("unconverted", 0) + 1
            continue
        entry["trades"] += 1
        entry["pnl"] += value
        entry["costs"] += trade.get("costs_base", trade.get("costs", 0.0)) or 0.0
        if value > 0:
            entry["wins"] += 1
            entry["gross_profit"] += value
        elif value < 0:
            entry["losses"] += 1
            entry["gross_loss"] += -value
        else:
            entry["scratches"] += 1
        # R is currency-free by construction — profit divided by the risk taken,
        # both in the same currency — so it is the one metric that compares a
        # Tokyo trade with a New York one directly.
        if trade.get("r_multiple") is not None:
            entry["r_values"].append(trade["r_multiple"])
        entry["bars_held"].append(trade.get("bars_held", 0))
        reason = trade.get("exit_reason")
        entry["stopped"] += reason == "stop"
        entry["targeted"] += reason == "target"
        entry["timed_out"] += reason == "timeout"

    for entry in groups.values():
        graded = entry["wins"] + entry["losses"]
        entry["win_rate_pct"] = (round(entry["wins"] / graded * 100, 1)
                                 if graded else None)
        entry["expectancy_r"] = pnl_math.expectancy(entry["r_values"])
        entry["profit_factor"] = (round(entry["gross_profit"] / entry["gross_loss"], 2)
                                  if entry["gross_loss"] > 0 else None)
        entry["no_losses_yet"] = entry["gross_loss"] == 0 and entry["trades"] > 0
        entry["avg_bars_held"] = (round(sum(entry["bars_held"]) / len(entry["bars_held"]), 1)
                                  if entry["bars_held"] else None)
        entry["best_r"] = round(max(entry["r_values"]), 2) if entry["r_values"] else None
        entry["worst_r"] = round(min(entry["r_values"]), 2) if entry["r_values"] else None
        # Sample size decides whether any of the above means anything. A 100%
        # hit rate over three trades is noise, and the UI must be able to say so.
        entry["reliable"] = entry["trades"] >= 30
        for key in ("pnl", "gross_profit", "gross_loss", "costs"):
            entry[key] = round(entry[key], 2)
        del entry["r_values"], entry["bars_held"]

    return dict(sorted(groups.items(), key=lambda kv: -kv[1]["trades"]))


def build(result, config, period=None):
    """Full report from run_backtest output."""
    trades = result["trades"]
    overall = summarise(trades).get("all", _empty())

    return {
        "overall": overall,
        "by_strategy": summarise(
            trades, lambda t: t.get("strategy"), lambda t: t.get("strategy_label")),
        "by_regime": summarise(
            trades, lambda t: t.get("regime"),
            lambda t: (t.get("regime") or "unknown").replace("_", " ").title()),
        "by_strategy_regime": summarise(
            trades,
            lambda t: f"{t.get('strategy') or 'untagged'} · {t.get('regime') or 'untagged'}",
            lambda t: (f"{t.get('strategy_label') or 'Untagged'} in "
                       f"{(t.get('regime') or 'unknown').replace('_', ' ').lower()}")),
        "by_ticker": summarise(trades, lambda t: t.get("ticker")),
        "by_direction": summarise(trades, lambda t: t.get("direction")),
        # The whole point of a multi-market run: does a result hold everywhere,
        # or is it one country's tax regime and market character in disguise?
        "by_market": summarise(trades, lambda t: market_of(t.get("ticker")),
                               lambda t: MARKET_NAMES.get(market_of(t.get("ticker")),
                                                          market_of(t.get("ticker")))),
        "by_currency": summarise(trades, lambda t: t.get("currency")),
        "by_year": summarise(trades, lambda t: (t.get("entry_date") or "")[:4]),
        "equity_curve": equity_curve(trades),
        "trades": sorted(trades, key=lambda t: t.get("entry_date") or ""),
        "tickers_tested": result["tickers_tested"],
        # Fill quality. A strategy whose signals mostly never fill, or whose
        # fills routinely drift onto the stop, is not the strategy the numbers
        # above describe — so the gap between signals and trades is reported
        # rather than hidden.
        "fills": {
            "signals": result.get("signals", 0),
            "traded": len(trades),
            "missed_fills": result.get("missed_fills", 0),
            "skipped_risk_collapsed": result.get("skipped_risk_collapsed", 0),
        },
        "notes": result["notes"],
        "period": period,
        "base_currency": config.get("base_currency", "USD"),
        "settings": {
            "portfolio_value": (config.get("account") or {}).get("portfolio_value"),
            "risk_per_trade_pct": (config.get("account") or {}).get("risk_per_trade_pct"),
        },
    }


def equity_curve(trades):
    """Cumulative P&L in trade order, plus the worst peak-to-trough fall.

    Max drawdown matters more than the total: a strategy that ends +£20,000
    having been £15,000 underwater on the way is one almost nobody actually
    sticks with, and abandoning a system at its low is how the edge is lost.
    """
    ordered = sorted(trades, key=lambda t: t.get("entry_date") or "")
    points, running, peak, max_dd = [], 0.0, 0.0, 0.0
    for trade in ordered:
        value = trade.get("pnl_base")
        if value is None:
            value = trade["pnl"] if trade.get("currency") in (None, "") else None
        if value is None:
            continue
        running += value
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        points.append({"date": trade.get("entry_date"), "cumulative_pnl": round(running, 2)})
    return {"points": points, "final_pnl": round(running, 2),
            "max_drawdown": round(max_dd, 2)}


def _empty():
    return {"label": "all", "trades": 0, "wins": 0, "losses": 0, "scratches": 0,
            "pnl": 0.0, "win_rate_pct": None, "expectancy_r": None,
            "profit_factor": None, "no_losses_yet": False, "avg_bars_held": None,
            "best_r": None, "worst_r": None, "reliable": False,
            "gross_profit": 0.0, "gross_loss": 0.0, "costs": 0.0,
            "stopped": 0, "targeted": 0, "timed_out": 0}
