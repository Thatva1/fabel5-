"""Closing an idea out: turn an exit price into recorded, converted P&L.

This is the thin layer between the deterministic maths in risk/pnl.py (which
knows nothing about currencies, databases or networks) and the journal. It
exists so the arithmetic stays testable in isolation while the FX lookup — the
one part that needs the outside world — lives somewhere it can be injected.

Nothing here places or closes an actual position. Marking an idea as a win does
not sell anything; it records what YOU did, so the journal can tell you which
strategies are worth keeping.
"""
from . import journal
from .core import fx
from .core.config import load_config
from .risk import pnl as pnl_math


def close_idea(idea_id, outcome, exit_price=None, notes=None, entry_override=None,
               config=None, fx_rates=None):
    """Record an outcome, computing realised P&L when an exit price is given.

    fx_rates can be injected for testing; otherwise it is fetched at close time
    and the rate used is stored on the row. Storing the rate matters: converting
    a two-year-old trade at today's rate would silently rewrite your history
    every time the pound moved.

    Returns {"ok": bool, "realised": {...}, "warnings": [...]}.
    """
    config = config or load_config()
    base_ccy = config.get("base_currency", "USD")
    warnings = []

    idea = journal.get_idea(idea_id)
    if idea is None:
        return {"ok": False, "error": f"idea #{idea_id} does not exist"}

    plan = (idea.get("payload") or {}).get("plan")
    realised = {}

    if outcome != "open" and exit_price is not None:
        if not plan:
            warnings.append(
                "This idea never had a trade plan (it was a watch item), so there is "
                "no position to work out a profit or loss from.")
        else:
            realised = pnl_math.close_out(plan, exit_price, entry_override=entry_override)
            instrument_ccy = realised.get("currency") or base_ccy
            pnl_instrument = realised.get("pnl_instrument")

            if pnl_instrument is None:
                warnings.append(
                    "Could not compute a profit or loss — the plan is missing the "
                    "direction, entry or share count.")
            elif instrument_ccy == base_ccy:
                realised["pnl_base"] = pnl_instrument
                realised["exit_fx_rate"] = 1.0
            else:
                rates = fx_rates
                if rates is None:
                    rates = _fetch_rates({instrument_ccy, base_ccy}, base_ccy, config,
                                         warnings)
                try:
                    realised["pnl_base"] = round(
                        fx.convert(pnl_instrument, instrument_ccy, base_ccy, rates or {}), 2)
                    realised["exit_fx_rate"] = round(
                        fx.convert(1.0, instrument_ccy, base_ccy, rates or {}), 6)
                except fx.MissingRateError:
                    warnings.append(
                        f"No {instrument_ccy}->{base_ccy} exchange rate available, so this "
                        f"trade's profit is recorded in {instrument_ccy} only and is left "
                        "out of your base-currency totals.")

            if realised.get("r_multiple") is None and plan.get("stop"):
                warnings.append("Could not compute the R-multiple for this trade.")

    elif outcome != "open" and exit_price is None:
        warnings.append(
            "No exit price recorded, so this counts toward your hit rate but not "
            "toward profit and loss. Add the price you actually got out at to include it.")

    ok = journal.record_outcome(idea_id, outcome, exit_price, notes, realised=realised)
    return {"ok": ok, "realised": realised, "warnings": warnings}


def _fetch_rates(currencies, base_ccy, config, warnings):
    """FX lookup, isolated so a data-provider outage degrades to a warning
    instead of losing the outcome the user just recorded."""
    try:
        from . import pipeline
        return pipeline.get_router(config).get_fx_rates(currencies, base_ccy)
    except Exception as exc:
        warnings.append(f"Exchange-rate lookup failed ({type(exc).__name__}): "
                        "profit recorded in the instrument's currency only.")
        return None
