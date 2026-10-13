"""Score every strategy country by country, on study runs that already exist.

    python tools/country_test.py

The plan is in reports/COUNTRY-TEST.md and was committed with this file before
any country result existed. Nothing is re-run and nothing is tuned: the trades
are the ones the US study (OCT02) and the European library study already
produced, regrouped by the country each share is listed in, and scored with
tools/region_test.py's verdict unchanged.

Each group is scored against its OWN baseline — macro_sector_rotation's trades
in the same group — so a country that simply rose more does not make every
strategy in it look clever.
"""
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import region_test  # noqa: E402

US_RUN = os.path.join(ROOT, "reports", "FULL-STUDY-OCT02-candidates.json")
EUROPE_RUN = os.path.join(ROOT, "reports", "FULL-STUDY-EUROPE-LIBRARY-candidates.json")
EUROPE_UNIVERSE = os.path.join(ROOT, "reports", "universe-europe.csv")
OUT = os.path.join(ROOT, "reports", "COUNTRY-TEST.json")

# Fixed before the run. Countries with fewer than about 45 listed companies are
# pooled with their neighbours: on their own they cannot reach 200 signals for
# most strategies, and a verdict on 20 companies is a verdict on 20 companies.
GROUPS = {
    "United Kingdom": "United Kingdom",
    "France": "France",
    "Germany": "Germany",
    "Switzerland": "Switzerland",
    "Sweden": "Nordics", "Finland": "Nordics", "Denmark": "Nordics", "Norway": "Nordics",
    "Italy": "Southern Europe", "Spain": "Southern Europe", "Portugal": "Southern Europe",
    "Netherlands": "Benelux and other", "Belgium": "Benelux and other",
    "Luxembourg": "Benelux and other", "Austria": "Benelux and other",
    "Ireland": "Benelux and other", "Jersey": "Benelux and other",
    "Guernsey": "Benelux and other", "Isle of Man": "Benelux and other",
}
ORDER = ["United States", "United Kingdom", "France", "Germany", "Switzerland",
         "Nordics", "Southern Europe", "Benelux and other"]


def europe_groups():
    with open(EUROPE_UNIVERSE) as handle:
        return {row["symbol"]: GROUPS.get(row["country"], "Benelux and other")
                for row in csv.DictReader(handle)}


def load(path):
    with open(path) as handle:
        return json.load(handle)


def grouped_trades():
    """{group: {strategy: [trades]}} across both runs."""
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for trade in load(US_RUN):
        out["United States"][trade.get("strategy")].append(trade)
    lookup = europe_groups()
    for trade in load(EUROPE_RUN):
        group = lookup.get(trade["ticker"])
        if group:
            out[group][trade.get("strategy")].append(trade)
    return out


def main():
    from assistant.strategies import registry

    names = [s.name for s in registry.all_strategies() if s.name != region_test.BASELINE]
    data = grouped_trades()
    results = {}
    for group in ORDER:
        by_strategy = data.get(group, {})
        baseline = by_strategy.get(region_test.BASELINE, [])
        results[group] = {"baseline_signals": len(baseline), "strategies": {}}
        print(f"\n== {group} — baseline {len(baseline)} signals, "
              f"{region_test.mean_of(baseline, region_test.gross_r) or 0:+.3f}R per trade")
        for name in names:
            label, why, facts = region_test.verdict(by_strategy.get(name, []), baseline)
            results[group]["strategies"][name] = {"verdict": label, "why": why, **facts}
            if facts:
                print(f"  {name:24s} {label:13s} {facts['signals']:6d}  lead "
                      f"{facts['lead'] if facts['lead'] is not None else float('nan'):+.3f}R  "
                      f"t {facts['t'] if facts['t'] is not None else float('nan'):+.1f}  "
                      f"net {facts['net'] if facts['net'] is not None else float('nan'):+.3f}R")
            else:
                print(f"  {name:24s} {label:13s}")
    with open(OUT, "w") as handle:
        json.dump(results, handle, indent=1, default=str)
    print(f"\nwritten {os.path.relpath(OUT, ROOT)}")
    return results


if __name__ == "__main__":
    main()
