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
import csv
import json
import os
import pickle
import sys
import tempfile
import time
from multiprocessing import get_context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant import markets, pipeline                          # noqa: E402
from assistant.backtest import blend, engine, portfolio, runner  # noqa: E402
from assistant.core.config import load_config                    # noqa: E402
from assistant.paper import screen                               # noqa: E402
from assistant.providers import bulk                             # noqa: E402
from assistant.strategies import registry                        # noqa: E402

PERIOD = "10y"
EQUITY_SLOTS = int(os.environ.get("STUDY_EQUITIES", "200"))
# "mixed"  — equities with FX and futures added alongside
# "macro"  — FX, futures and cash macro ETFs ONLY, competing against each other
MODE = os.environ.get("STUDY_MODE", "mixed")
# STUDY_TAG names a run that must not replace the published one. Everything it
# writes carries the tag — including KELLY-EDGES and the correlation files,
# which the live book sizes from and which a side study has no business
# rewriting.
TAG = os.environ.get("STUDY_TAG", "")
_TAG_SUFFIX = f"-{TAG}" if TAG else ""
_SUFFIX = ("" if MODE == "mixed" else f"-{MODE.upper()}") + _TAG_SUFFIX
# A CSV with a `symbol` and an `asset_class` column: replay exactly these
# instruments instead of re-screening. Re-screening months later returns a
# different universe, and two studies on different universes cannot be compared.
UNIVERSE_FILE = os.environ.get("STUDY_UNIVERSE")
# Strategies to switch on for this run only, whatever config.yaml says. This is
# how a rule that ships disabled gets measured without touching the live book.
FORCE_ENABLE = [n for n in os.environ.get("STUDY_ENABLE", "").split(",") if n]
# The replay is the expensive step and each instrument is independent, so it
# splits across processes. 1 keeps the original single-process path.
WORKERS = int(os.environ.get("STUDY_WORKERS", "1"))
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "reports", f"FULL-STUDY{_SUFFIX}.json")
CANDIDATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "reports", f"FULL-STUDY{_SUFFIX}-candidates.json")


def _report(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", name)


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
    """(bulk-fetched symbols, individually-fetched symbols) for this mode.

    In macro mode there are no single stocks at all. That is the point: pooling
    fifty macro instruments with five hundred equities does not test them, it
    excludes them — a ranking whose twelfth-best name is up 183% can never
    select a currency pair that moved 8%.
    """
    if UNIVERSE_FILE:
        with open(UNIVERSE_FILE) as handle:
            rows = list(csv.DictReader(handle))
        return ([r["symbol"] for r in rows if r["asset_class"] == "equity"],
                [r["symbol"] for r in rows if r["asset_class"] != "equity"])
    if MODE == "macro":
        return [], markets.macro_universe()
    equities, _, _ = screen.tradable_universe(config, router)
    chosen = list(equities[:EQUITY_SLOTS])
    for sector in markets.SECTOR_ETFS:
        if sector not in chosen:
            chosen.append(sector)
    return chosen, markets.symbols()


_WORKER = {}


def _worker_init(snapshot_path):
    """Load the price snapshot once per process and build the cross-section.

    A fresh process rather than a fork: forking after the network and numeric
    libraries have started is unreliable on macOS, and a worker that dies
    mid-replay looks like an instrument with no trades.
    """
    from assistant.strategies.cross_section import CrossSection
    with open(snapshot_path, "rb") as handle:
        _WORKER.update(pickle.load(handle))
    _WORKER["cross_section"] = CrossSection.from_frames(_WORKER["frames"])


def _worker_replay(ticker):
    outcome = engine.backtest_ticker(
        ticker, _WORKER["frames"][ticker], _WORKER["config"], _WORKER["benchmark"],
        None, unsized=True, cross_section=_WORKER["cross_section"])
    notes = []
    base = _WORKER["config"].get("base_currency", "USD")
    engine.convert_trades(outcome["trades"], base, base, None, notes)
    return outcome


def replay(frames, study_cfg, benchmark, progress):
    """Every instrument, bar by bar. Same result with one process or many: each
    instrument is replayed independently against the same shared cross-section,
    which is exactly what engine.run_backtest does in sequence."""
    if WORKERS <= 1:
        return engine.run_backtest(list(frames), study_cfg, lambda t: frames[t],
                                   benchmark_fn=lambda: benchmark,
                                   progress_cb=progress, unsized=True)["trades"]
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as handle:
        pickle.dump({"frames": frames, "config": study_cfg, "benchmark": benchmark},
                    handle)
        snapshot = handle.name
    trades, done = [], 0
    try:
        with get_context("spawn").Pool(WORKERS, _worker_init, (snapshot,)) as pool:
            # Longest series first, so no process is left holding the slow
            # instruments at the end while the others sit idle.
            order = sorted(frames, key=lambda t: -len(frames[t]))
            for outcome in pool.imap_unordered(_worker_replay, order):
                trades.extend(outcome["trades"])
                done += 1
                if done % 25 == 0 or done == len(order):
                    log(f"  {done}/{len(order)} instruments replayed, "
                        f"{len(trades)} candidates")
    finally:
        os.unlink(snapshot)
    # imap_unordered returns in completion order; the portfolio simulation
    # must not depend on which process happened to finish first.
    trades.sort(key=lambda t: (t.get("entry_date") or "", t.get("ticker") or "",
                               t.get("strategy") or ""))
    return trades


def main():
    config = load_config()
    if FORCE_ENABLE:
        known = {s.name for s in registry.all_strategies()}
        unknown = [n for n in FORCE_ENABLE if n not in known]
        if unknown:
            raise SystemExit(f"STUDY_ENABLE names unknown strategies: {unknown}")
        blocks = dict(config.get("strategies") or {})
        for name in FORCE_ENABLE:
            blocks[name] = {**(blocks.get(name) or {}), "enabled": True}
        config = {**config, "strategies": blocks}
        log(f"switched on for this run only: {', '.join(FORCE_ENABLE)}")
    # USD throughout: US equities, FX quoted against the dollar and dollar-
    # denominated futures. Converting into GBP would fold a currency return into
    # every strategy's result and make them incomparable.
    config = {**config, "base_currency": "USD",
              "scanner": {**config["scanner"], "benchmark": "^GSPC"}}
    router = pipeline.get_router(config)

    equities, derivatives = build_universe(config, router)
    log(f"universe: {len(equities)} equities + {len(derivatives)} FX/futures")

    frames = {}
    if equities:
        log("fetching equity history…")
        frames = bulk.ohlcv_history(equities, period=PERIOD)
        log(f"  {len(frames)} equity series")

    log(f"fetching {len(derivatives)} FX / futures / macro-ETF series…")
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

    def progress(ticker, bars, total):
        if bars % 2000 == 0:
            log(f"  {ticker}: {bars}/{total} bars")

    out = {"trades": replay(frames, study_cfg, benchmark, progress)}
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
    streams = {}          # {strategy: (dates, periodic returns)} for correlation

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
        if sim.get("dates") and len(sim["equity_curve"]) > 3:
            streams[label] = (sim["dates"][1:], blend.periodic_returns(sim["equity_curve"]))
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

    # --- Correlation, blend, and the out-of-sample edges Kelly sizes from ----
    per_strategy = {k: v for k, v in streams.items()
                    if not k.startswith("ALL_") and not k.startswith("BUY_")}
    dates, aligned = blend.align_streams(per_strategy)
    log(f"correlating {len(aligned)} strategies over {len(dates)} shared periods")

    correlation, chosen, rejected = {}, [], {}
    if aligned:
        correlation = blend.correlation_matrix(aligned)
        blend.write_matrix_csv(correlation, _report(f"STRATEGY-CORRELATION{_TAG_SUFFIX}.csv"))
        blend.write_heatmap_svg(correlation, _report(f"STRATEGY-CORRELATION{_TAG_SUFFIX}.svg"))

        # Edges measured on the OUT-OF-SAMPLE half only. Kelly refuses an
        # in-sample estimate, and this is where that refusal is honoured: the
        # blend and the sizing both see only the half the rules were not
        # selected against.
        oos = {}
        for name, series in aligned.items():
            oos_returns = [r for d, r in zip(dates, series)
                           if split_date and d > split_date]
            if len(oos_returns) >= 3:
                oos[name] = oos_returns
        oos_means = {n: sum(v) / len(v) for n, v in oos.items()}
        chosen, rejected = blend.choose_blend(
            oos_means, oos,
            max_correlation=float((config.get("blend") or {}).get("max_correlation", 0.6)),
            max_strategies=int((config.get("blend") or {}).get("max_strategies", 4)))
        log(f"blend chosen: {chosen}")
        for name, why in rejected.items():
            log(f"  rejected {name}: {why}")

        with open(_report(f"KELLY-EDGES{_TAG_SUFFIX}.json"), "w") as handle:
            json.dump({"split_date": split_date,
                       "out_of_sample_returns": oos,
                       "means": oos_means,
                       "blend": chosen}, handle, indent=2)

        # The blend itself, simulated as a book.
        if chosen:
            simulate("BLEND", [t for t in out["trades"]
                               if t.get("strategy") in set(chosen)])

    payload = {
        "correlation": correlation,
        "blend": {"chosen": chosen, "rejected": rejected},
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
