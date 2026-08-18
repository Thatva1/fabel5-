"""Find out whether a shorter horizon works — with evidence, not opinion.

WHAT "TRAINING" CAN AND CANNOT MEAN HERE

Nothing in this library is a trained model. There are no weights, no gradient,
nothing that learns. Each strategy is a rule with published parameters —
De Bondt & Thaler's three-year reversal, Jegadeesh & Titman's twelve-month
momentum — and those numbers came from the papers, not from this data.

So "train it to be faster" has to mean one of two different things, and they
have very different costs:

  1. RE-FIT the existing rules at shorter lookbacks and keep what survives.
     That is what this module does. It is also the single easiest way to
     produce a lie: sweep enough settings against one history and something
     will look excellent by chance alone.

  2. BUILD strategies designed for a short horizon. A three-year reversal
     does not become a two-hour strategy by changing a number — it becomes a
     different, untested rule wearing a citation it has no claim to. Genuinely
     intraday rules live in research/intraday.py and are tested separately.

HOW THIS AVOIDS FOOLING YOU

Every configuration is scored on data it was NOT chosen against. The history
splits in two: settings are ranked on the first half, and the winner is then
run once on the second. A setting that looks wonderful in-sample and collapses
out-of-sample is reported as exactly that, because the failure mode being
guarded against is finding a 40-day lookback that "works" on one decade.

The number to read is not the best in-sample score. It is the DEGRADATION
between the halves, and whether the out-of-sample result still beats doing
nothing.
"""
import itertools
import math
from datetime import datetime


def midpoint_split(frames):
    """The date that halves the available history.

    A fixed split date cannot be right for an arbitrary window. Splitting five
    years at 2022-06-27 left 213 bars in the first half against the engine's
    260-bar warmup, so the in-sample side emitted no signals at all and every
    configuration scored zero — a sweep that silently measured nothing.
    """
    dates = []
    for frame in frames.values():
        try:
            index = frame.index
            if getattr(index, "tz", None) is not None:
                index = index.tz_localize(None)
            dates.extend([index[0], index[-1]])
        except Exception:
            continue
    if not dates:
        return None
    start, end = min(dates), max(dates)
    return (start + (end - start) / 2).strftime("%Y-%m-%d")


def usable(frames, config, label):
    """Is there enough history here for the engine to emit anything?

    Reported rather than assumed: an empty result from too-short data looks
    exactly like an empty result from a rule that does not work.
    """
    from . import engine

    cfg = engine.settings(config)
    warmup = int(cfg.get("warmup_bars", 260))
    longest = max((len(f) for f in frames.values()), default=0)
    return {"label": label, "instruments": len(frames), "longest_bars": longest,
            "warmup_bars": warmup, "ok": longest > warmup + 20,
            "note": (None if longest > warmup + 20 else
                     f"{label}: longest series is {longest} bars against a "
                     f"{warmup}-bar warmup — the engine cannot emit a signal here.")}


def _split_frames(frames, split_at):
    """Split each instrument's history at a date, keeping both halves."""
    first, second = {}, {}
    cutoff = datetime.fromisoformat(split_at) if isinstance(split_at, str) else split_at
    for ticker, frame in frames.items():
        try:
            index = frame.index
            if getattr(index, "tz", None) is not None:
                index = index.tz_localize(None)
            mask = index < cutoff
            a, b = frame[mask], frame[~mask]
        except Exception:
            continue
        if len(a) > 60:
            first[ticker] = a
        if len(b) > 60:
            second[ticker] = b
    return first, second


def grid(overrides):
    """Every combination of the values given.

    `overrides` is {parameter: [values]}. Kept explicit rather than clever:
    the caller should be able to count the configurations before running them,
    because the count is exactly the multiple-comparisons problem.
    """
    keys = list(overrides)
    for values in itertools.product(*(overrides[k] for k in keys)):
        yield dict(zip(keys, values))


def score(trades):
    """Judge a configuration on the shape of its returns, not its total.

    Total return rewards a single lucky trade. What matters for a rule that
    will keep running is whether the average trade is positive and whether that
    average is distinguishable from noise, which is what the t-statistic says.
    """
    rs = [t.get("r_multiple") if t.get("r_multiple") is not None else t.get("r")
          for t in trades]
    rs = [r for r in rs if r is not None]
    if not rs:
        return {"trades": len(trades), "mean_r": None, "t_stat": None,
                "total_r": 0.0, "win_rate_pct": None}
    n = len(rs)
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / n) if n > 1 else 0.0
    wins = [r for r in rs if r > 0]
    return {
        "trades": n,
        "mean_r": round(mean, 4),
        "total_r": round(sum(rs), 2),
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "sd_r": round(sd, 4),
        "t_stat": round(mean / (sd / math.sqrt(n)), 2) if sd and n > 1 else None,
    }


def _run(strategy_name, frames, config, overrides, benchmark=None):
    """Replay every bar of every instrument under one configuration.

    Uses the SAME engine the backtest uses. An earlier version of this called
    the strategy router once per instrument, which evaluates a single bar — the
    last one — so 400 instruments produced 400 evaluations of one date and
    almost no trades. A sweep has to replay history or it is not measuring the
    rule, it is sampling one afternoon.

    Only the swept strategy is left enabled, so its trades are its own rather
    than whatever survived contention against eleven others.
    """
    from . import engine
    from .cross_section_compat import build_cross_section

    merged = {**config}
    strategies = {}
    for name, spec in (config.get("strategies") or {}).items():
        strategies[name] = {**(spec or {}), "enabled": name == strategy_name}
    strategies.setdefault(strategy_name, {})
    strategies[strategy_name] = {**(strategies.get(strategy_name) or {}),
                                 "enabled": True, **overrides}
    merged["strategies"] = strategies

    cross = build_cross_section(frames)
    trades = []
    for ticker, frame in frames.items():
        try:
            result = engine.backtest_ticker(ticker, frame, merged,
                                            benchmark_closes=benchmark,
                                            unsized=True, cross_section=cross)
        except Exception:
            continue
        for trade in result.get("trades", []):
            if trade.get("strategy") and trade["strategy"] != strategy_name:
                continue
            trades.append(trade)
    return trades


def sweep(strategy_name, frames, config, overrides, split_at, benchmark=None,
          progress_cb=None):
    """Rank configurations on the first half, then test the winner on the second.

    Returns every configuration's in-sample score, the winner, and the winner's
    out-of-sample result — so the degradation between them is visible rather
    than something the reader has to reconstruct.
    """
    if not split_at:
        split_at = midpoint_split(frames)
        if not split_at:
            return {"error": "no usable history"}

    in_frames, out_frames = _split_frames(frames, split_at)
    if not in_frames or not out_frames:
        return {"error": f"not enough history either side of {split_at}"}

    # Both halves must clear the engine's warmup or the result is silence
    # masquerading as a finding.
    checks = [usable(in_frames, config, "in-sample"),
              usable(out_frames, config, "out-of-sample")]
    blocked = [c for c in checks if not c["ok"]]
    if blocked:
        return {"error": " ".join(c["note"] for c in blocked)
                + f" Split was {split_at}; try a longer paper.history_period "
                  f"or a split nearer the middle of the data."}

    configs = list(grid(overrides))
    results = []
    for index, combo in enumerate(configs, 1):
        trades = _run(strategy_name, in_frames, config, combo, benchmark)
        results.append({"config": combo, "in_sample": score(trades)})
        if progress_cb:
            progress_cb(index, len(configs), combo)

    ranked = [r for r in results if (r["in_sample"]["mean_r"] or 0) > 0]
    ranked.sort(key=lambda r: -(r["in_sample"]["t_stat"] or 0))

    out = {
        "strategy": strategy_name,
        "split_at": str(split_at),
        "configurations": len(configs),
        "instruments_in": len(in_frames),
        "instruments_out": len(out_frames),
        "results": sorted(results, key=lambda r: -(r["in_sample"]["mean_r"] or -99)),
        "winner": None,
        "out_of_sample": None,
        "verdict": None,
    }
    if not ranked:
        out["verdict"] = ("No configuration had a positive average trade even "
                          "in-sample. Nothing here is worth carrying forward.")
        return out

    winner = ranked[0]
    out["winner"] = winner
    oos = score(_run(strategy_name, out_frames, config, winner["config"], benchmark))
    out["out_of_sample"] = oos
    out["verdict"] = _verdict(winner["in_sample"], oos, len(configs))
    return out


def _verdict(in_sample, out_of_sample, configurations):
    """Say plainly whether the winner survived, and price in the search itself."""
    mean_in = in_sample.get("mean_r") or 0
    mean_out = out_of_sample.get("mean_r")
    if mean_out is None or not out_of_sample.get("trades"):
        return ("The best in-sample configuration produced no trades in the "
                "out-of-sample half. That is a failure, not a neutral result.")

    kept = (mean_out / mean_in * 100) if mean_in else 0
    t_out = out_of_sample.get("t_stat")
    note = (f"Searched {configurations} configurations, so the best in-sample "
            f"score is inflated by the search itself — with that many tries, "
            f"something always looks good.")

    if mean_out <= 0:
        return (f"FAILED. Best in-sample averaged {mean_in:+.3f}R and turned "
                f"{mean_out:+.3f}R out-of-sample. The setting fitted the first "
                f"half and did not generalise. {note}")
    if t_out is not None and t_out < 2:
        return (f"WEAK. Kept {kept:.0f}% of its in-sample edge "
                f"({mean_in:+.3f}R → {mean_out:+.3f}R) but t={t_out} out-of-sample, "
                f"so the average is not distinguishable from zero. {note}")
    return (f"SURVIVED. Kept {kept:.0f}% of its in-sample edge "
            f"({mean_in:+.3f}R → {mean_out:+.3f}R) with t={t_out} out-of-sample. "
            f"Worth testing forward on paper before trusting. {note}")
