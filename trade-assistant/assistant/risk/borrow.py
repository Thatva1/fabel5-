"""Can this instrument actually be shorted, and how crowded is the trade?

Two different questions that get confused constantly:

  * BORROW AVAILABILITY — is there stock to borrow at all? Without it the trade
    simply cannot be placed, no matter how good the setup is.
  * SHORT CROWDING — how much of the float is already sold short? That does not
    stop you trading; it warns that borrow will be expensive and that a squeeze
    can run your stop over.

Free data answers the second well and the first badly. This module is therefore
deliberately honest about not knowing: `UNKNOWN` is a first-class answer, and
the risk gate decides what to do with it (flag by default, block if you set
`block_if_borrow_unknown: true`). What it never does is guess YES.
"""
YES = "yes"
NO = "no"
UNKNOWN = "unknown"


def check_shortability(ticker, config, broker=None, fundamentals=None, currency="USD"):
    """Best available answer, with its provenance attached.

    Sources are tried in order of authority: your own explicit list, then the
    broker (the only source that truly knows), then nothing.

    currency: the INSTRUMENT's currency. The broker needs it to resolve the
    contract — without it IBKR asked about a USD line for every symbol and
    answered UNKNOWN for every London, Paris or Frankfurt name, quietly
    switching this check off for most of the markets this tool is aimed at.

    Returns {status, source, detail, crowding{}}.
    """
    rules = (config or {}).get("short_rules") or {}
    crowding = _crowding(fundamentals)

    overrides = {str(k).upper(): v for k, v in (rules.get("shortable_overrides") or {}).items()}
    if ticker and ticker.upper() in overrides:
        allowed = bool(overrides[ticker.upper()])
        return {
            "status": YES if allowed else NO,
            "source": "your config.yaml shortable_overrides list",
            "detail": (f"You listed {ticker} as "
                       f"{'shortable' if allowed else 'NOT shortable'}."),
            "crowding": crowding,
        }

    if broker is not None:
        try:
            answer = broker.is_shortable(ticker, currency)
        except Exception as exc:
            answer = {"status": UNKNOWN,
                      "detail": f"Broker check failed ({type(exc).__name__}: {exc})."}
        if answer and answer.get("status") in (YES, NO):
            return {
                "status": answer["status"],
                "source": answer.get("source", getattr(broker, "name", "broker")),
                "detail": answer.get("detail", ""),
                "crowding": crowding,
            }
        if answer and answer.get("detail"):
            return {"status": UNKNOWN, "source": getattr(broker, "name", "broker"),
                    "detail": answer["detail"], "crowding": crowding}

    return {
        "status": UNKNOWN,
        "source": "no source available",
        "detail": ("Borrow availability cannot be verified from free data. Your broker "
                   "is the only reliable source — check it can be shorted there before "
                   "acting, or add the ticker to short_rules.shortable_overrides in "
                   "config.yaml once you know."),
        "crowding": crowding,
    }


def _crowding(fundamentals):
    """Short interest as a percentage of float, when the data layer supplied it.

    The value arrives ALREADY normalised to a percentage — see
    providers/yfinance_provider._fraction_to_pct, which is the only place that
    knows the source unit.

    This used to normalise here with `if pct <= 1.0: pct *= 100`, which cannot
    tell a fraction from a small percentage: 0.8% of float was reported as 80%
    and 1% as 100%. That flagged the *least* crowded names — the safest ones to
    short — as the most crowded, training you to ignore the one warning you
    want to trust when it is real.
    """
    if not isinstance(fundamentals, dict):
        return {"short_percent_of_float": None}
    raw = fundamentals.get("short_percent_of_float")
    value = raw.get("value") if isinstance(raw, dict) else raw
    source = raw.get("source") if isinstance(raw, dict) else None
    if value is None:
        return {"short_percent_of_float": None}
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return {"short_percent_of_float": None}
    return {"short_percent_of_float": round(pct, 2), "source": source}
