"""Rank the strategies by their measured edge, for strategy_priority.

    python tools/rank_strategies.py reports/FULL-STUDY-EUROPE-LIBRARY-candidates.json \\
        --source "UK and Europe, 811 shares, run 4 October 2026"

Edge is what tools/region_test.py measures: a strategy's average trade in each
calendar month minus the unselective baseline's in the same month, averaged
over the months. It scores every signal on its own, so neither how often a rule
fires nor how much cash a book built from it holds changes the number — the
two things that made the old Calmar ordering put the rule that fires on
everything near the top.

ONLY EVER RUN THIS ON DATA THE STRATEGIES WERE NOT CHOSEN ON. Every rule here
was picked and tuned on one US decade, so a US ranking would grade them on the
test they were written to pass. The first ranking uses the European run.

Writes reports/STRATEGY-EDGES.json and prints the list to paste into
config.yaml. config.yaml is edited by hand, never by a program, which is what
keeps its comments; this script does not touch it.

Re-rank every six months on data newer than the last ranking. The paper
session says when the file is older than that.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_test                                  # noqa: E402
from assistant.strategies import registry           # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports",
                   "STRATEGY-EDGES.json")


def rank(trades):
    """[(name, facts)] best edge first; strategies with no signals last."""
    by_strategy = {}
    for trade in trades:
        by_strategy.setdefault(trade.get("strategy"), []).append(trade)
    baseline = by_strategy.get(region_test.BASELINE, [])
    measured, unmeasured = [], []
    for strategy in registry.all_strategies():
        if strategy.name == region_test.BASELINE:
            continue
        rule = by_strategy.get(strategy.name, [])
        lead, t, months = region_test.paired(rule, baseline) if rule else (None, None, 0)
        facts = {"edge_r": None if lead is None else round(lead, 4),
                 "t": None if t is None else round(t, 2),
                 "months": months, "signals": len(rule)}
        (measured if lead is not None else unmeasured).append((strategy.name, facts))
    measured.sort(key=lambda pair: -pair[1]["edge_r"])
    return measured + unmeasured


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates")
    parser.add_argument("--source", required=True,
                        help="what data this is, in words; recorded in the file")
    args = parser.parse_args()
    with open(args.candidates) as handle:
        ranked = rank(json.load(handle))

    payload = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": args.source,
        "candidates_file": os.path.basename(args.candidates),
        "measure": "mean monthly lead over macro_sector_rotation, gross R per trade",
        "order": [name for name, _ in ranked],
        "edges": dict(ranked),
    }
    with open(OUT, "w") as handle:
        json.dump(payload, handle, indent=2)

    print(f"{'strategy':24s} {'edge':>8} {'t':>6} {'signals':>8}")
    for name, f in ranked:
        edge = "  —" if f["edge_r"] is None else f"{f['edge_r']:+.3f}R"
        t = "  —" if f["t"] is None else f"{f['t']:+.1f}"
        print(f"{name:24s} {edge:>8} {t:>6} {f['signals']:8d}")
    print(f"\nwritten to {os.path.normpath(OUT)}\n\nstrategy_priority:")
    for name, _ in ranked:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
