"""Ties the pieces together: replay → portfolio → benchmarks → validation.

Three things live here that the individual modules deliberately do not know
about, because they are judgements rather than mechanics:

  * **The benchmark.** A strategy's return means nothing on its own. Buy-and-hold
    of the SAME universe over the SAME period is the honest comparison, because
    it isolates "did the timing add value" from "were these good stocks". A
    blended equity/cash portfolio is added too, since a lower-return
    lower-drawdown strategy must beat the trivially available alternative of
    simply holding less equity.

  * **Out-of-sample validation.** Every parameter in this project was chosen by
    looking at one decade. Splitting that decade and reporting both halves
    separately is the only defence against fitting the noise — and a result that
    appears in one half and not the other was never a result.

  * **Sequence sensitivity.** Compounding makes the ORDER of returns decisive.
    A strategy whose good years happen to precede its bad one looks far better
    than the same trades reshuffled, so the spread across shuffles is reported.
"""
import random

from . import metrics, portfolio


def build_market_states(candidates, index_series, config):
    """Risk-on/off per index per entry date, carrying hysteresis forward.

    State is walked in date order because the re-entry buffer depends on the
    previous state — evaluating each date independently would lose the very
    hysteresis that stops the whipsaw.
    """
    from ..research import market_regime as mr

    needed = {}
    for candidate in candidates:
        index = candidate.get("index_symbol")
        if index:
            needed.setdefault(index, set()).add(candidate["entry_date"])

    states = {}
    for index, dates in needed.items():
        series = index_series.get(index)
        if series is None:
            continue
        previous = None
        for date in sorted(dates):
            window = series.loc[:date]
            result = mr.classify_market(window, config, previous_state=previous)
            previous = result["state"] if result["state"] != mr.UNKNOWN else previous
            states[(index, date)] = result["state"]
    return states


def buy_and_hold(price_frames, config, start_date=None, end_date=None,
                 fx_lookup=None, calendar=None):
    """Equal-weight, fully invested, no leverage — the benchmark that matters.

    Deliberately compared on the SAME universe as the strategy. Benchmarking
    against a broad index instead conflates two questions: whether the timing
    adds value, and whether the stock selection does.
    """
    cfg = portfolio.settings(config)
    capital = float(cfg["starting_equity"])
    usable = {t: df for t, df in price_frames.items() if df is not None and len(df) > 1}
    if not usable:
        return {}

    per_name = capital / len(usable)
    holdings, dates = {}, calendar or []
    for ticker, df in usable.items():
        window = df.loc[start_date:end_date] if (start_date or end_date) else df
        if len(window) < 2:
            continue
        entry = float(window["Open"].iloc[0])
        if entry <= 0:
            continue
        fx = fx_lookup(ticker, str(window.index[0])[:10]) if fx_lookup else 1.0
        holdings[ticker] = {"shares": per_name / (entry * (fx or 1.0)),
                            "fx": fx or 1.0, "window": window}
        if not calendar:
            dates = sorted(set(dates) | {str(d)[:10] for d in window.index})

    if not holdings:
        return {}

    curve = []
    for date in dates:
        total = 0.0
        for holding in holdings.values():
            window = holding["window"].loc[:date]
            if len(window) == 0:
                total += 0.0
                continue
            total += holding["shares"] * float(window["Close"].iloc[-1]) * holding["fx"]
        curve.append(round(total, 2))

    if not curve:
        return {}
    summary = metrics.summarise_curve(curve, dates, periods_per_year=len(curve) /
                                      max(_years(dates), 1e-6))
    summary["label"] = f"Buy & hold, equal weight, {len(holdings)} instruments"
    return summary


def blended_benchmark(buyhold_curve, dates, equity_weight=0.6, cash_rate_pct=2.0):
    """A passive mix of the same buy-and-hold and cash.

    The comparison a low-return low-drawdown strategy must actually beat: you
    can always reduce risk for free by simply holding less equity, so beating
    100% equities on drawdown alone proves nothing.
    """
    if not buyhold_curve:
        return {}
    start = buyhold_curve[0]
    years = _years(dates)
    periods = max(len(buyhold_curve) - 1, 1)
    per_period_cash = (1 + cash_rate_pct / 100) ** (years / periods) - 1

    curve, cash = [], start * (1 - equity_weight)
    for i, value in enumerate(buyhold_curve):
        equity_part = start * equity_weight * (value / start)
        if i:
            cash *= 1 + per_period_cash
        curve.append(round(equity_part + cash, 2))
    summary = metrics.summarise_curve(curve, dates,
                                      periods_per_year=len(curve) / max(years, 1e-6))
    summary["label"] = (f"{int(equity_weight * 100)}/{int((1 - equity_weight) * 100)} "
                        f"buy & hold / cash at {cash_rate_pct}%")
    return summary


def split_candidates(candidates, split_date):
    """Development set / validation set, by entry date."""
    develop = [c for c in candidates if c["entry_date"] < split_date]
    validate = [c for c in candidates if c["entry_date"] >= split_date]
    return develop, validate


def validate_out_of_sample(candidates, config, split_date, min_trades=20, **kwargs):
    """Run both halves and say plainly whether the result survived.

    A configuration that works in the development half and fails in the
    validation half was fitted to the first half. This is the check that every
    finding in this project has so far been missing.

    `min_trades` guards against the opposite error — declaring a verdict from a
    handful of trades. A half with too few is reported as inconclusive rather
    than given a confident-sounding label it cannot support.
    """
    develop, validate = split_candidates(candidates, split_date)
    in_sample = portfolio.simulate(develop, config, **kwargs)
    out_sample = portfolio.simulate(validate, config, **kwargs)

    in_cagr = (in_sample["metrics"] or {}).get("cagr_pct")
    out_cagr = (out_sample["metrics"] or {}).get("cagr_pct")
    in_n, out_n = len(in_sample["trades"]), len(out_sample["trades"])

    verdict, note = "inconclusive", ""
    if in_n < min_trades or out_n < min_trades:
        note = (f"Not enough trades to judge: {in_n} in development and {out_n} in "
                f"validation, against a {min_trades} minimum in each.")
    elif in_cagr is None or out_cagr is None:
        note = "Not enough trades in one half to judge."
    elif out_cagr <= 0 < in_cagr:
        verdict = "FAILED"
        note = ("Profitable in the development period and not in validation — "
                "the configuration is fitted to the first half.")
    elif out_cagr > 0 and in_cagr > 0:
        decay = (in_cagr - out_cagr) / abs(in_cagr) * 100 if in_cagr else 0
        verdict = "SURVIVED" if decay < 50 else "WEAKENED"
        note = (f"Return fell {decay:.0f}% from development to validation. "
                + ("Consistent enough to take seriously." if decay < 50 else
                   "Most of the edge did not carry over."))
    elif in_cagr <= 0:
        note = "Unprofitable in the development period; nothing to validate."

    return {
        "split_date": split_date,
        "develop": {"trades": len(in_sample["trades"]), **(in_sample["metrics"] or {})},
        "validate": {"trades": len(out_sample["trades"]), **(out_sample["metrics"] or {})},
        "verdict": verdict,
        "note": note,
    }


def sequence_sensitivity(trades, config, shuffles=200, seed=7):
    """How much of the result depends on the ORDER the years arrived in.

    Compounding makes sequence decisive: a bad year landing after three good
    ones hits a large base and recovers, while the same year first can end the
    experiment. If a good result only holds when the crash arrives late, it is
    fragile rather than robust.
    """
    pnls = [t.get("pnl_base") for t in trades if t.get("pnl_base") is not None]
    if len(pnls) < 10:
        return {}

    cfg = portfolio.settings(config)
    start = float(cfg["starting_equity"])
    rng = random.Random(seed)
    finals, drawdowns = [], []

    for _ in range(shuffles):
        order = pnls[:]
        rng.shuffle(order)
        equity, curve = start, [start]
        for pnl in order:
            equity += pnl
            curve.append(equity)
        finals.append(equity)
        drawdowns.append(metrics.max_drawdown(curve)["pct"])

    finals.sort()
    drawdowns.sort()
    return {
        "shuffles": shuffles,
        "final_median": round(finals[len(finals) // 2], 2),
        "final_worst_5pct": round(finals[int(len(finals) * 0.05)], 2),
        "final_best_5pct": round(finals[int(len(finals) * 0.95)], 2),
        "drawdown_median_pct": drawdowns[len(drawdowns) // 2],
        "drawdown_worst_5pct_pct": drawdowns[int(len(drawdowns) * 0.95)],
    }


def _years(dates):
    if not dates or len(dates) < 2:
        return 1.0
    from datetime import date as _date
    try:
        first = _date.fromisoformat(str(dates[0])[:10])
        last = _date.fromisoformat(str(dates[-1])[:10])
        return max((last - first).days / 365.25, 1e-6)
    except Exception:
        return 1.0
