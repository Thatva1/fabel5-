"""Every strategy, every asset class, measured against holding the same thing.

Design note, because the obvious approach is unaffordable. Replaying N
instruments once per strategy costs N x S; this replays ONCE with every strategy
enabled and its own position slot, then attributes the resulting candidates by
strategy and runs a cheap portfolio simulation per subset. Each strategy gets an
independent book because per_strategy slots stop them competing for the same
instrument during the replay — so a strategy's numbers are its own, not a
by-product of losing a contention fight to a more frequent signal.

Every strategy is measured against the SAME buy-and-hold of the SAME universe,
unlevered, so "did the timing add anything" is separable from "was this a good
set of instruments to own".
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant import markets, pipeline                          # noqa: E402
from assistant.backtest import engine, portfolio, runner         # noqa: E402
from assistant.core.config import load_config                    # noqa: E402
from assistant.paper import screen                               # noqa: E402
from assistant.providers import bulk                             # noqa: E402
from assistant.strategies import registry                        # noqa: E402

PERIOD = "10y"
EQUITY_SLOTS = int(os.environ.get("STUDY_EQUITIES", "200"))
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "reports", "FULL-STUDY.json")
CANDIDATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "reports", "FULL-STUDY-candidates.json")


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _thin(section):
    """The handful of numbers worth comparing across an in/out sample split."""
    if not isinstance(section, dict):
        return section
    metrics = section.get("metrics", section)
    keep = ("cagr_pct", "max_drawdown_pct", "sharpe", "sortino", "calmar",
            "total_return_pct", "ulcer_index", "gain_to_pain")
    out = {k: metrics.get(k) for k in keep if k in metrics}
    if "trades" in section:
        out["trades"] = (len(section["trades"]) if isinstance(section["trades"], list)
                         else section["trades"])
    return out


def build_universe(config, router):
    """Liquid equities + every sector ETF + all FX + all futures."""
    equities, _, _ = screen.tradable_universe(config, router)
    chosen = list(equities[:EQUITY_SLOTS])
    for sector in markets.SECTOR_ETFS:
        if sector not in chosen:
            chosen.append(sector)
    return chosen, markets.symbols()


def main():
    config = load_config()
    # USD throughout: US equities, FX quoted against the dollar and dollar-
    # denominated futures. Converting into GBP would fold a currency return into
    # every strategy's result and make them incomparable.
    config = {**config, "base_currency": "USD",
              "scanner": {**config["scanner"], "benchmark": "^GSPC"}}
    router = pipeline.get_router(config)

    equities, derivatives = build_universe(config, router)
    log(f"universe: {len(equities)} equities + {len(derivatives)} FX/futures")

    log("fetching equity history…")
    frames = bulk.ohlcv_history(equities, period=PERIOD)
    log(f"  {len(frames)} equity series")

    log("fetching FX and futures history…")
    for symbol in derivatives:
        try:
            df = router.get_prices(symbol, period=PERIOD)
            if df is not None and len(df) > 500:
                frames[symbol] = df
        except Exception as exc:
            log(f"  {symbol}: unavailable ({type(exc).__name__})")
    log(f"  {len(frames)} total instruments")

    warning = markets.leverage_warning(frames)
    if warning:
        log(f"NOTE: {warning}")

    benchmark = router.get_prices("^GSPC", period=PERIOD)["Close"]
    calendar = sorted({str(d)[:10] for df in frames.values() for d in df.index})

    def price_lookup(ticker, date):
        df = frames.get(ticker)
        window = df.loc[:date] if df is not None else None
        return float(window["Close"].iloc[-1]) if window is not None and len(window) else None

    # ONE replay. per_strategy slots so each strategy's trades are its own.
    study_cfg = {**config,
                 "backtest": {**config["backtest"], "position_slots": "per_strategy"}}
    log("replaying — this is the expensive step…")
    started = time.time()
    done = {"n": 0}

    def progress(ticker, bars, total):
        if bars % 2000 == 0:
            log(f"  {ticker}: {bars}/{total} bars")

    out = engine.run_backtest(list(frames), study_cfg, lambda t: frames[t],
                              benchmark_fn=lambda: benchmark, progress_cb=progress,
                              unsized=True)
    log(f"replay done in {(time.time() - started) / 60:.1f} min — "
        f"{len(out['trades'])} candidates")

    # The candidates are the expensive artefact — 100 minutes of replay — and
    # every question asked afterwards is seconds of work on top of them. The
    # first version of this script threw them away and kept only the summary,
    # so a single wrong dictionary key in the reporting cost the entire replay
    # to discover and the entire replay again to fix. Saved first, analysed
    # second.
    os.makedirs(os.path.dirname(CANDIDATES_PATH), exist_ok=True)
    with open(CANDIDATES_PATH, "w") as handle:
        json.dump(out["trades"], handle, default=str)
    log(f"candidates saved to {os.path.basename(CANDIDATES_PATH)} "
        f"({os.path.getsize(CANDIDATES_PATH) / 1e6:.0f} MB) — "
        "re-analysis no longer needs a replay")

    results = {}

    def simulate(label, candidates):
        if not candidates:
            results[label] = {"trades": 0, "note": "no candidates"}
            return
        sim = portfolio.simulate(candidates, config, calendar=calendar,
                                 price_lookup=price_lookup)
        wins = [t for t in sim["trades"] if (t.get("pnl_base") or 0) > 0]
        results[label] = {
            **sim["metrics"],
            "trades": len(sim["trades"]),
            "win_rate_pct": (round(len(wins) / len(sim["trades"]) * 100, 2)
                             if sim["trades"] else None),
            "final_equity": sim["final_equity"],
            "marked_to_market": sim["marked_to_market"],
            "skipped": sim["skipped"],
        }
        log(f"  {label:<18} CAGR {results[label].get('cagr_pct')}%  "
            f"DD {results[label].get('max_drawdown_pct')}%  "
            f"Sharpe {results[label].get('sharpe')}  trades {len(sim['trades'])}")

    # The split date, for the check that has never once been run in this
    # project. Everything measured so far is in-sample: the rules were chosen
    # knowing how the decade went. A strategy that holds up on the half of
    # history it was not selected against is worth something; one that only
    # works on the whole sample is a description of the past.
    dates = sorted({t["entry_date"] for t in out["trades"] if t.get("entry_date")})
    split_date = dates[len(dates) // 2] if dates else None
    log(f"out-of-sample split at {split_date}")

    log("simulating each strategy on its own…")
    for strategy in registry.all_strategies():
        subset = [t for t in out["trades"] if t.get("strategy") == strategy.name]
        simulate(strategy.name, subset)
        if split_date and subset:
            try:
                split = runner.validate_out_of_sample(
                    subset, config, split_date, calendar=calendar,
                    price_lookup=price_lookup)
                # develop / validate, not in_sample / out_sample. Reading the
                # wrong keys silently produced a table of None next to a column
                # of confident verdicts — the analysis had run correctly and
                # only the reporting was broken, which is the harder version to
                # notice.
                results[strategy.name]["out_of_sample"] = {
                    "split_date": split.get("split_date"),
                    "develop": _thin(split.get("develop")),
                    "validate": _thin(split.get("validate")),
                    "verdict": split.get("verdict"),
                    "note": split.get("note"),
                }
            except Exception as exc:
                results[strategy.name]["out_of_sample"] = {
                    "error": f"{type(exc).__name__}: {exc}"}

    log("simulating the whole library together…")
    simulate("ALL_COMBINED", out["trades"])

    log("buy and hold of the same universe…")
    bh = runner.buy_and_hold(frames, config, calendar=calendar)
    results["BUY_AND_HOLD"] = {**bh, "trades": len(frames)}

    # Equities only, so the multi-asset contribution is separable.
    eq_only = {t: df for t, df in frames.items()
               if not markets.is_fx(t) and not markets.is_future(t)}
    results["BUY_AND_HOLD_EQUITY_ONLY"] = {
        **runner.buy_and_hold(eq_only, config, calendar=calendar),
        "trades": len(eq_only)}

    payload = {
        "period": PERIOD,
        "instruments": len(frames),
        "equities": len([t for t in frames if not markets.is_fx(t) and not markets.is_future(t)]),
        "fx": len([t for t in frames if markets.is_fx(t)]),
        "futures": len([t for t in frames if markets.is_future(t)]),
        "leverage_warning": warning,
        "candidates": len(out["trades"]),
        "results": results,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as handle:
        json.dump(payload, handle, indent=2, default=str)
    log(f"written to {os.path.normpath(OUT_PATH)}")


if __name__ == "__main__":
    main()
