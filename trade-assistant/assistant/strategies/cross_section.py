"""Universe-wide ranking — the half of the problem a per-ticker strategy cannot see.

A per-ticker strategy can answer "is this name in an uptrend". It cannot answer
"is this name in the top ten of the universe", because that question is about
every OTHER name too. This module is the missing half: one object is built per
scan (or per backtest) from the closes of the whole universe, handed to every
strategy through the context, and answers rank questions as of a date.

Two properties matter more than the arithmetic:

**No look-ahead, structurally.** Every matrix here is built from `.shift(n)` of
past closes only, and a lookup `as_of` a date returns the row at or before that
date, never after it. A cross-sectional rank computed with next month's returns
is the most flattering bug in quantitative finance and the hardest to spot in a
headline number, so it is closed by construction rather than by care.

**Built once, not per bar.** The momentum and volatility matrices are computed
for the whole universe over all of history the first time a strategy asks for
them, then cached. A ten-year replay over 93 instruments asks the same question
roughly 200,000 times; recomputing per bar would make the backtest unusable.

The closes are aligned onto one index and forward-filled, so a name that did not
trade on a given day carries its last known price. That is the only sane way to
compare instruments across exchange calendars, but it does mean a foreign name's
realised volatility is measured with a few flat days in it — a small,
downward-biased error worth knowing about when reading a low-volatility rank.
"""
import math

import pandas as pd


class CrossSection:
    """Ranks one universe of instruments against itself, as of any date."""

    def __init__(self, closes_by_ticker):
        """closes_by_ticker: {ticker: Series of closes}. Anything empty is dropped."""
        series = {}
        for ticker, closes in (closes_by_ticker or {}).items():
            if closes is None or len(closes) == 0:
                continue
            clean = pd.Series(closes).dropna()
            if clean.empty:
                continue
            series[str(ticker)] = clean

        if series:
            frame = pd.concat(series, axis=1).sort_index()
            # Duplicate timestamps would make a rank lookup ambiguous.
            self.closes = frame[~frame.index.duplicated(keep="last")].ffill()
        else:
            self.closes = pd.DataFrame()
        self._cache = {}

    @classmethod
    def from_frames(cls, frames_by_ticker):
        """Build from {ticker: OHLCV DataFrame} — what both callers actually hold."""
        closes = {}
        for ticker, df in (frames_by_ticker or {}).items():
            if df is None or len(df) == 0 or "Close" not in getattr(df, "columns", []):
                continue
            closes[ticker] = df["Close"]
        return cls(closes)

    @property
    def tickers(self):
        return list(self.closes.columns)

    def __len__(self):
        return len(self.closes.columns)

    # -- the two questions strategies ask ---------------------------------

    def momentum(self, ticker, as_of, lookback_bars=252, skip_bars=21):
        """Rank by return over `lookback_bars`, ending `skip_bars` ago.

        Rank 1 is the strongest name. The skip is not decoration: the most
        recent month reverses on average (Jegadeesh 1990), so including it
        contaminates a momentum signal with a reversal signal.
        """
        return self._lookup(("momentum", int(lookback_bars), int(skip_bars)),
                            ticker, as_of)

    def volatility(self, ticker, as_of, lookback_bars=60):
        """Rank by annualised realised volatility. Rank 1 is the CALMEST name."""
        return self._lookup(("volatility", int(lookback_bars)), ticker, as_of)

    # -- machinery ---------------------------------------------------------

    def _lookup(self, key, ticker, as_of):
        """{value, rank, count, percentile} for one name, or None if unrankable.

        percentile is where the name sits in the universe, 0-100, counted from
        the best: 8.0 means "in the strongest 8% of names ranked today".
        """
        matrix = self._matrix(key)
        if matrix is None or str(ticker) not in matrix.columns:
            return None
        row = self._row(matrix, as_of)
        if row is None:
            return None
        row = row.dropna()
        # A rank against one other instrument is not a cross-section, and a rank
        # against nothing is meaningless. Refuse rather than report rank 1 of 1.
        if str(ticker) not in row.index or len(row) < 2:
            return None

        lowest_is_best = key[0] == "volatility"
        ranks = row.rank(ascending=lowest_is_best, method="min")
        count = int(len(row))
        rank = int(ranks[str(ticker)])
        return {
            "value": float(row[str(ticker)]),
            "rank": rank,
            "count": count,
            "percentile": round(rank / count * 100, 1),
        }

    def _matrix(self, key):
        if key in self._cache:
            return self._cache[key]

        matrix = None
        if not self.closes.empty:
            if key[0] == "momentum":
                _, lookback, skip = key
                recent = self.closes.shift(skip)
                base = self.closes.shift(skip + lookback)
                matrix = recent / base.replace(0, math.nan) - 1
            elif key[0] == "volatility":
                _, lookback = key
                returns = self.closes.pct_change()
                matrix = returns.rolling(lookback).std(ddof=0) * math.sqrt(252) * 100

        self._cache[key] = matrix
        return matrix

    @staticmethod
    def _row(matrix, as_of):
        """The last row at or before `as_of`. Never a row after it."""
        if matrix is None or matrix.empty or as_of is None:
            return None
        try:
            label = matrix.index.asof(as_of)
        except (TypeError, ValueError, KeyError):
            return None
        # asof returns NaN when as_of predates the whole index.
        if label is None or (isinstance(label, float) and math.isnan(label)):
            return None
        try:
            row = matrix.loc[label]
        except KeyError:
            return None
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return row
