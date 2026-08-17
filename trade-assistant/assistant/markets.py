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


# Unlevered ETFs that give cash-market exposure to the same things the futures
# above trade. Included in a macro book so it is not made only of derivatives:
# an ETF has no margin, no roll and no multiplier, so where the two agree the
# result is not an artefact of how futures are modelled.
MACRO_ETFS = {
    "TLT": {"name": "20+ Year Treasuries", "asset_class": "rates"},
    "IEF": {"name": "7-10 Year Treasuries", "asset_class": "rates"},
    "SHY": {"name": "1-3 Year Treasuries", "asset_class": "rates"},
    "BIL": {"name": "1-3 Month T-Bills", "asset_class": "rates"},
    "LQD": {"name": "Investment Grade Credit", "asset_class": "credit"},
    "HYG": {"name": "High Yield Credit", "asset_class": "credit"},
    "JNK": {"name": "High Yield Credit (alt)", "asset_class": "credit"},
    "AGG": {"name": "US Aggregate Bond", "asset_class": "credit"},
    "BND": {"name": "Total Bond Market", "asset_class": "credit"},
    "EMB": {"name": "EM Sovereign Debt", "asset_class": "credit"},
    "GLD": {"name": "Gold", "asset_class": "commodity"},
    "SLV": {"name": "Silver", "asset_class": "commodity"},
    "USO": {"name": "Crude Oil", "asset_class": "commodity"},
    "SPY": {"name": "S&P 500", "asset_class": "equity_index"},
    "QQQ": {"name": "Nasdaq 100", "asset_class": "equity_index"},
    "IWM": {"name": "Russell 2000", "asset_class": "equity_index"},
    "EFA": {"name": "Developed ex-US", "asset_class": "equity_index"},
    "EEM": {"name": "Emerging Markets", "asset_class": "equity_index"},
}


# London. Screened by hand rather than by the liquidity filter, because that
# filter reads a Finnhub listing that only covers US exchanges — there is no
# equivalent bulk list here for the LSE. These are FTSE 100 constituents liquid
# enough that a position can be exited at something close to the modelled price.
#
# Yahoo-style .L suffixes, which is what the rest of the project speaks; the
# IBKR provider maps the suffix onto LSE/GBP.
LSE_UNIVERSE = [
    "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L", "RIO.L", "GSK.L", "DGE.L",
    "BATS.L", "GLEN.L", "REL.L", "LSEG.L", "NG.L", "CPG.L", "RKT.L", "BARC.L",
    "AAL.L", "LLOY.L", "NWG.L", "PRU.L", "TSCO.L", "IMB.L", "VOD.L", "BT-A.L",
    "SSE.L", "AV.L", "LGEN.L", "STAN.L", "ANTO.L", "BA.L", "SGE.L", "EXPN.L",
    "INF.L", "SMT.L", "III.L", "WTB.L", "SBRY.L", "KGF.L", "ITV.L", "SN.L",
]


def lse_universe():
    """The London names, when London is switched on."""
    return list(LSE_UNIVERSE)


def macro_universe():
    """FX, futures and the cash ETFs that track the same exposures.

    The book a macro strategy should be tested on. Pooling these with hundreds
    of single stocks does not test them — it silently excludes them: a currency
    pair moving 8% in a year cannot place in a ranking whose twelfth-best name
    is up 183%, so every cross-sectional strategy in the library is
    structurally incapable of ever selecting one. Only an absolute signal like
    time-series momentum can reach them in a mixed universe. Here they compete
    against their own kind.
    """
    return list(FX_PAIRS) + list(FUTURES) + list(MACRO_ETFS)


# Instruments that are the SAME BET wearing different tickers. Spot sterling and
# the sterling future track one exchange rate; SPY and the E-mini track one
# index. Holding both is one position with two tickets and twice the size.
#
# This exists because measured correlation cannot be relied on to notice. SPY
# against ES=F measures 0.98 and is caught; GBPUSD=X against 6B=F measures 0.12
# and is not — yfinance's FX bars close on a different boundary from CME
# settlement, so the two return series are computed over offset windows and
# decorrelate even though the underlying is identical. A correlation cap fed
# that number concludes the two are unrelated and lets both through.
#
# Identity is not a statistic. Where two instruments are the same exposure by
# construction, say so and stop measuring.
EXPOSURE_GROUPS = {
    "EURUSD=X": "EUR", "6E=F": "EUR",
    "GBPUSD=X": "GBP", "6B=F": "GBP",
    "USDJPY=X": "JPY", "6J=F": "JPY",
    "AUDUSD=X": "AUD", "6A=F": "AUD",
    "SPY": "SP500", "ES=F": "SP500", "VOO": "SP500", "IVV": "SP500",
    "QQQ": "NDX", "NQ=F": "NDX",
    "IWM": "RUSSELL", "RTY=F": "RUSSELL",
    "DIA": "DOW", "YM=F": "DOW",
    "GLD": "GOLD", "IAU": "GOLD", "GC=F": "GOLD",
    "SLV": "SILVER", "SI=F": "SILVER",
    "USO": "CRUDE", "CL=F": "CRUDE",
    "UNG": "NATGAS", "NG=F": "NATGAS",
    "TLT": "USRATES_LONG", "ZB=F": "USRATES_LONG", "VGLT": "USRATES_LONG",
    "IEF": "USRATES_MID", "ZN=F": "USRATES_MID", "VGIT": "USRATES_MID",
    "SHY": "USRATES_SHORT", "ZT=F": "USRATES_SHORT", "ZF=F": "USRATES_SHORT",
    "AGG": "USAGG", "BND": "USAGG",
    "HYG": "USHY", "JNK": "USHY",
}


# What one futures contract is actually worth, and what it costs to carry.
#
# `multiplier` converts a quoted price into dollars of notional. It is not
# cosmetic: ES quoted at 5,000 is $250,000 of index, not $5,000, so a book that
# treats the quote as the price of one unit understates that position fiftyfold.
# The grain contracts are quoted in CENTS per bushel on a 5,000-bushel contract,
# which is why their multiplier is 50 rather than 5,000.
#
# `margin` is roughly what an exchange asks to carry one contract overnight.
# Real margins move with volatility and differ by broker, so these are
# order-of-magnitude figures for modelling capital use — not quotes. They are
# in config so they can be replaced with a broker's actual numbers.
#
# The pair together is what makes a futures position honest here: capital
# committed is the MARGIN, profit and loss runs on the NOTIONAL, and the ratio
# between them is the leverage that a cash-sized model was silently discarding.
CONTRACT_SPECS = {
    "ES=F": {"multiplier": 50.0, "margin": 12_000.0, "unit": "S&P 500 index point"},
    "NQ=F": {"multiplier": 20.0, "margin": 18_000.0, "unit": "Nasdaq 100 index point"},
    "YM=F": {"multiplier": 5.0, "margin": 8_000.0, "unit": "Dow index point"},
    "RTY=F": {"multiplier": 50.0, "margin": 8_000.0, "unit": "Russell index point"},
    "ZN=F": {"multiplier": 1_000.0, "margin": 2_000.0, "unit": "price point"},
    "ZB=F": {"multiplier": 1_000.0, "margin": 4_000.0, "unit": "price point"},
    "ZF=F": {"multiplier": 1_000.0, "margin": 1_200.0, "unit": "price point"},
    "ZT=F": {"multiplier": 2_000.0, "margin": 800.0, "unit": "price point"},
    "CL=F": {"multiplier": 1_000.0, "margin": 6_000.0, "unit": "barrel"},
    "GC=F": {"multiplier": 100.0, "margin": 11_000.0, "unit": "troy ounce"},
    "SI=F": {"multiplier": 5_000.0, "margin": 16_000.0, "unit": "troy ounce"},
    "NG=F": {"multiplier": 10_000.0, "margin": 3_500.0, "unit": "MMBtu"},
    "HG=F": {"multiplier": 25_000.0, "margin": 5_500.0, "unit": "pound"},
    # Grains quote in cents per bushel: 5,000 bushels / 100 cents = 50.
    "ZC=F": {"multiplier": 50.0, "margin": 2_000.0, "unit": "cent per bushel"},
    "ZS=F": {"multiplier": 50.0, "margin": 3_500.0, "unit": "cent per bushel"},
    "ZW=F": {"multiplier": 50.0, "margin": 2_500.0, "unit": "cent per bushel"},
    "6E=F": {"multiplier": 125_000.0, "margin": 3_000.0, "unit": "EUR"},
    "6J=F": {"multiplier": 12_500_000.0, "margin": 3_500.0, "unit": "JPY"},
    "6B=F": {"multiplier": 62_500.0, "margin": 2_000.0, "unit": "GBP"},
    "6A=F": {"multiplier": 100_000.0, "margin": 1_700.0, "unit": "AUD"},
}


def contract_multiplier(ticker):
    """Dollars of notional per point of quoted price. 1.0 for anything cash."""
    spec = CONTRACT_SPECS.get(str(ticker).upper())
    return float(spec["multiplier"]) if spec else 1.0


def initial_margin(ticker):
    """Capital to carry one contract, or None for a cash instrument.

    None means "pay for it in full", which is the correct answer for a share
    and the wrong one for a future — that conflation is exactly what made every
    previous futures number unreadable.
    """
    spec = CONTRACT_SPECS.get(str(ticker).upper())
    return float(spec["margin"]) if spec else None


def notional(ticker, price, quantity=1):
    """What a position is really exposed to, in dollars."""
    return float(price) * contract_multiplier(ticker) * float(quantity)


def capital_required(ticker, price, quantity=1):
    """Cash consumed to open. Margin for a future, full price for a share."""
    margin = initial_margin(ticker)
    if margin is None:
        return float(price) * float(quantity)
    return margin * float(quantity)


def leverage_of(ticker, price, quantity=1):
    """Notional divided by capital committed. 1.0 for a fully-paid share."""
    capital = capital_required(ticker, price, quantity)
    if capital <= 0:
        return None
    return round(notional(ticker, price, quantity) / capital, 2)


def exposure_group(ticker):
    """What this instrument is a bet ON, or None when it is only itself.

    Two instruments sharing a group are one position however differently they
    are priced, quoted or settled.
    """
    return EXPOSURE_GROUPS.get(str(ticker).upper())


def is_fx(ticker):
    return str(ticker).upper().endswith("=X")


def is_future(ticker):
    return str(ticker).upper().endswith("=F")


def is_derivative(ticker):
    """Futures only. An FX spot pair is not a derivative."""
    return is_future(ticker)


def asset_class_of(ticker):
    """Broad class for diversification accounting, or 'equity' by default."""
    symbol = str(ticker).upper()
    entry = CATALOGUE.get(symbol) or MACRO_ETFS.get(symbol)
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
