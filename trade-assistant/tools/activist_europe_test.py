"""Score the activist-proxy test on the UK and European universe.

    python tools/activist_europe_test.py reports/FULL-STUDY-EUROPE-candidates.json

WRITTEN AND COMMITTED BEFORE THE RUN, and that is the point of it. The US study
tried four ideas and this one came out best, which is how false discoveries are
made. One test on data it has not seen, with the measure, the comparison and the
pass mark fixed in advance, is what tells a finding from a fluke. Changing
anything below after seeing the result would turn it back into a fifth idea.

THE TEST

  Rule       activist_13d_tracking exactly as shipped. No parameter changed.
  Universe   reports/universe-europe.csv (tools/build_europe_universe.py).
  Baseline   macro_sector_rotation on the same instruments: a long in anything
             while the STOXX Europe 600 is in an uptrend. It is what an
             unselective long with a stop earned, so a rule has to beat it to
             show it selects anything.
  Measure    gross R per trade, averaged within each calendar month, then the
             rule's month minus the baseline's month. Trades in one month move
             together, so the month is the unit of evidence, not the trade.
  Split      12 April 2022, the same date the US study split on.

THE PASS MARK

  PASS          the monthly difference is positive with t >= 2.0, AND positive
                in both halves, AND the average trade is positive after costs
  FAIL          the difference is zero or negative, OR t < 1.0, OR the average
                trade loses money after costs
  INCONCLUSIVE  anything else, or fewer than 200 signals

Costs are the engine's: commission both ways, and UK stamp duty of 0.5% on
purchases, which on a stop two ATRs away is about a tenth of the amount risked.
"""
import collections
import json
import math
import statistics
import sys

RULE = "activist_13d_tracking"
BASELINE = "macro_sector_rotation"
SPLIT = "2022-04-12"
MIN_SIGNALS = 200
PASS_T, FAIL_T = 2.0, 1.0
RISK_BUDGET = 10_000.0          # 1% of a 1,000,000 book: what one trade risks


def gross_r(trade):
    risk = abs(trade["entry_price"] - trade["stop"])
    return trade["gross_per_share"] / risk if risk > 0 else None


def net_r(trade):
    """After costs: the per-share duty against the per-share risk, and the two
    commissions against the cash risked on the trade."""
    risk = abs(trade["entry_price"] - trade["stop"])
    if risk <= 0:
        return None
    return ((trade["gross_per_share"] - trade.get("cost_rate", 0.0)) / risk
            - trade.get("cost_fixed", 0.0) / RISK_BUDGET)


def monthly(trades, value=gross_r):
    months = collections.defaultdict(list)
    for trade in trades:
        r = value(trade)
        if r is not None:
            months[trade["entry_date"][:7]].append(r)
    return {month: sum(v) / len(v) for month, v in months.items()}


def paired(rule, baseline):
    """(mean monthly difference, t, months) over months where both traded."""
    ours, theirs = monthly(rule), monthly(baseline)
    diffs = [ours[m] - theirs[m] for m in ours if m in theirs]
    if len(diffs) < 3:
        return None, None, len(diffs)
    spread = statistics.pstdev(diffs)
    mean = statistics.mean(diffs)
    return mean, (mean / (spread / math.sqrt(len(diffs))) if spread else float("nan")), len(diffs)


def mean_of(trades, value):
    values = [v for v in (value(t) for t in trades) if v is not None]
    return (sum(values) / len(values)) if values else None


def describe(label, rule, baseline):
    diff, t, months = paired(rule, baseline)
    wins = [t_ for t_ in rule if (gross_r(t_) or 0) > 0]
    line = (f"{label:<22} signals {len(rule):5d}  gross {mean_of(rule, gross_r) or 0:+.3f}R  "
            f"net {mean_of(rule, net_r) or 0:+.3f}R  wins {len(wins) / len(rule) * 100 if rule else 0:4.1f}%  "
            f"baseline {mean_of(baseline, gross_r) or 0:+.3f}R  ")
    line += (f"monthly difference {diff:+.3f}R  t {t:+.2f}  ({months} months)"
             if diff is not None else f"too few shared months ({months})")
    print(line)
    return diff, t


def main(path):
    with open(path) as handle:
        trades = json.load(handle)
    rule = [t for t in trades if t.get("strategy") == RULE]
    baseline = [t for t in trades if t.get("strategy") == BASELINE]

    diff, t = describe("whole period", rule, baseline)
    first, _ = describe("before " + SPLIT,
                        [x for x in rule if x["entry_date"] < SPLIT],
                        [x for x in baseline if x["entry_date"] < SPLIT])
    second, _ = describe("from " + SPLIT,
                         [x for x in rule if x["entry_date"] >= SPLIT],
                         [x for x in baseline if x["entry_date"] >= SPLIT])
    # Reported, not judged on: the pass mark is the whole universe.
    describe("UK only", [x for x in rule if x["ticker"].endswith(".L")],
             [x for x in baseline if x["ticker"].endswith(".L")])
    describe("continent only", [x for x in rule if not x["ticker"].endswith(".L")],
             [x for x in baseline if not x["ticker"].endswith(".L")])

    net = mean_of(rule, net_r)
    if len(rule) < MIN_SIGNALS or diff is None:
        verdict = "INCONCLUSIVE"
        why = f"{len(rule)} signals is fewer than the {MIN_SIGNALS} the test needs"
    elif diff <= 0 or t < FAIL_T or (net is not None and net <= 0):
        verdict = "FAIL"
        why = ("no better than buying anything in an uptrend" if diff <= 0 or t < FAIL_T
               else "the average trade loses money after costs")
    elif t >= PASS_T and (first or 0) > 0 and (second or 0) > 0:
        verdict = "PASS"
        why = "ahead of the baseline, in both halves, and positive after costs"
    else:
        verdict = "INCONCLUSIVE"
        why = "ahead of the baseline, but not by enough or not in both halves"
    print(f"\nVERDICT: {verdict} — {why}")
    return verdict


if __name__ == "__main__":
    main(sys.argv[1])
