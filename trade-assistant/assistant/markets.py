"""Instruments outside the US share listing — FX pairs and futures.

The screened equity universe already reaches rates, credit and commodities
through unlevered ETFs (TLT, LQD, GLD). What it cannot reach is the two things
that are not listed on an equity exchange at all: currency pairs, and futures.

Both are handled here rather than in the screen, because neither can be
liquidity-screened the way a share can. A currency pair has no share price and
no daily dollar volume in any comparable sense, and a futures symbol is a
continuously-rolled front-month series rather than a security. They are a fixed,
curated catalogue instead — small enough to be listed explicitly and stable
enough that it does not need refreshing weekly.

WHAT THE ENGINE DOES AND DOES NOT MODEL, because this matters more here than
anywhere else in the project:

  * Futures are LEVERED. A contract is controlled with margin worth a fraction
    of its notional. This engine sizes every position as though the full
    notional were paid in cash, exactly as it does for a share. That is
    deliberately conservative — it understates both the return and the risk of a
    real futures book — but it is NOT how a futures account behaves, and a
    result from here cannot be read as one.
  * Contract multipliers are not applied. One ES point is $50; the engine treats
    the quoted price as the price of one unit.
  * Roll yield is absent. yfinance serves a front-month continuous series, so
    the term structure that carry strategies actually trade is not visible.
    That is why there is no carry strategy in the library despite the research
    supporting one.
  * Options are not here at all. No usable historical options data reaches this
    stack, and inventing a proxy would be worse than the gap.

Read together: this makes futures and FX testable as PRICE SERIES for trend and
reversal research, which is what the time-series momentum literature actually
uses. It does not make this a derivatives trading system.
"""

# asset_class is what the portfolio layer needs to tell a genuinely diversifying
# position from a second ticket on the same bet. Six equity ETFs are one bet;
# equities plus rates plus commodities are three.
FX_PAIRS = {
    "EURUSD=X": {"name": "Euro / US Dollar", "asset_class": "fx"},
    "GBPUSD=X": {"name": "Sterling / US Dollar", "asset_class": "fx"},
    "USDJPY=X": {"name": "US Dollar / Yen", "asset_class": "fx"},
    "AUDUSD=X": {"name": "Australian Dollar / US Dollar", "asset_class": "fx"},
    "USDCHF=X": {"name": "US Dollar / Swiss Franc", "asset_class": "fx"},
    "USDCAD=X": {"name": "US Dollar / Canadian Dollar", "asset_class": "fx"},
    "NZDUSD=X": {"name": "NZ Dollar / US Dollar", "asset_class": "fx"},
    "USDSEK=X": {"name": "US Dollar / Swedish Krona", "asset_class": "fx"},
}

FUTURES = {
    # Equity index
    "ES=F": {"name": "E-mini S&P 500", "asset_class": "equity_future"},
    "NQ=F": {"name": "E-mini Nasdaq 100", "asset_class": "equity_future"},
    "YM=F": {"name": "E-mini Dow", "asset_class": "equity_future"},
    "RTY=F": {"name": "E-mini Russell 2000", "asset_class": "equity_future"},
    # Rates
    "ZN=F": {"name": "10-Year T-Note", "asset_class": "rate_future"},
    "ZB=F": {"name": "30-Year T-Bond", "asset_class": "rate_future"},
    "ZF=F": {"name": "5-Year T-Note", "asset_class": "rate_future"},
    "ZT=F": {"name": "2-Year T-Note", "asset_class": "rate_future"},
    # Commodities
    "CL=F": {"name": "Crude Oil", "asset_class": "commodity_future"},
    "GC=F": {"name": "Gold", "asset_class": "commodity_future"},
    "SI=F": {"name": "Silver", "asset_class": "commodity_future"},
    "NG=F": {"name": "Natural Gas", "asset_class": "commodity_future"},
    "HG=F": {"name": "Copper", "asset_class": "commodity_future"},
    "ZC=F": {"name": "Corn", "asset_class": "commodity_future"},
    "ZS=F": {"name": "Soybeans", "asset_class": "commodity_future"},
    "ZW=F": {"name": "Wheat", "asset_class": "commodity_future"},
    # Currency futures — the exchange-traded expression of the FX pairs above.
    "6E=F": {"name": "Euro FX Future", "asset_class": "fx_future"},
    "6J=F": {"name": "Japanese Yen Future", "asset_class": "fx_future"},
    "6B=F": {"name": "British Pound Future", "asset_class": "fx_future"},
    "6A=F": {"name": "Australian Dollar Future", "asset_class": "fx_future"},
}

CATALOGUE = {**FX_PAIRS, **FUTURES}

# Sector ETFs, for the sector-rotation strategy. Unlevered, liquid, and the
# standard set the sector-momentum research is run on.
SECTOR_ETFS = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
    "XLV": "Health Care", "XLI": "Industrials", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication Services",
}


def is_fx(ticker):
    return str(ticker).upper().endswith("=X")


def is_future(ticker):
    return str(ticker).upper().endswith("=F")


def is_derivative(ticker):
    """Futures only. An FX spot pair is not a derivative."""
    return is_future(ticker)


def asset_class_of(ticker):
    """Broad class for diversification accounting, or 'equity' by default."""
    entry = CATALOGUE.get(str(ticker).upper())
    if entry:
        return entry["asset_class"]
    return "equity"


def symbols(include_fx=True, include_futures=True):
    out = []
    if include_fx:
        out.extend(FX_PAIRS)
    if include_futures:
        out.extend(FUTURES)
    return out


def describe(ticker):
    entry = CATALOGUE.get(str(ticker).upper())
    return entry["name"] if entry else str(ticker)


def leverage_warning(tickers):
    """The one sentence that has to travel with any futures result.

    Returned rather than logged so a caller has to decide where to put it. A
    futures P&L produced by a cash-equity sizer is not wrong arithmetic — it is
    the right arithmetic for the wrong instrument, and that is far easier to
    misread.
    """
    futures = sorted(t for t in tickers if is_future(t))
    if not futures:
        return None
    return (f"{len(futures)} futures in this run are sized as if their full "
            "notional were paid in cash, with no margin and no contract "
            "multiplier. Returns and risk are both understated against a real "
            "futures account; treat these as price-series research, not as a "
            "futures book.")
