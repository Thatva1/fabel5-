"""Risk and return metrics on an equity curve — pure functions, no I/O.

Two mistakes this module exists to prevent, both of which have already been made
in this project:

  * **Drawdown divided by the wrong denominator.** A £30,000 fall must be
    measured against the peak equity AT THE TIME, not against the final or
    starting value. Dividing by a later, larger peak understates every early
    drawdown — which is exactly how an 27% fall got reported as 18%.
  * **Drawdown quoted in pounds with no denominator at all.** "£55,837" means
    nothing without knowing whether the book was £100k or £1m.

It also adds the two figures that decide whether a strategy is actually
survivable: how LONG you spend underwater, and how bad the downside volatility
is as distinct from volatility in general. A 9% drawdown you recover from in a
month and a 9% drawdown that lasts three years are not the same experience, and
CAGR-plus-max-drawdown cannot tell them apart.
"""
import math

TRADING_DAYS = 252


def drawdown_series(equity):
    """(drawdown_fraction, running_peak) for each point, peak measured AT THE TIME."""
    peaks, drawdowns, peak = [], [], None
    for value in equity:
        peak = value if peak is None else max(peak, value)
        peaks.append(peak)
        drawdowns.append(0.0 if peak <= 0 else (peak - value) / peak)
    return drawdowns, peaks


def max_drawdown(equity):
    """Worst peak-to-trough fall, as a fraction and in currency.

    Returns {pct, amount, peak, trough, peak_index, trough_index}.
    """
    if not equity:
        return {"pct": None, "amount": None, "peak": None, "trough": None,
                "peak_index": None, "trough_index": None}

    # The running best is kept as a FRACTION and converted to a percentage only
    # on the way out. Storing it as a percentage while comparing against a
    # fraction means that after the first drawdown nothing can ever beat it
    # (0.5 > 4.55 is false), so the function silently returns the FIRST
    # drawdown instead of the largest.
    worst = {"fraction": 0.0, "amount": 0.0, "peak": equity[0], "trough": equity[0],
             "peak_index": 0, "trough_index": 0}
    peak, peak_index = equity[0], 0
    for i, value in enumerate(equity):
        if value > peak:
            peak, peak_index = value, i
        if peak > 0:
            fraction = (peak - value) / peak
            if fraction > worst["fraction"]:
                worst = {"fraction": fraction, "amount": peak - value,
                         "peak": peak, "trough": value,
                         "peak_index": peak_index, "trough_index": i}
    return {
        "pct": round(worst["fraction"] * 100, 2),
        "amount": round(worst["amount"], 2),
        "peak": round(worst["peak"], 2),
        "trough": round(worst["trough"], 2),
        "peak_index": worst["peak_index"],
        "trough_index": worst["trough_index"],
    }


def time_underwater(equity, dates=None):
    """How long the curve spends below a previous peak.

    The metric that separates a strategy you can live with from one you abandon
    at the bottom. Max drawdown says how deep; this says how long.

    Returns {longest_periods, current_periods, pct_of_time, longest_start,
    longest_end} — periods are whatever the equity points are spaced by.
    """
    if not equity:
        return {"longest_periods": 0, "current_periods": 0, "pct_of_time": None,
                "longest_start": None, "longest_end": None}

    longest = run = 0
    peak = equity[0]
    start_index = longest_start = longest_end = 0
    underwater_points = 0

    for i, value in enumerate(equity):
        if value >= peak:
            peak = value
            run = 0
            start_index = i
        else:
            run += 1
            underwater_points += 1
            if run > longest:
                longest, longest_start, longest_end = run, start_index, i

    out = {
        "longest_periods": longest,
        "current_periods": run,
        "pct_of_time": round(underwater_points / len(equity) * 100, 1),
        "longest_start": _at(dates, longest_start),
        "longest_end": _at(dates, longest_end),
    }
    return out


def _at(dates, index):
    if not dates or index is None or index >= len(dates):
        return None
    return str(dates[index])[:10]


def returns_from_equity(equity):
    """Period-over-period fractional returns."""
    out = []
    for previous, current in zip(equity, equity[1:]):
        out.append((current / previous - 1) if previous else 0.0)
    return out


def cagr(equity, years):
    """Compound annual growth rate. None when it cannot be computed —
    a negative or zero final value has no real-valued growth rate."""
    if not equity or years <= 0:
        return None
    start, end = equity[0], equity[-1]
    if start <= 0 or end <= 0:
        return None
    return round(((end / start) ** (1 / years) - 1) * 100, 2)


def _stdev(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def sharpe(equity, periods_per_year=TRADING_DAYS, risk_free_rate=0.0):
    """Return per unit of TOTAL volatility, annualised."""
    rets = returns_from_equity(equity)
    if len(rets) < 2:
        return None
    excess = [r - risk_free_rate / periods_per_year for r in rets]
    sd = _stdev(excess)
    if not sd:
        return None
    mean = sum(excess) / len(excess)
    return round(mean / sd * math.sqrt(periods_per_year), 2)


def sortino(equity, periods_per_year=TRADING_DAYS, risk_free_rate=0.0):
    """Return per unit of DOWNSIDE volatility.

    Preferred here over Sharpe: Sharpe penalises upside swings as much as
    downside ones, which misprices a strategy whose whole shape is small
    frequent losses and occasional 3R winners. Only the losses are the risk.
    """
    rets = returns_from_equity(equity)
    if len(rets) < 2:
        return None
    target = risk_free_rate / periods_per_year
    excess = [r - target for r in rets]
    downside = [min(0.0, r) for r in excess]
    downside_dev = math.sqrt(sum(d ** 2 for d in downside) / len(downside))
    if downside_dev == 0:
        return None
    mean = sum(excess) / len(excess)
    return round(mean / downside_dev * math.sqrt(periods_per_year), 2)


def calmar(equity, years):
    """CAGR divided by max drawdown — return per unit of worst-case pain."""
    growth = cagr(equity, years)
    dd = max_drawdown(equity)["pct"]
    if growth is None or not dd:
        return None
    return round(growth / dd, 2)


def summarise_curve(equity, dates=None, periods_per_year=TRADING_DAYS,
                    risk_free_rate=0.0):
    """Everything about an equity curve, in one dict."""
    if not equity:
        return {}
    years = max(len(equity) - 1, 1) / periods_per_year
    dd = max_drawdown(equity)
    underwater = time_underwater(equity, dates)
    return {
        "start_equity": round(equity[0], 2),
        "final_equity": round(equity[-1], 2),
        "total_return_pct": (round((equity[-1] / equity[0] - 1) * 100, 2)
                             if equity[0] else None),
        "years": round(years, 2),
        "cagr_pct": cagr(equity, years),
        "max_drawdown_pct": dd["pct"],
        "max_drawdown_amount": dd["amount"],
        "max_drawdown_peak": dd["peak"],
        "max_drawdown_trough": dd["trough"],
        "max_drawdown_start": _at(dates, dd["peak_index"]),
        "max_drawdown_end": _at(dates, dd["trough_index"]),
        "longest_underwater_periods": underwater["longest_periods"],
        "longest_underwater_start": underwater["longest_start"],
        "longest_underwater_end": underwater["longest_end"],
        "pct_of_time_underwater": underwater["pct_of_time"],
        "sharpe": sharpe(equity, periods_per_year, risk_free_rate),
        "sortino": sortino(equity, periods_per_year, risk_free_rate),
        "calmar": calmar(equity, years),
    }
