"""Score every strategy in a study run against the unselective baseline.

    python tools/region_test.py reports/FULL-STUDY-EUROPE-LIBRARY-candidates.json

The generalisation of tools/activist_europe_test.py to the whole library, with
the same measure and the same pass mark, written and committed before the
European library run so that nothing below can be adjusted to suit its result.

THE MEASURE

  Baseline   macro_sector_rotation in the same run: a long in anything while the
             region's index is in an uptrend. What an unselective long with a
             stop earned. A strategy has to beat it to show it selects anything.
  Per trade  gross result as a multiple of the amount risked (R); and after
             costs, with UK stamp duty and commission charged as the engine does.
  Per month  the strategy's average trade in a calendar month minus the
             baseline's in the same month. Trades in one month move together, so
             the month is the unit of evidence, not the trade.
  Split      12 April 2022.

THE PASS MARK, PER STRATEGY

  PASS          ahead of the baseline with t >= 2.0, ahead in both halves, and
                the average trade positive after costs
  FAIL          not ahead of the baseline, OR t < 1.0, OR the average trade
                loses money after costs
  INCONCLUSIVE  anything else, or fewer than 200 signals
  UNTESTABLE    no signals at all: the rule cannot act on this universe

Testing many strategies at once means some will clear t = 2.0 by luck. At this
bar about one in forty would, so across fourteen the expected number of lucky
passes is about a third of one. A PASS is still a reason to look harder, not a
reason to trade.
"""
import collections
import math
import statistics
import sys

import json

BASELINE = "macro_sector_rotation"
SPLIT = "2022-04-12"
MIN_SIGNALS = 200
PASS_T, FAIL_T = 2.0, 1.0
RISK_BUDGET = 10_000.0          # 1% of a 1,000,000 book: what one trade risks


def gross_r(trade):
    risk = abs(trade["entry_price"] - trade["stop"])
    return trade["gross_per_share"] / risk if risk > 0 else None


def net_r(trade):
    risk = abs(trade["entry_price"] - trade["stop"])
    if risk <= 0:
        return None
    return ((trade["gross_per_share"] - trade.get("cost_rate", 0.0)) / risk
            - trade.get("cost_fixed", 0.0) / RISK_BUDGET)


def mean_of(trades, value):
    values = [v for v in (value(t) for t in trades) if v is not None]
    return (sum(values) / len(values)) if values else None


def monthly(trades):
    months = collections.defaultdict(list)
    for trade in trades:
        r = gross_r(trade)
        if r is not None:
            months[trade["entry_date"][:7]].append(r)
    return {month: sum(v) / len(v) for month, v in months.items()}


def paired(rule, baseline):
    """(mean monthly lead over the baseline, t, months compared)."""
    ours, theirs = monthly(rule), monthly(baseline)
    diffs = [ours[m] - theirs[m] for m in ours if m in theirs]
    if len(diffs) < 3:
        return None, None, len(diffs)
    spread = statistics.pstdev(diffs)
    mean = statistics.mean(diffs)
    return mean, (mean / (spread / math.sqrt(len(diffs))) if spread else float("nan")), len(diffs)


def verdict(rule, baseline):
    if not rule:
        return "UNTESTABLE", "no signals on this universe", {}
    lead, t, months = paired(rule, baseline)
    first, _, _ = paired([x for x in rule if x["entry_date"] < SPLIT],
                         [x for x in baseline if x["entry_date"] < SPLIT])
    second, _, _ = paired([x for x in rule if x["entry_date"] >= SPLIT],
                          [x for x in baseline if x["entry_date"] >= SPLIT])
    net = mean_of(rule, net_r)
    facts = {"signals": len(rule), "gross": mean_of(rule, gross_r), "net": net,
             "lead": lead, "t": t, "months": months, "first": first, "second": second}
    if len(rule) < MIN_SIGNALS or lead is None:
        return "INCONCLUSIVE", f"{len(rule)} signals, fewer than {MIN_SIGNALS}", facts
    if lead <= 0 or t < FAIL_T:
        return "FAIL", "no better than buying anything in an uptrend", facts
    if net is not None and net <= 0:
        return "FAIL", "the average trade loses money after costs", facts
    if t >= PASS_T and (first or 0) > 0 and (second or 0) > 0:
        return "PASS", "ahead of the baseline, in both halves, positive after costs", facts
    return "INCONCLUSIVE", "ahead of the baseline, but not by enough or not in both halves", facts


def main(path):
    with open(path) as handle:
        trades = json.load(handle)
    by_strategy = collections.defaultdict(list)
    for trade in trades:
        by_strategy[trade.get("strategy")].append(trade)
    baseline = by_strategy.get(BASELINE, [])
    print(f"baseline {BASELINE}: {len(baseline)} signals, "
          f"{mean_of(baseline, gross_r) or 0:+.3f}R gross per trade\n")

    from assistant.strategies import registry
    names = [s.name for s in registry.all_strategies() if s.name != BASELINE]
    print(f"{'strategy':24s} {'verdict':13s} {'signals':>7} {'gross':>7} {'net':>7} "
          f"{'lead':>7} {'t':>6} {'1st half':>8} {'2nd half':>8}  UK lead (t) / rest lead (t)")
    results = {}
    for name in names:
        rule = by_strategy.get(name, [])
        label, why, f = verdict(rule, baseline)
        results[name] = {"verdict": label, "why": why, **f}
        if not f:
            print(f"{name:24s} {label:13s} {0:7d}  {why}")
            continue
        uk = paired([x for x in rule if x["ticker"].endswith(".L")],
                    [x for x in baseline if x["ticker"].endswith(".L")])
        eu = paired([x for x in rule if not x["ticker"].endswith(".L")],
                    [x for x in baseline if not x["ticker"].endswith(".L")])
        fmt = lambda v: "   —   " if v is None else f"{v:+7.3f}"
        tf = lambda v: "  —  " if v is None else f"{v:+5.1f}"
        print(f"{name:24s} {label:13s} {f['signals']:7d} {fmt(f['gross'])} {fmt(f['net'])} "
              f"{fmt(f['lead'])} {tf(f['t']):>6} {fmt(f['first']):>8} {fmt(f['second']):>8}  "
              f"{fmt(uk[0])} ({tf(uk[1])}) / {fmt(eu[0])} ({tf(eu[1])})")
    return results


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main(sys.argv[1])
