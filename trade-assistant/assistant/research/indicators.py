"""Shared technical indicators — pure functions over a price DataFrame.

Every strategy and the regime classifier compute from here, so a change to (say)
how ATR is smoothed lands everywhere at once instead of drifting between modules.
Nothing here does I/O and nothing here makes a decision; these are just numbers.

Inputs are pandas objects with the yfinance column names (Open/High/Low/Close/Volume).
Functions return pandas Series unless the name ends in a scalar-ish word.
"""
import math

import pandas as pd


def rsi(closes, period=14):
    """Relative Strength Index, simple-average form.

    The edge cases are handled explicitly because the plain formula divides by
    the average loss. A window with NO losing days has an average loss of zero,
    so RS is undefined and the naive result is NaN — which reads downstream as
    "no RSI data" and silently disqualifies the strongest trends in the scan,
    the exact names momentum is looking for. By convention an all-up window is
    RSI 100, an all-down window is 0, and a window that did not move is 50.
    """
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, math.nan)
    out = 100 - (100 / (1 + rs))
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((gain == 0) & (loss > 0), 0.0)
    return out.mask((gain == 0) & (loss == 0), 50.0)


def true_range(df):
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(df, period=14):
    """Average True Range, simple mean (matches the original scanner)."""
    return true_range(df).rolling(period).mean()


def _wilder(series, period):
    """Wilder's smoothing — the averaging ADX is defined with. It is an EMA with
    alpha = 1/period, not the 2/(period+1) alpha pandas uses by default."""
    return series.ewm(alpha=1 / period, adjust=False).mean()


def adx(df, period=14):
    """Average Directional Index — trend STRENGTH, not direction.

    Returns a DataFrame with adx / plus_di / minus_di columns. Reading:
    below ~20 there is no trend worth following; above ~25 a trend is real.
    +DI above -DI means the trend is up, the reverse means down.
    """
    high, low = df["High"], df["Low"]
    up_move = high.diff()
    down_move = -low.diff()

    # A bar only counts toward one direction: the larger move wins, and a move
    # that is negative counts as zero rather than flipping sign.
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)

    tr_smooth = _wilder(true_range(df), period)
    plus_di = 100 * _wilder(plus_dm, period) / tr_smooth.replace(0, math.nan)
    minus_di = 100 * _wilder(minus_dm, period) / tr_smooth.replace(0, math.nan)

    di_sum = (plus_di + minus_di).replace(0, math.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return pd.DataFrame({"adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di})


def bollinger(closes, period=20, num_std=2.0):
    """Bollinger bands plus band width as a percentage of the middle band.

    width_pct is the squeeze/expansion measure: it is scale-free, so a £5 stock
    and a £500 stock are directly comparable.
    """
    middle = closes.rolling(period).mean()
    std = closes.rolling(period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    width_pct = (upper - lower) / middle.replace(0, math.nan) * 100
    return pd.DataFrame({"middle": middle, "upper": upper, "lower": lower,
                         "width_pct": width_pct})


def percentile_rank(series, lookback=120):
    """Where the latest value sits within its own recent history, 0-100.

    0 means "lowest in the lookback window" — that is what a volatility squeeze
    looks like. Returns None when there is not enough history to be meaningful.
    """
    window = series.dropna().tail(lookback)
    if len(window) < 20:
        return None
    latest = window.iloc[-1]
    if math.isnan(latest):
        return None
    # Values within a hair of the latest count as ties. Without this, a series
    # whose width is effectively CONSTANT ranks at the 1st percentile purely on
    # floating-point drift — and a flat, unchanging band width would be read as
    # a volatility squeeze, which is the opposite of what it means.
    scale = max(abs(float(window.max())), 1e-12)
    tolerance = scale * 1e-6
    return round(float((window <= latest + tolerance).sum()) / len(window) * 100, 1)


def slope_pct(series, periods=21):
    """Percentage change of a series over `periods` bars — used for 200-day MA
    direction. A flat-to-rising MA and a rolling-over MA are different regimes
    even when price sits on the same side of it."""
    clean = series.dropna()
    if len(clean) <= periods:
        return None
    past = float(clean.iloc[-(periods + 1)])
    if past == 0:
        return None
    return round((float(clean.iloc[-1]) / past - 1) * 100, 2)


def relative_strength(closes, benchmark_closes, days=63):
    """Ticker return minus benchmark return over `days` bars, in percentage points.

    Positive means the name is outperforming its index — the momentum strategy
    prefers those. Returns None when the two series cannot be aligned.
    """
    if benchmark_closes is None:
        return None
    joined = (closes.to_frame("t")
              .join(benchmark_closes.to_frame("b"), how="inner")
              .dropna())
    if len(joined) <= days:
        return None
    t_ret = float(joined["t"].iloc[-1]) / float(joined["t"].iloc[-(days + 1)]) - 1
    b_ret = float(joined["b"].iloc[-1]) / float(joined["b"].iloc[-(days + 1)]) - 1
    return round((t_ret - b_ret) * 100, 2)


def total_return(closes, lookback_bars, skip_bars=0):
    """Return over `lookback_bars`, ending `skip_bars` bars ago, as a fraction.

    The skip is what separates a momentum signal from a reversal one. The most
    recent month tends to mean-revert (Jegadeesh 1990), so the classic momentum
    signal is "12 months, skipping the last one" — 252 bars back, ending 21 bars
    ago. Returns None when there is not enough history to measure it honestly.
    """
    clean = closes.dropna()
    need = int(lookback_bars) + int(skip_bars) + 1
    if len(clean) < need:
        return None
    recent = float(clean.iloc[-(int(skip_bars) + 1)])
    base = float(clean.iloc[-need])
    if base == 0 or not is_finite(base) or not is_finite(recent):
        return None
    return recent / base - 1


def realized_vol(closes, lookback_bars=60, periods_per_year=252):
    """Annualised standard deviation of daily returns, in percent."""
    clean = closes.dropna()
    if len(clean) < int(lookback_bars) + 1:
        return None
    returns = clean.pct_change().dropna().tail(int(lookback_bars))
    if len(returns) < int(lookback_bars):
        return None
    sd = float(returns.std(ddof=0))
    if not is_finite(sd):
        return None
    return round(sd * math.sqrt(periods_per_year) * 100, 2)


def beta(closes, benchmark_closes, lookback_bars=252):
    """Sensitivity to the benchmark: cov(stock, index) / var(index).

    Beta 1.0 moves with the index, 0.6 moves 60% as much. Both series are joined
    on their shared dates first, because a stock and an index on different
    exchange calendars have different holidays, and pairing a Tuesday return
    with a Wednesday one produces a number that looks like beta and is not.
    """
    if benchmark_closes is None or closes is None:
        return None
    joined = (closes.to_frame("t")
              .join(benchmark_closes.to_frame("b"), how="inner")
              .dropna())
    if len(joined) < int(lookback_bars) + 1:
        return None
    returns = joined.pct_change().dropna().tail(int(lookback_bars))
    if len(returns) < int(lookback_bars):
        return None

    stock, index = returns["t"], returns["b"]
    index_centred = index - index.mean()
    variance = float((index_centred ** 2).mean())
    if not is_finite(variance) or variance <= 0:
        return None
    covariance = float(((stock - stock.mean()) * index_centred).mean())
    if not is_finite(covariance):
        return None
    return round(covariance / variance, 3)


def above_moving_average(closes, period=200):
    """True/False for "the latest close is above its own average", or None.

    None means "not enough history to say", which is different from False and
    must stay different — a strategy that treats an unanswerable trend question
    as a failed one behaves correctly; one that treats it as passed does not.
    """
    clean = closes.dropna()
    if len(clean) < int(period):
        return None
    average = latest(clean.rolling(int(period)).mean())
    price = latest(clean)
    if average is None or price is None:
        return None
    return price > average


def recent_extremes(df, lookback=20):
    """Highest high / lowest low over the last `lookback` COMPLETED bars.

    Excludes today deliberately: "broke above the 20-day high" is meaningless if
    today's own high is part of the high being broken.
    """
    if len(df) < lookback + 1:
        return None, None
    window = df.iloc[-(lookback + 1):-1]
    return float(window["High"].max()), float(window["Low"].min())


def touches_in_band(series, level, tolerance_pct, lookback=60):
    """How many of the last `lookback` bars came within tolerance of a level.

    Range trading needs a level price has actually respected more than once; a
    line touched a single time is not support, it is a coincidence.
    """
    window = series.dropna().tail(lookback)
    if window.empty or not level:
        return 0
    tolerance = abs(level) * tolerance_pct / 100
    return int((window.sub(level).abs() <= tolerance).sum())


def is_finite(value):
    """True when a value is a real number we can safely put in a decision."""
    if value is None:
        return False
    try:
        return not (math.isnan(float(value)) or math.isinf(float(value)))
    except (TypeError, ValueError):
        return False


def latest(series):
    """Last value of a series as a plain float, or None if unusable."""
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    return float(value) if is_finite(value) else None
