"""Turning a chain of quotes into implied volatilities, one strike at a time.

WHAT THIS ADDS TO risk/options.py

That module answers "what is this option worth at this volatility" and "what
volatility does this price imply". This one answers the question you actually
have in front of a screen: given a whole chain of two-sided quotes, what does
the market think, and how much of what it appears to think is really just the
spread.

THREE THINGS IT DOES DIFFERENTLY FROM THE OBVIOUS IMPLEMENTATION

1. THE MID, NEVER THE LAST. A last trade on an illiquid strike can be hours old
   and struck against a market that has since moved. The mid of a live two-sided
   quote is the only price that describes now. Where there is no two-sided
   quote there is no price here either, rather than a stale print dressed as one.

2. AN IV BRACKET, NOT AN IV POINT. This is the part most chain displays get
   wrong. An option quoted 1.05 / 1.45 does not have an implied volatility; it
   has a RANGE, and on a wide strike that range can be ten volatility points
   wide. Reporting the mid IV alone turns a shrug into a number, and it is the
   number that makes a skew curve look like information. So every row carries
   iv_bid, iv_mid and iv_ask, and the width between them is the honest measure
   of how much the quote actually says.

3. THE FORWARD IS DERIVED, NOT ASSUMED. Black-Scholes needs a dividend yield,
   and guessing one biases every implied vol in the chain in the same direction
   — which looks exactly like a real skew. Put-call parity gives the forward
   directly from the call and put mids at the same strike, with no dividend
   estimate at all. Where both sides quote, that is what is used.

WHAT IT REFUSES

A strike whose price cannot identify a volatility (see options.implied_vol) is
reported with iv None rather than a fabricated number. So is a crossed quote, a
one-sided quote, and a mid below intrinsic. On a chain with no market-data
subscription every row comes back empty, which is the correct answer and is
visibly different from a chain of zeros.
"""
import math

from ..risk import options

# A quote wider than this fraction of its own mid is not really a quote. The
# IV bracket is still reported — it is the useful part — but the mid is flagged
# so nothing downstream treats it as a price anyone would trade at.
WIDE_SPREAD_FRACTION = 0.25


def _mid(bid, ask):
    """Mid of a two-sided quote, or None. Crossed and one-sided both fail."""
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (bid > 0 and ask > 0) or ask < bid:
        return None
    return (bid + ask) / 2.0


def _spread_fraction(bid, ask):
    mid = _mid(bid, ask)
    if not mid:
        return None
    return (float(ask) - float(bid)) / mid


def implied_forward(rows, spot, years, rate):
    """The forward price the chain itself implies, via put-call parity.

    C - P = e^-rT (F - K), so F = K + (C - P) e^rT at any strike where both
    sides quote. Solved at the strike closest to spot, because parity is
    arithmetically exact everywhere and numerically best where both options
    carry real time value.

    This replaces a guessed dividend yield. Guessing one biases every implied
    vol in the chain the same way, which is indistinguishable from a real skew
    and considerably more convincing than one.

    Returns (forward, strike_used) or (None, None) when no strike quotes both
    sides — in which case the caller must supply a dividend and knows it.
    """
    candidates = []
    for row in rows:
        call_mid = _mid(row.get("call_bid"), row.get("call_ask"))
        put_mid = _mid(row.get("put_bid"), row.get("put_ask"))
        if call_mid is None or put_mid is None:
            continue
        strike = float(row["strike"])
        forward = strike + (call_mid - put_mid) * math.exp(rate * years)
        candidates.append((abs(strike - spot), forward, strike))
    if not candidates:
        return None, None
    candidates.sort()
    return candidates[0][1], candidates[0][2]


def _dividend_from_forward(forward, spot, years, rate):
    """The carry that reconciles the observed forward with the spot.

    F = S e^(r-q)T, so q = r - ln(F/S)/T. For an index this is the dividend
    yield; for a single stock it absorbs borrow cost too, which is correct —
    both are carry, and the model only ever sees their sum.
    """
    if not (forward and spot and years > 0) or forward <= 0:
        return 0.0
    return rate - math.log(forward / spot) / years


def build(quotes, spot, years, rate, *, dividend=None, multiplier=100):
    """One row per strike, with the volatility each side of the market implies.

    `quotes` is [{strike, call_bid, call_ask, put_bid, put_ask}, ...]; any of
    the four may be missing. Returns {"rows": [...], "surface": {...}}.
    """
    rows = sorted(quotes or [], key=lambda r: float(r["strike"]))

    # Prefer the forward the chain implies over any dividend handed in. Where
    # parity cannot be solved, fall back to what the caller supplied and say so.
    forward, parity_strike = implied_forward(rows, spot, years, rate)
    if forward is not None:
        carry = _dividend_from_forward(forward, spot, years, rate)
        carry_source = f"put-call parity at the {parity_strike:g} strike"
    else:
        carry = float(dividend or 0.0)
        carry_source = ("supplied dividend yield — no strike quoted both sides, "
                        "so the forward could not be derived")

    out = []
    for row in rows:
        strike = float(row["strike"])
        built = {"strike": strike}
        for kind, prefix in ((options.CALL, "call"), (options.PUT, "put")):
            bid, ask = row.get(f"{prefix}_bid"), row.get(f"{prefix}_ask")
            mid = _mid(bid, ask)
            built[f"{prefix}_mid"] = round(mid, 4) if mid is not None else None
            built[f"{prefix}_spread_pct"] = (
                round(_spread_fraction(bid, ask) * 100, 1)
                if _spread_fraction(bid, ask) is not None else None)
            built[f"{prefix}_wide"] = bool(
                _spread_fraction(bid, ask) is not None
                and _spread_fraction(bid, ask) > WIDE_SPREAD_FRACTION)

            # The bracket. Each side of the quote implies its own volatility,
            # and the distance between them is what the quote does NOT say.
            for label, price in (("bid", bid), ("mid", mid), ("ask", ask)):
                value = None
                if price is not None:
                    try:
                        value = options.implied_vol(kind, float(price), spot,
                                                    strike, years, rate, carry)
                    except (TypeError, ValueError):
                        value = None
                built[f"{prefix}_iv_{label}"] = (round(value, 5)
                                                 if value is not None else None)

            iv = built[f"{prefix}_iv_mid"]
            built[f"{prefix}_iv_width"] = (
                round(built[f"{prefix}_iv_ask"] - built[f"{prefix}_iv_bid"], 5)
                if built[f"{prefix}_iv_bid"] is not None
                and built[f"{prefix}_iv_ask"] is not None else None)

            if iv is not None:
                g = options.greeks(kind, spot, strike, years, rate, iv, carry)
                built[f"{prefix}_delta"] = round(g["delta"], 4)
                built[f"{prefix}_gamma"] = round(g["gamma"], 6)
                built[f"{prefix}_vega"] = round(g["vega"], 4)
                built[f"{prefix}_theta"] = round(g["theta"], 4)
            else:
                for greek in ("delta", "gamma", "vega", "theta"):
                    built[f"{prefix}_{greek}"] = None
        out.append(built)

    return {"rows": out,
            "surface": _surface(out, spot, forward, carry, carry_source, years),
            "spot": spot, "years": years, "rate": rate,
            "forward": round(forward, 4) if forward is not None else None,
            "carry": round(carry, 5), "carry_source": carry_source,
            "multiplier": multiplier}


def _surface(rows, spot, forward, carry, carry_source, years):
    """The three numbers worth reading before any individual strike.

    Deliberately few. A chain has a hundred rows and almost all the information
    in it is in where the at-the-money vol sits, how lopsided the wings are,
    and how much of the apparent shape is spread rather than opinion.
    """
    anchor = forward or spot
    priced = [r for r in rows if r["call_iv_mid"] is not None
              or r["put_iv_mid"] is not None]
    if not priced:
        return {"quoted_strikes": 0,
                "note": ("No strike produced a usable implied volatility. With "
                         "no options market-data subscription this is the "
                         "expected result, and it is not the same as a "
                         "volatility of zero.")}

    # At the money: whichever side is OUT of the money is the one being quoted
    # on time value alone, so it is the better-behaved implied vol.
    def side_iv(row):
        return (row["put_iv_mid"] if row["strike"] < anchor
                else row["call_iv_mid"])

    nearest = min(priced, key=lambda r: abs(r["strike"] - anchor))
    atm = side_iv(nearest) or nearest["call_iv_mid"] or nearest["put_iv_mid"]

    # Skew, measured the way it is actually traded: a downside put against an
    # upside call roughly equidistant from the forward.
    downside = [r for r in priced if r["strike"] < anchor * 0.95
                and r["put_iv_mid"] is not None]
    upside = [r for r in priced if r["strike"] > anchor * 1.05
              and r["call_iv_mid"] is not None]
    skew = None
    if downside and upside:
        low = max(downside, key=lambda r: r["strike"])
        high = min(upside, key=lambda r: r["strike"])
        skew = round(low["put_iv_mid"] - high["call_iv_mid"], 5)

    widths = [r[f"{side}_iv_width"] for r in priced for side in ("call", "put")
              if r.get(f"{side}_iv_width") is not None]
    median_width = (sorted(widths)[len(widths) // 2] if widths else None)

    return {
        "quoted_strikes": len(priced),
        "atm_iv": round(atm, 5) if atm is not None else None,
        "skew_25pct": skew,
        # How much of the shape above is the market disagreeing with itself.
        # A skew of 3 volatility points read off quotes each 6 points wide is
        # not a skew, it is two spreads.
        "median_iv_width": median_width,
        "skew_exceeds_spread": (bool(skew is not None and median_width
                                     and abs(skew) > median_width)),
        "forward": round(forward, 4) if forward is not None else None,
        "carry_pct": round(carry * 100, 3),
        "carry_source": carry_source,
        "years": years,
    }


def parity_violations(rows, spot, years, rate, carry, tolerance=0.02):
    """Strikes where the call and put disagree about the forward.

    Put-call parity is an arbitrage identity, not a model: it holds whatever
    volatility does. A strike that breaks it has a stale quote on one side, and
    every implied volatility taken from that side is wrong in a way no
    volatility test would catch.

    `tolerance` is in price units, and should be at least the tick.
    """
    out = []
    for row in rows:
        call_mid, put_mid = row.get("call_mid"), row.get("put_mid")
        if call_mid is None or put_mid is None:
            continue
        strike = float(row["strike"])
        expected = (spot * math.exp(-carry * years)
                    - strike * math.exp(-rate * years))
        gap = (call_mid - put_mid) - expected
        if abs(gap) > tolerance:
            out.append({"strike": strike, "gap": round(gap, 4),
                        "call_mid": call_mid, "put_mid": put_mid})
    return out
