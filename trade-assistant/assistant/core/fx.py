"""Deterministic currency conversion. Pure functions, injected rates — no I/O.

Rates dict format: {"EURUSD": 1.08} meaning 1 EUR = 1.08 USD. Conversion tries
the direct pair, the inverse pair, then crosses through USD.
"""


class MissingRateError(ValueError):
    pass


def convert(amount, from_ccy, to_ccy, rates):
    """Convert amount from one currency to another using the supplied rates."""
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return float(amount)

    direct = rates.get(from_ccy + to_ccy)
    if direct:
        return float(amount) * float(direct)

    inverse = rates.get(to_ccy + from_ccy)
    if inverse:
        return float(amount) / float(inverse)

    if from_ccy != "USD" and to_ccy != "USD":
        try:
            in_usd = convert(amount, from_ccy, "USD", rates)
            return convert(in_usd, "USD", to_ccy, rates)
        except MissingRateError:
            pass

    raise MissingRateError(f"no FX rate available for {from_ccy}->{to_ccy}")


def normalize_position_value(shares, price, position_ccy, base_ccy, rates):
    """Market value of a position expressed in the base currency."""
    return convert(float(shares) * float(price), position_ccy, base_ccy, rates)
