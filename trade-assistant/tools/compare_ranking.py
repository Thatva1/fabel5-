"""Backtest the composite scorer against the edge-ranked list, side by side.

    python tools/compare_ranking.py reports/FULL-STUDY-OCT04-candidates.json

Four ways of turning the same candidates into one book, all under the live
book's limits (1% positions, up to 120, 90% invested, 25% per strategy, one
position per instrument, no per-market cap — the live session applies none):

  before    strategy_priority as it stood on 2 October (ts_momentum last),
            ties broken by reward:risk and then list order
  ranked    strategy_priority by measured edge per trade (4 October), ties
            broken by signal strength
  composite the composite scorer, weighted by the SAME out-of-sample edges and
            correlations the ranked list was built from (the European run)
  composite as shipped
            the composite scorer reading the files it would read if switched on
            today: reports/KELLY-EDGES.json and STRATEGY-CORRELATION.csv, both
            from the August US study

Scored on the US candidates. The edges came from Europe, so nothing being
compared was fitted to the data it is scored on. Two entry patterns: every day
the backtest produces a signal, and only on the first trading day of each
month, which is closer to how the paper book enters.
"""
import json
import os
import pickle
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from assistant.backtest import portfolio, runner      # noqa: E402
from assistant.core.config import load_config         # noqa: E402
from assistant.strategies import composite            # noqa: E402

SPLIT = "2022-04-12"
BEFORE_PRIORITY = ["long_reversal", "dual_momentum", "low_beta", "turn_of_month",
                   "short_reversal", "sector_momentum", "high_52w", "low_volatility",
                   "xs_momentum", "band_reversion", "halloween", "ts_momentum"]


def frames_for(config):
    """Daily history for the study universe, every series from Yahoo, cached."""
    cache = os.path.join(tempfile.gettempdir(), "compare_ranking_frames.pkl")
    if os.path.exists(cache):
        with open(cache, "rb") as handle:
            return pickle.load(handle)
    os.environ.setdefault("STUDY_UNIVERSE", os.path.join(ROOT, "reports", "share", "universe.csv"))
    import full_study
    from assistant import pipeline
    from assistant.providers import bulk
    providers = dict(config.get("providers") or {})
    providers["ibkr"] = {**(providers.get("ibkr") or {}), "enabled": False}
    router = pipeline.get_router({**config, "providers": providers})
    equities, derivatives = full_study.build_universe(config, router)
    frames = bulk.ohlcv_history(equities, period="10y")
    for symbol in derivatives:
        try:
            df = router.get_prices(symbol, period="10y")
            if df is not None and len(df) > 500:
                frames[symbol] = df
        except Exception:
            pass
    with open(cache, "wb") as handle:
        pickle.dump(frames, handle)
    return frames


def main(path):
    config = load_config()
    from assistant.strategies import registry
    live = {s.name for s in registry.enabled_strategies(config)}

    with open(path) as handle:
        candidates = [c for c in json.load(handle) if c.get("strategy") in live]
    first_of_month = {}
    for date in sorted({c["signal_date"] for c in candidates}):
        first_of_month.setdefault(date[:7], date)
    monthly = [c for c in candidates if c["signal_date"] in set(first_of_month.values())]

    frames = frames_for(config)
    calendar = sorted({str(d)[:10] for df in frames.values() for d in df.index})
    closes = {}
    for ticker, df in frames.items():
        series = df["Close"].copy()
        series.index = [str(d)[:10] for d in series.index]
        closes[ticker] = series

    def price_lookup(ticker, date):
        series = closes.get(ticker)
        if series is None:
            return None
        window = series.loc[:date]
        return float(window.iloc[-1]) if len(window) else None

    with open(os.path.join(ROOT, "reports", "STRATEGY-EDGES.json")) as handle:
        europe = json.load(handle)
    europe_edges = {n: f["edge_r"] for n, f in europe["edges"].items() if f["edge_r"]}
    europe_matrix = composite.load_correlations(
        os.path.join(ROOT, "reports", "STRATEGY-CORRELATION-EUROPE-LIBRARY.csv"))
    shipped = (composite.load_edges()[0], composite.load_correlations())

    live_book = {**config["portfolio"], "one_position_per_ticker": True,
                 "max_strategy_share_pct": 25, "max_positions_per_market": 120}
    variants = [
        ("before (2 Oct)", {"strategy_priority": BEFORE_PRIORITY},
         {"tie_break": "arrival"}, None),
        ("ranked (edge + strength)", {}, {"tie_break": "strength"}, None),
        ("composite (Europe edges)", {}, {"ranking": "composite"},
         (europe_edges, europe_matrix)),
        ("composite as shipped", {}, {"ranking": "composite"}, shipped),
    ]

    results = []
    for entries, pool in (("every signal day", candidates), ("monthly entry", monthly)):
        for period, subset in (("whole", pool),
                               ("first half", [c for c in pool if c["entry_date"] < SPLIT]),
                               ("second half", [c for c in pool if c["entry_date"] >= SPLIT])):
            print(f"\n=== {entries}, {period} ===", flush=True)
            for label, top, knobs, inputs in variants:
                cfg = {**config, **top, "portfolio": {**live_book, **knobs}}
                sim = portfolio.simulate(subset, cfg, calendar=calendar,
                                         price_lookup=price_lookup, composite_inputs=inputs)
                m = sim["metrics"]
                held = {}
                for trade in sim["trades"]:
                    held[trade["strategy"]] = held.get(trade["strategy"], 0) + \
                        trade["value_base"] * max(1, trade.get("bars_held") or 1)
                total = sum(held.values()) or 1
                mix = sorted(((k, round(v / total * 100)) for k, v in held.items()),
                             key=lambda kv: -kv[1])
                row = {"entries": entries, "period": period, "variant": label,
                       **{k: m.get(k) for k in ("cagr_pct", "max_drawdown_pct", "sharpe",
                                                "calmar", "volatility_pct")},
                       "trades": len(sim["trades"]), "mix": mix}
                results.append(row)
                print(f"  {label:<27} CAGR {m.get('cagr_pct'):>6}%  DD {m.get('max_drawdown_pct'):>6}%  "
                      f"Sharpe {m.get('sharpe'):>5}  Calmar {m.get('calmar'):>5}  trades {len(sim['trades']):>5}  "
                      f"largest {mix[0][0] if mix else '-'} {mix[0][1] if mix else 0}%", flush=True)

    bh = runner.buy_and_hold(frames, config, calendar=calendar)
    print(f"\nbuy and hold: CAGR {bh.get('cagr_pct')}%  DD {bh.get('max_drawdown_pct')}%  "
          f"Sharpe {bh.get('sharpe')}")
    out = os.path.join(ROOT, "reports", "RANKING-COMPARISON.json")
    with open(out, "w") as handle:
        json.dump({"candidates_file": os.path.basename(path), "split": SPLIT,
                   "portfolio": live_book, "results": results,
                   "buy_and_hold": {k: bh.get(k) for k in ("cagr_pct", "max_drawdown_pct", "sharpe")}},
                  handle, indent=2, default=str)
    print(f"written to {os.path.normpath(out)}")


if __name__ == "__main__":
    main(sys.argv[1])
