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
import time

import pandas as pd

from .base import ProviderUnavailable

# Yahoo rejects very long symbol lists on one URL. 100 is comfortably inside
# what it accepts, and small enough that one throttled chunk loses less.
CHUNK = 100

# A chunk of 100 real listings never legitimately returns nothing. When it does,
# the request was rate-limited, and retrying after a pause recovers it. Getting
# this wrong is expensive in a way that is easy to miss: a throttled run looks
# exactly like a universe of delisted shells, and screening the full US listing
# once produced 537 "tradable" names with NVDA, MSFT, SPY and QQQ all absent.
EMPTY_CHUNK_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5
# Breathing room between chunks. Cheap insurance against tripping the limiter
# in the first place, which is far cheaper than recovering from it.
CHUNK_PAUSE_SECONDS = 0.4


class ThrottleSuspected(Exception):
    """Raised when a wide pull yields so little that the data cannot be trusted.

    Deliberately an exception rather than a partial result. A caller that gets
    back 12% of a universe has no way to tell throttling from genuine delisting,
    and the failure mode is silent: it caches, and every later run is built on
    an arbitrary slice of the market.
    """


def _download(symbols, period, threads=True):
    import yfinance as yf
    return yf.download(list(symbols), period=period, auto_adjust=True,
                       progress=False, group_by="ticker", threads=threads)


def _download_with_retry(symbols, period, extract, threads=True):
    """Download one chunk, retrying while it comes back completely empty.

    Returns (rows, recovered) so the caller can count how much of the run needed
    a retry — a high count is the signal to slow down, not to carry on.
    """
    for attempt in range(EMPTY_CHUNK_RETRIES + 1):
        try:
            frame = _download(symbols, period, threads)
            rows = extract(frame, symbols)
        except Exception:
            rows = {}
        if rows or len(symbols) < 5:
            return rows, attempt > 0
        if attempt < EMPTY_CHUNK_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
    return {}, True


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
        rows, _ = _download_with_retry(batch, period, _closes_from, threads)
        closes.update(rows)
        if index + 1 < chunks:
            time.sleep(CHUNK_PAUSE_SECONDS)
        if progress_cb:
            progress_cb(min((index + 1) * chunk, len(symbols)), len(symbols), len(closes))
    return closes


def liquidity_table(symbols, period="3mo", chunk=CHUNK, progress_cb=None,
                    on_batch=None):
    """{symbol: {price, dollar_volume}} — the screen inputs, in one pass.

    Median dollar volume, not mean: a single news-day spike on an otherwise
    untraded shell would clear a mean-based filter, and that is exactly the kind
    of name a momentum ranking reaches for first.
    """
    symbols = [s for s in dict.fromkeys(symbols) if s]
    table, chunks = {}, math.ceil(len(symbols) / chunk) if symbols else 0
    exhausted = 0
    for index in range(chunks):
        batch = symbols[index * chunk:(index + 1) * chunk]
        rows, recovered = _download_with_retry(batch, period, _liquidity_from)
        if not rows and len(batch) >= 5:
            exhausted += 1
        table.update(rows)
        # Hand each batch to the caller as it lands, so a run that dies to the
        # rate limiter later still leaves this work behind.
        if on_batch and rows:
            on_batch(dict(table))
        if index + 1 < chunks:
            time.sleep(CHUNK_PAUSE_SECONDS)
        if progress_cb:
            progress_cb(min((index + 1) * chunk, len(symbols)), len(symbols), len(table))

        # Stop as soon as the limiter is clearly winning. Pressing on makes it
        # worse — every further request deepens the throttle — and the partial
        # table is already safe, so there is nothing to gain by continuing.
        done_chunks = index + 1
        if done_chunks >= 10 and exhausted > done_chunks / 3:
            raise ThrottleSuspected(
                f"{exhausted} of {done_chunks} chunks returned nothing even after "
                "retries. This is rate limiting, not delisting. Measurements taken "
                "so far are kept; re-run when the limit has reset to continue.")
    return table


def _liquidity_from(frame, symbols):
    """Price / median dollar volume / bar count, from one grouped frame."""
    out = {}
    if frame is None or len(frame) == 0:
        return out
    single = len(symbols) == 1
    available = set(symbols) if single else set(getattr(frame.columns, "levels", [[]])[0])
    for symbol in symbols:
        if symbol not in available:
            continue
        try:
            part = frame if single else frame[symbol]
            close, volume = part["Close"].dropna(), part["Volume"].dropna()
        except (KeyError, IndexError):
            continue
        if close.empty or volume.empty:
            continue
        paired = close.to_frame("c").join(volume.to_frame("v"), how="inner").dropna()
        if paired.empty:
            continue
        out[symbol] = {
            "price": float(paired["c"].iloc[-1]),
            "dollar_volume": float((paired["c"] * paired["v"]).median()),
            "bars": int(len(paired)),
        }
    return out


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


def ohlcv_history_ibkr(symbols, period="2y", config=None, progress_cb=None):
    """{symbol: OHLCV DataFrame} from IBKR over ONE connection.

    The provider opens a socket per call, which is right for a handful of
    lookups and wrong for fifteen hundred: the handshake dominates. Measured at
    1.44 seconds per instrument on one held-open connection, so the full
    universe is about half an hour — fine for a monthly rebalance, and ordinary
    days only mark the dozen positions actually held.

    A symbol that returns nothing is omitted rather than carried as an empty
    frame. With IBKR "nothing" almost always means the account lacks a
    market-data subscription for that venue, not that the instrument is
    untradeable, so the caller is told how many were lost rather than left to
    infer it from a short list.
    """
    import pandas as pd

    from .ibkr_provider import IBKRDataProvider

    provider = IBKRDataProvider(config)
    if not provider.is_available():
        raise ProviderUnavailable("ibkr: not enabled")

    ib = provider._connect()
    out, missing = {}, []
    try:
        for index, symbol in enumerate(dict.fromkeys(symbols), start=1):
            try:
                contract = provider._contract_for(symbol)
                if str(symbol).upper().endswith("=F"):
                    contract = provider._resolve_future(ib, contract) or contract
                bars = ib.reqHistoricalData(
                    contract, endDateTime="", durationStr=provider._duration(period),
                    barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
                    formatDate=1)
                # The same trailing-dot retry get_prices does. Without it this
                # path — the one that actually builds the tradable universe —
                # silently dropped London lines whose IB symbol carries a dot:
                # BT-A.L, AV.L, BA.L, SN.L, CPG.L all failed here while the
                # identical lookup succeeded through get_prices. A fix applied
                # to one of two fetch paths is not a fix.
                if not bars:
                    for variant in provider._symbol_variants(symbol):
                        probe = provider._contract_for(symbol)
                        probe.symbol = variant
                        bars = ib.reqHistoricalData(
                            probe, endDateTime="",
                            durationStr=provider._duration(period),
                            barSizeSetting="1 day", whatToShow="TRADES",
                            useRTH=True, formatDate=1)
                        if bars:
                            break
                if bars:
                    frame = pd.DataFrame([{
                        "Open": b.open, "High": b.high, "Low": b.low,
                        "Close": b.close, "Volume": b.volume} for b in bars],
                        index=pd.to_datetime([b.date for b in bars]))
                    frame = frame.dropna(subset=["Close"])
                    if len(frame):
                        out[symbol] = frame
                    else:
                        missing.append(symbol)
                else:
                    missing.append(symbol)
            except Exception:
                missing.append(symbol)
            if progress_cb and index % 100 == 0:
                progress_cb(index, len(symbols), len(out))
    finally:
        ib.disconnect()
    return out, missing
