"""Batched close-price download — what makes a wide universe affordable.

The per-ticker provider issues one HTTP request per symbol. Measured on this
machine that is ~1.3 seconds each, so the full ~28,000-symbol US list would take
over ten hours and be throttled long before finishing. yfinance's own batch
endpoint pulls the same data at ~0.16s per symbol, which brings a full pull down
to a little over an hour and a 500-name universe down to about a minute.

This module exists only for the WIDE path — building a cross-section over
thousands of names. The single-ticker provider is still the right tool when a
strategy needs full OHLCV for one instrument, and nothing here replaces it.

Only closes are returned. A cross-sectional rank needs nothing else, and keeping
five columns for 28,000 symbols is several gigabytes of memory spent to answer a
question that uses one of them.
"""
import math

import pandas as pd

# Yahoo rejects very long symbol lists on one URL. 200 is comfortably inside
# what it accepts and large enough that per-request overhead stops mattering.
CHUNK = 200


def _download(symbols, period, threads):
    import yfinance as yf
    return yf.download(list(symbols), period=period, auto_adjust=True,
                       progress=False, group_by="ticker", threads=threads)


def _closes_from(frame, symbols):
    """Pull each symbol's Close out of yfinance's grouped frame.

    The shape differs between a one-symbol and a many-symbol request, which is
    a classic source of silent single-name breakage.
    """
    out = {}
    if frame is None or len(frame) == 0:
        return out
    if len(symbols) == 1:
        only = list(symbols)[0]
        if "Close" in getattr(frame, "columns", []):
            series = frame["Close"].dropna()
            if not series.empty:
                out[only] = series
        return out

    available = set(getattr(frame.columns, "levels", [[]])[0])
    for symbol in symbols:
        if symbol not in available:
            continue
        try:
            series = frame[symbol]["Close"].dropna()
        except (KeyError, IndexError):
            continue
        if not series.empty:
            out[symbol] = series
    return out


def close_history(symbols, period="2y", chunk=CHUNK, threads=True, progress_cb=None):
    """{symbol: close Series} for as many symbols as actually have data.

    Symbols that return nothing are omitted rather than carried as empty
    columns. Delisted and never-traded tickers are a large fraction of any full
    exchange listing, and an empty column would rank as missing data in some
    places and as a zero in others.
    """
    symbols = [s for s in dict.fromkeys(symbols) if s]
    closes, chunks = {}, math.ceil(len(symbols) / chunk) if symbols else 0
    for index in range(chunks):
        batch = symbols[index * chunk:(index + 1) * chunk]
        try:
            frame = _download(batch, period, threads)
        except Exception:
            # One bad chunk must not lose the other 27,000 symbols.
            frame = None
        closes.update(_closes_from(frame, batch))
        if progress_cb:
            progress_cb(min((index + 1) * chunk, len(symbols)), len(symbols), len(closes))
    return closes


def liquidity_table(symbols, period="3mo", chunk=CHUNK, progress_cb=None):
    """{symbol: {price, dollar_volume}} — the screen inputs, in one pass.

    Median dollar volume, not mean: a single news-day spike on an otherwise
    untraded shell would clear a mean-based filter, and that is exactly the kind
    of name a momentum ranking reaches for first.
    """
    import yfinance as yf

    symbols = [s for s in dict.fromkeys(symbols) if s]
    table, chunks = {}, math.ceil(len(symbols) / chunk) if symbols else 0
    for index in range(chunks):
        batch = symbols[index * chunk:(index + 1) * chunk]
        try:
            frame = yf.download(batch, period=period, auto_adjust=True,
                                progress=False, group_by="ticker", threads=True)
        except Exception:
            frame = None
        if frame is not None and len(frame):
            single = len(batch) == 1
            available = set(getattr(frame.columns, "levels", [[]])[0]) if not single else set(batch)
            for symbol in batch:
                if symbol not in available:
                    continue
                try:
                    part = frame if single else frame[symbol]
                    close = part["Close"].dropna()
                    volume = part["Volume"].dropna()
                except (KeyError, IndexError):
                    continue
                if close.empty or volume.empty:
                    continue
                paired = close.to_frame("c").join(volume.to_frame("v"), how="inner").dropna()
                if paired.empty:
                    continue
                table[symbol] = {
                    "price": float(paired["c"].iloc[-1]),
                    "dollar_volume": float((paired["c"] * paired["v"]).median()),
                    "bars": int(len(paired)),
                }
        if progress_cb:
            progress_cb(min((index + 1) * chunk, len(symbols)), len(symbols), len(table))
    return table


def ohlcv_history(symbols, period="2y", chunk=CHUNK, progress_cb=None):
    """{symbol: OHLCV DataFrame} — what the scanner and strategies need.

    Five times the payload of close_history, so this is for the rebalance day
    only. On an ordinary day the trader marks its open positions and needs
    nothing else.
    """
    import yfinance as yf

    symbols = [s for s in dict.fromkeys(symbols) if s]
    out, chunks = {}, math.ceil(len(symbols) / chunk) if symbols else 0
    for index in range(chunks):
        batch = symbols[index * chunk:(index + 1) * chunk]
        try:
            frame = yf.download(batch, period=period, auto_adjust=True,
                                progress=False, group_by="ticker", threads=True)
        except Exception:
            frame = None
        if frame is not None and len(frame):
            single = len(batch) == 1
            available = (set(batch) if single
                         else set(getattr(frame.columns, "levels", [[]])[0]))
            for symbol in batch:
                if symbol not in available:
                    continue
                try:
                    part = (frame if single else frame[symbol]).dropna(subset=["Close"])
                except (KeyError, IndexError):
                    continue
                if len(part) and {"Open", "High", "Low", "Close", "Volume"} <= set(part.columns):
                    out[symbol] = part
        if progress_cb:
            progress_cb(min((index + 1) * chunk, len(symbols)), len(symbols), len(out))
    return out


def to_frame(closes):
    """Align a {symbol: Series} map into one forward-filled frame."""
    if not closes:
        return pd.DataFrame()
    frame = pd.concat(closes, axis=1).sort_index()
    return frame[~frame.index.duplicated(keep="last")].ffill()
