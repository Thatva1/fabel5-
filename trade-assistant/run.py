#!/usr/bin/env python3
"""Trade Assistant CLI (Layer 1 — research only).

  python run.py serve [port]     dashboard (default port from config.yaml: 5002)
  python run.py scan             scan watchlist, full pipeline on flagged tickers
  python run.py analyze TICKER   force the full pipeline on one ticker/name
  python run.py backtest [TICKERS...]   replay the strategies over history
                                 (defaults to your watchlist; --years N,
                                  --csv FILE to write every trade to a spreadsheet)
  python run.py paper            one paper-trading session (marks the book,
                                 takes exits, rebalances if a month has turned;
                                 --status to just read it, --rebalance to force,
                                 --refresh-universe to re-screen,
                                 --reset to archive the book and start fresh)
  python run.py coverage         measure what the LICENSED feed can actually
                                 price, and name the subscription that would
                                 unlock the rest (--universe for the full screen)
  python run.py intraday         does an edge exist on 5/15/30/60-minute bars?
                                 (--bars 15m, --cost-bps 10, then tickers)
  python run.py sweep --all      sweep EVERY strategy's lookback on one split
  python run.py sweep STRATEGY   does a SHORTER lookback still work? Ranks
                                 settings on the first half of history and
                                 tests the winner on the second half
                                 (--lookbacks 20,40,60,120,252 --split DATE)
  python run.py publish          build the PUBLIC static site (no app, no broker,
                                 no order code) — --out DIR, --positions
  python run.py journal          journal stats in the terminal
  python run.py selftest         run the deterministic-math unit tests
  python run.py ibkr-check       test the IB Gateway/TWS connection (places nothing)
  python run.py ibkr-data-check  check IBKR as a DATA source: prices, contract
                                 specs and option chains, and say exactly which
                                 subscription is missing if one is

Research / decision-support only — nothing is ever traded automatically,
and no output is financial advice.
"""
import json
import subprocess
import sys

from assistant.core.config import DISCLAIMER, PROJECT_ROOT


def _print_idea(idea):
    if idea.get("error"):
        print(f"  {idea['ticker']}: ERROR {idea['error']}")
        return
    gate, plan, thesis = idea["gate"], idea["plan"], idea["thesis"]
    print(f"\n=== {idea['ticker']}  (idea #{idea['idea_id']}) — {gate['verdict'].upper()} ===")
    setup = idea.get("strategy_idea")
    if setup:
        print(f"  Setup: {setup.get('strategy_label')} in "
              f"{(setup.get('regime') or '').replace('_', ' ').lower()} — {setup.get('headline')}")
        for reason in setup.get("reasons", []):
            print(f"    · {reason}")
    print(f"  Thesis [{thesis.get('source')}]: {thesis.get('thesis_summary')}")
    if plan:
        print(f"  Plan: {plan['direction'].upper()}  entry {plan['entry_zone'][0]}–{plan['entry_zone'][1]}"
              f"  stop {plan['stop']}  target {plan['target']}  ({plan['reward_risk']}:1  {plan['currency']})")
        print(f"        {plan['shares']} shares ≈ {plan['position_value']:,} {plan['currency']}  "
              f"max loss {plan['risk_amount']:,} {plan['currency']} — confidence {plan['confidence']}/100")
    else:
        print("  Plan: none — thesis does not support a defined-risk setup")
    for x in gate.get("hard_failures", []):
        print(f"  ✗ {x}")
    for x in gate.get("soft_flags", []):
        print(f"  ⚠ {x}")


PORT_NAMES = {7497: "TWS paper", 7496: "TWS LIVE", 4002: "Gateway paper", 4001: "Gateway LIVE"}


def _ibkr_check():
    """Diagnose the IB Gateway/TWS connection in plain English. Places no orders."""
    import socket

    from assistant.core.config import load_config

    config = load_config()
    exec_cfg = config.get("execution", {})
    host = exec_cfg.get("host", "127.0.0.1")
    port = int(exec_cfg.get("port", 7497))
    print(f"Config: execution.enabled={exec_cfg.get('enabled', False)}  {host}:{port} "
          f"({PORT_NAMES.get(port, 'unknown port')})\n")

    # 1. Is anything listening at all? Scan the usual ports to catch a mismatch.
    open_ports = []
    for candidate in (7497, 7496, 4002, 4001):
        sock = socket.socket()
        sock.settimeout(1)
        if sock.connect_ex((host, candidate)) == 0:
            open_ports.append(candidate)
        sock.close()

    if not open_ports:
        print("✗ Nothing is listening on any IB port.")
        print("  → Is IB Gateway (or TWS) running and logged in?")
        print("  → In Gateway: Configure → Settings → API → Settings →")
        print("    tick 'Enable ActiveX and Socket Clients'.")
        return

    print("Ports open: " + ", ".join(f"{p} ({PORT_NAMES.get(p, '?')})" for p in open_ports))
    if port not in open_ports:
        print(f"\n✗ Your config port {port} is NOT one of them.")
        print(f"  → Set execution.port in config.yaml to {open_ports[0]}, "
              f"or change the port inside Gateway to match.")
        return

    # 2. Real API handshake.
    try:
        from assistant.broker.ibkr import IBKRBroker
    except Exception as exc:
        print(f"\n✗ Could not load the IB library: {exc}")
        return

    if not exec_cfg.get("enabled"):
        print("\nNote: execution.enabled is false, so the dashboard stays research-only.")
        print("Testing the connection anyway (this places nothing)…")
        config = {**config, "execution": {**exec_cfg, "enabled": True}}

    try:
        broker = IBKRBroker(config)
        ping = broker.ping()
    except Exception as exc:
        print(f"\n✗ Port is open but the API handshake failed:\n  {exc}")
        print("  → Common cause: 127.0.0.1 not in Gateway's 'Trusted IPs',")
        print("    or another app is already using the same client id.")
        return

    accounts = ping.get("accounts") or []
    print(f"\n✓ Connected via {ping['library']}")
    print(f"  Accounts: {', '.join(accounts) or '(none reported)'}")
    if ping["paper"]:
        print("  Mode: PAPER (simulated money)")
    else:
        print("\n  ⚠️  Mode: LIVE — this is a REAL-MONEY account.")
        print("     IBKR paper accounts start with 'DU'; this one does not.")
        print("     If you meant to use paper: log out of IB Gateway and log back in")
        print("     with the Paper Trading option selected and your 'du…' username.")
        if ping.get("port_mismatch"):
            print(f"     (Port {port} is conventionally the paper port, which is why this")
            print("      is easy to miss — the account id is what actually decides.)")

    try:
        account = broker.get_account()
        print(f"  Net liquidation value: {account['portfolio_value']:,.2f} {account['base_currency']}")
        positions = broker.get_positions()
        print(f"  Open positions: {len(positions)}")
        for p in positions[:5]:
            print(f"    {p['ticker']}: {p['shares']:g} @ {p['entry_price']:.2f} {p['currency']}")
    except Exception as exc:
        print(f"  (Could not read account details: {exc})")

    print("\nNothing was ordered — this is a read-only check.")


def _fmt(value, suffix="", digits=2):
    return "—" if value is None else f"{value:,.{digits}f}{suffix}"


def _print_group(title, groups, ccy, limit=12):
    if not groups:
        return
    print(f"\n{title}")
    print(f"  {'':<34}{'Trades':>7}{'Win%':>7}{'Expect':>9}{'P&L':>13}{'PF':>7}  ")
    print("  " + "-" * 78)
    for _, g in list(groups.items())[:limit]:
        flag = "" if g["reliable"] else "  (small sample)"
        print(f"  {g['label'][:33]:<34}{g['trades']:>7}"
              f"{_fmt(g['win_rate_pct'], digits=1):>7}"
              f"{_fmt(g['expectancy_r'], 'R'):>9}"
              f"{_fmt(g['pnl']) + ' ' + ccy:>13}"
              f"{_fmt(g['profit_factor']):>7}{flag}")


CSV_COLUMNS = [
    ("signal_date", "Date the rules fired"),
    ("entry_date", "Date the position actually opened"),
    ("ticker", "Instrument"),
    ("strategy", "Which rule set produced it"),
    ("regime", "Market conditions at the time"),
    ("direction", "long or short"),
    ("headline", "Plain-English description of the setup"),
    ("signal_price", "Entry price the plan asked for"),
    ("entry_price", "Price actually filled, after slippage"),
    ("stop", "Where the trade was invalidated"),
    ("target", "Where it aimed to exit"),
    ("exit_price", "Price it actually closed at, after slippage"),
    ("exit_reason", "target / stop / timeout"),
    ("bars_held", "Trading days held"),
    ("shares", "Position size from your risk rules"),
    ("currency", "Currency this instrument trades in"),
    ("gross_pnl", "Profit before costs, in the instrument's currency"),
    ("costs", "Commission + slippage + stamp duty, instrument's currency"),
    ("pnl", "Profit after costs, in the instrument's currency"),
    ("fx_rate", "Rate to your base currency on the entry date"),
    ("pnl_base", "Profit after costs in YOUR base currency — the addable one"),
    ("costs_base", "Costs in your base currency"),
    ("r_multiple", "Profit as a multiple of the risk taken (currency-free)"),
    ("r_multiple_gross", "Same, before costs"),
]


def _write_trades_csv(path, out):
    """Every trade, one row each, with a header row of plain-English notes so the
    file is readable without the code next to it."""
    import csv as csv_module

    fields = [name for name, _ in CSV_COLUMNS]
    with open(path, "w", newline="") as handle:
        writer = csv_module.writer(handle)
        writer.writerow(fields)
        writer.writerow([note for _, note in CSV_COLUMNS])
        for trade in out["trades"]:
            writer.writerow([trade.get(f, "") for f in fields])
    return len(out["trades"])


def _paper_reset(argv):
    """Archive the current book and start a new one.

    ARCHIVES rather than deletes. A paper book is a track record, and a track
    record that can be silently erased and restarted is worth nothing — the
    ability to quietly rerun a bad month is exactly what makes a paper result
    unbelievable to anyone you show it to. The old file keeps its own timestamp
    so the sequence of books is reconstructable after the fact.
    """
    import os
    import shutil
    from datetime import datetime, timezone

    from assistant.paper.book import BOOK_PATH, Book

    book = Book.load()
    if not book.started:
        print("No paper book to reset.")
        return

    if "--yes" not in argv:
        summary = book.summary()
        print("This will archive the current paper book and start a new one.\n")
        print(f"  Started    {summary['started_at'][:10]}")
        print(f"  Equity     {summary['equity']:,.2f} {summary['base_currency']}")
        print(f"  Positions  {summary['open_positions']} open, "
              f"{summary['closed_trades']} closed")
        print("\nRe-run with --yes to confirm. The old book is archived, not deleted.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = f"{BOOK_PATH.rsplit('.json', 1)[0]}-archived-{stamp}.json"
    shutil.copy2(BOOK_PATH, archive)
    os.remove(BOOK_PATH)
    print(f"Archived to {os.path.basename(archive)}")
    print("New book starts on the next `python run.py paper` run.")


def _coverage(argv):
    """Measure what the licensed feed can actually price."""
    from assistant.core.config import load_config
    from assistant.providers import coverage

    config = load_config()
    symbols = config.get("watchlist", [])
    if "--universe" in argv:
        from assistant.paper import screen
        from assistant import pipeline
        symbols, _, _ = screen.tradable_universe(config, pipeline.get_router(config))

    print(f"Probing {len(symbols)} instruments against IBKR. "
          f"About a second each.\n")
    try:
        report = coverage.probe_symbols(
            symbols, config,
            progress_cb=lambda done, total, ticker: print(
                f"  {done}/{total} {ticker}", end="\r", flush=True))
    except Exception as exc:
        print(f"\nCould not measure coverage: {exc}")
        return
    coverage.save(report)

    counts = report["counts"]
    print(f"\n\nTRADABLE   {counts['tradable']:>4} of {counts['total']}"
          f"   (licensed IBKR data)")
    print(f"UNAVAILABLE{counts['unavailable']:>4}\n")
    for group in coverage.subscription_summary(report)["groups"]:
        print(f"  [{group['bundle']}] — {group['count']} instrument(s)")
        print(f"    {', '.join(group['symbols'])}")
        print(f"    -> {group['action']}\n")


def _sweep_all(argv):
    """Sweep every strategy that has a lookback, and write one report.

    Run as a batch rather than one at a time so every strategy is judged on the
    same split of the same history. Sweeping them separately over weeks would
    mean comparing results fitted to different windows, which is how a
    "best settings" table quietly becomes a collection of unrelated accidents.
    """
    import json as _json
    import os
    import time as _time

    from assistant import pipeline
    from assistant.backtest import sweep as sweep_mod
    from assistant.core.config import load_config
    from assistant.paper import session as paper_session, screen
    from assistant.providers import coverage

    limit = 250
    for i, a in enumerate(argv):
        if a == "--instruments" and i + 1 < len(argv):
            limit = int(argv[i + 1])

    config = load_config()
    router = pipeline.get_router(config)
    print("Loading history…")
    universe, _, _ = screen.tradable_universe(config, router)
    universe, _ = coverage.tradable_symbols(universe, config)
    out_state = {}
    frames = paper_session._fetch(
        universe[:limit], config["paper"]["history_period"], config, "sweep",
        out_state, cache_hours=config["paper"].get("universe_history_cache_hours"))
    benchmark = paper_session._benchmark(router, config)
    split_at = sweep_mod.midpoint_split(frames)

    # Each strategy is swept around ITS OWN published horizon. Testing a
    # three-year reversal at 20 bars is not a shorter version of it, it is a
    # different rule — so the grid is scaled per strategy rather than shared.
    lookbacks = paper_session._strategy_lookbacks(config)
    grids = {}
    for name, need in lookbacks.items():
        base = need
        if base >= 700:
            grids[name] = [126, 252, 504, 756]
        elif base >= 200:
            grids[name] = [20, 60, 120, 252]
        else:
            grids[name] = [5, 10, 21, 42]

    print(f"{len(frames)} instruments · split {split_at} · "
          f"{len(grids)} strategies\n")

    results, started = {}, _time.time()
    for index, (name, values) in enumerate(sorted(grids.items()), 1):
        print(f"[{index}/{len(grids)}] {name} — lookbacks {values}", flush=True)
        try:
            results[name] = sweep_mod.sweep(
                name, frames, config, {"lookback_bars": values}, split_at,
                benchmark=benchmark)
        except Exception as exc:
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"    {results[name].get('verdict') or results[name].get('error')}\n",
              flush=True)

    path = os.path.join(PROJECT_ROOT, "reports", "SWEEP-LOOKBACKS.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        _json.dump({"split_at": split_at, "instruments": len(frames),
                    "results": results}, handle, indent=2, default=str)

    print(f"\n{'strategy':<18}{'current':>9}{'best':>7}{'in R':>9}{'out R':>9}{'t out':>8}  verdict")
    for name in sorted(results):
        r = results[name]
        if r.get("error") or not r.get("winner"):
            print(f"{name:<18}{lookbacks.get(name,0):>9}{'—':>7}{'—':>9}{'—':>9}{'—':>8}  "
                  f"{(r.get('verdict') or r.get('error') or '')[:44]}")
            continue
        w, o = r["winner"], r["out_of_sample"]
        print(f"{name:<18}{lookbacks.get(name,0):>9}"
              f"{w['config'].get('lookback_bars'):>7}"
              f"{(w['in_sample']['mean_r'] or 0):>9.3f}{(o.get('mean_r') or 0):>9.3f}"
              f"{(o.get('t_stat') or 0):>8.2f}  {r['verdict'].split('.')[0]}")

    print(f"\nWrote {path}   ({(_time.time()-started)/60:.1f} min)")
    print("\nRead the DEGRADATION, not the best in-sample score. An out-of-sample")
    print("result that BEATS in-sample is a regime artefact, not robustness —")
    print("the same pattern reports/share/RESULTS-1M.md already flags.")
    print(f"\n{DISCLAIMER}")


def _sweep(argv):
    """Re-fit one strategy at shorter lookbacks, validated out-of-sample."""
    if argv and argv[0] == "--all":
        return _sweep_all(argv[1:])
    from assistant import pipeline
    from assistant.backtest import sweep as sweep_mod
    from assistant.core.config import load_config
    from assistant.paper import session as paper_session, screen
    from assistant.providers import coverage

    if not argv or argv[0].startswith("--"):
        print("Which strategy? e.g.  python run.py sweep ts_momentum")
        return
    strategy = argv[0]

    lookbacks = [20, 40, 60, 120, 252]
    split_at = None          # default: halve whatever history is available
    for i, a in enumerate(argv):
        if a == "--lookbacks" and i + 1 < len(argv):
            lookbacks = [int(x) for x in argv[i + 1].split(",")]
        if a == "--split" and i + 1 < len(argv):
            split_at = argv[i + 1]

    config = load_config()
    router = pipeline.get_router(config)
    print("Loading history (uses the session cache when it is warm)…")
    universe, _, _ = screen.tradable_universe(config, router)
    universe, _ = coverage.tradable_symbols(universe, config)
    universe = universe[:400]          # enough to rank; keeps the sweep tractable
    out = {}
    frames = paper_session._fetch(
        universe, config["paper"]["history_period"], config, "sweep", out,
        cache_hours=config["paper"].get("universe_history_cache_hours"))
    from assistant.backtest import sweep as _sw
    split_at = split_at or _sw.midpoint_split(frames)
    print(f"{len(frames)} instruments · split {split_at} · "
          f"{len(lookbacks)} lookbacks\n")

    result = sweep_mod.sweep(
        strategy, frames, config, {"lookback_bars": lookbacks}, split_at,
        benchmark=paper_session._benchmark(router, config),
        progress_cb=lambda i, n, c: print(f"  {i}/{n} lookback={c.get('lookback_bars')}",
                                          end="\r", flush=True))
    if result.get("error"):
        print(result["error"])
        return

    print(f"\n\n{'lookback':>9}{'trades':>8}{'mean R':>10}{'t':>7}{'win%':>7}")
    for row in result["results"]:
        sc = row["in_sample"]
        print(f"{row['config'].get('lookback_bars'):>9}{sc['trades']:>8}"
              f"{(sc['mean_r'] or 0):>10.4f}{(sc['t_stat'] or 0):>7.2f}"
              f"{(sc['win_rate_pct'] or 0):>7}")

    if result["winner"]:
        w = result["winner"]
        print(f"\nBest in-sample: lookback={w['config'].get('lookback_bars')}")
        print(f"Out-of-sample:  {result['out_of_sample']}")
    print(f"\n{result['verdict']}")
    print(f"\n{DISCLAIMER}")


def _publish(argv):
    """Build the public static site. Contains no order code and no broker."""
    import os

    from assistant.core.config import load_config
    from assistant.publish import export

    out_dir = os.path.join(PROJECT_ROOT, "public")
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]
    include_positions = "--positions" in argv

    result = export.write(out_dir, load_config(), include_positions=include_positions)
    data = result["data"]
    print(f"Wrote {result['html']}")
    print(f"      {result['json']}")
    if data.get("started"):
        s = data["summary"]
        print(f"\n  equity {s['equity']:,.2f} {s['base_currency']} "
              f"({s['return_pct']:+.2f}%) · {s['open_positions']} open · "
              f"{data['totals']['closed']} closed")
    print("\nStatic files only — nothing in them can place an order or reach a broker.")
    if include_positions:
        print("NOTE: --positions publishes your live holdings. Anyone reading the page")
        print("      learns what you are in before you are out of it.")


def _intraday(argv):
    """Test whether an edge exists at intraday horizons. Trades nothing."""
    from assistant.core.config import load_config
    from assistant.research import intraday

    config = load_config()

    # Consume flags and their VALUES together. Scanning for flags separately
    # and then filtering leaves each flag's argument behind, so `--bars 15m`
    # quietly added a ticker called "15m" to the universe.
    BAR_ALIASES = {"1m": "1 min", "5m": "5 mins", "15m": "15 mins",
                   "30m": "30 mins", "1h": "1 hour", "60m": "1 hour",
                   "2h": "2 hours"}
    bar_size, cost_bps, symbols = "5 mins", 10.0, []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--bars" and i + 1 < len(argv):
            raw = argv[i + 1].lower()
            bar_size = BAR_ALIASES.get(raw, raw)
            i += 2
        elif arg == "--cost-bps" and i + 1 < len(argv):
            try:
                cost_bps = float(argv[i + 1])
            except ValueError:
                print(f"--cost-bps needs a number, got {argv[i + 1]!r}")
                return
            i += 2
        elif arg.startswith("--"):
            i += 1
        else:
            symbols.append(arg.upper())
            i += 1

    if not symbols:
        symbols = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMZN", "META", "TSLA"]

    print(f"Fetching {bar_size} bars for {len(symbols)} instruments.")
    print("Historical bars only — nothing here is tradable on this account, "
          "which has no live quote feed.\n")
    try:
        frames = intraday.fetch(
            symbols, config, bar_size=bar_size,
            progress_cb=lambda d, t, s: print(f"  {d}/{t} {s}", end="\r", flush=True))
    except Exception as exc:
        print(f"\nCould not fetch intraday data: {exc}")
        return

    got = {k: v for k, v in frames.items() if v is not None and len(v)}
    print(f"\n\n{len(got)} of {len(symbols)} returned bars.")
    if not got:
        print("Nothing to evaluate.")
        return
    for ticker, frame in list(got.items())[:3]:
        print(f"  {ticker}: {len(frame)} bars, {frame.index[0]} -> {frame.index[-1]}")

    out = intraday.evaluate(got, cost_bps=cost_bps)
    print(f"\n{out['sessions']} sessions across {out['instruments']} instruments, "
          f"assuming {cost_bps} bps round-trip cost.\n")
    print(f"{'strategy':<26}{'trades':>7}{'net/trade':>11}{'win%':>7}"
          f"{'t':>7}{'breakeven':>11}  verdict")
    for name, r in out["results"].items():
        net, be = r["net"], r["breakeven_cost_bps"]
        if not net.get("trades"):
            print(f"{name:<26}{'—':>7}")
            continue
        print(f"{name:<26}{net['trades']:>7}{net['mean_pct']:>10.4f}%"
              f"{net['win_rate_pct']:>7}{(net['t_stat'] or 0):>7.2f}"
              f"{(be if be is not None else 0):>10.2f}b"
              f"  {'CLEARS COSTS' if r['tradable_at_cost'] else 'below costs'}")

    v = out["verdict"]
    print(f"\n{'EDGE FOUND' if v['edge_found'] else 'NO EDGE'} — {v['message']}")
    print(f"\n{DISCLAIMER}")


def _paper(argv):
    """One paper-trading session. Places nothing — there is no broker in it."""
    from assistant.paper import screen, session
    from assistant.paper.book import Book

    force = "--refresh-universe" in argv
    rebalance = True if "--rebalance" in argv else None

    if "--reset" in argv:
        _paper_reset(argv)
        return

    if "--status" in argv:
        book = Book.load()
        if not book.started:
            print("No paper book yet. Run `python run.py paper` to start one.")
            return
        summary = book.summary()
        ccy = summary["base_currency"]
        print(f"Paper book — started {summary['started_at'][:10]}, "
              f"{summary['days']} sessions")
        print(f"  Equity        {summary['equity']:,.2f} {ccy}  "
              f"({summary['return_pct']:+.2f}% from {summary['starting_equity']:,.0f})")
        print(f"  Cash          {summary['cash']:,.2f} {ccy}   "
              f"exposure {summary['exposure_pct']}%")
        print(f"  Positions     {summary['open_positions']} open, "
              f"{summary['closed_trades']} closed")
        if summary["win_rate_pct"] is not None:
            print(f"  Win rate      {summary['win_rate_pct']}%")
        print(f"  Max drawdown  {summary['max_drawdown_pct']}%")
        for position in book.positions:
            value = position["shares"] * position["last_price"]
            move = (position["last_price"] / position["entry_price"] - 1) * 100
            print(f"    {position['ticker']:<8} {position['strategy']:<14} "
                  f"{position['shares']:>8.0f} @ {position['entry_price']:>9.2f}  "
                  f"now {position['last_price']:>9.2f} ({move:+.1f}%)  "
                  f"value {value:>11,.0f}")
        print(f"\n{DISCLAIMER}")
        return

    print("Running one paper session. Nothing is traded; no broker is involved.\n")
    out = session.run(force_refresh=force, rebalance_override=rebalance,
                      progress_cb=lambda stage: print(f"  … {stage}", flush=True))

    print(f"\n{out['date']} — universe {out['universe']:,} instruments"
          f"{' (cached)' if out['universe_from_cache'] else ' (freshly screened)'}")
    if out.get("screen"):
        print(f"  {screen.describe(out['screen'])}")
    if out["rebalanced"]:
        print(f"  REBALANCE DAY — {out.get('candidates', 0)} candidates qualified")
    for closed in out["closed"]:
        print(f"  CLOSED  {closed['ticker']:<8} {closed['reason']:<7} "
              f"{closed['pnl']:>10,.2f}  ({closed['r']}R)")
    for opened in out["opened"]:
        print(f"  OPENED  {opened['ticker']:<8} {opened['strategy']:<14} "
              f"{opened['shares']:>8.0f} @ {opened['price']:>9.2f}  "
              f"stop {opened['stop']}")
    if out["skipped"]:
        blocked = {k: v for k, v in out["skipped"].items() if v}
        if blocked:
            print(f"  blocked by limits: {blocked}")
    for note in out["notes"]:
        print(f"  note: {note}")

    summary = out.get("summary")
    if summary:
        print(f"\n  Equity {summary['equity']:,.2f} {summary['base_currency']}  "
              f"({summary['return_pct']:+.2f}%)  "
              f"{summary['open_positions']} open  "
              f"exposure {summary['exposure_pct']}%")
    print(f"\n{DISCLAIMER}")


def _ibkr_data_check():
    """Prove IBKR data works BEFORE a study is run against it.

    Without a market-data subscription IBKR returns delayed or empty results
    rather than an error, which is the failure mode that once cached 537
    instruments as though they were the whole market. Each probe below reports
    what it actually got.
    """
    from assistant.core.config import load_config
    from assistant.providers.base import ProviderUnavailable
    from assistant.providers.ibkr_provider import IBKRDataProvider

    config = load_config()
    cfg = (config.get("providers") or {}).get("ibkr") or {}
    provider = IBKRDataProvider(config)

    print("IBKR data source check — nothing is traded, nothing is ordered.\n")
    print(f"  enabled : {cfg.get('enabled', False)}")
    print(f"  endpoint: {cfg.get('host','127.0.0.1')}:{cfg.get('port', 7497)}")
    if not cfg.get("enabled"):
        print("\n  Switched off. Set providers.ibkr.enabled: true in config.yaml,")
        print("  then start IB Gateway or TWS and log in (a paper account is fine).")
        return

    probes = [
        ("daily bars, liquid equity", lambda: provider.get_prices("AAPL", "1mo")),
        ("daily bars, futures", lambda: provider.get_prices("ES=F", "1mo")),
        ("daily bars, FX", lambda: provider.get_prices("EURUSD=X", "1mo")),
        ("contract spec, futures", lambda: provider.contract_spec("ES=F")),
        ("option chain", lambda: provider.option_chain("SPY")),
    ]
    ok = 0
    print()
    for label, probe in probes:
        try:
            result = probe()
            size = len(result) if hasattr(result, "__len__") else "?"
            print(f"  OK    {label:<26} ({size} rows/keys)")
            ok += 1
        except ProviderUnavailable as exc:
            print(f"  FAIL  {label:<26} {exc}")
        except Exception as exc:
            print(f"  FAIL  {label:<26} {type(exc).__name__}: {exc}")

    print(f"\n  {ok}/{len(probes)} probes passed.")
    if ok < len(probes):
        print("  A failure here is usually a missing market-data subscription for")
        print("  that instrument class, not a broken connection. Check IBKR")
        print("  Account Management -> Market Data Subscriptions.")
    else:
        print("  Ready. Re-run the study with IBKR as the price source.")
    print(f"\n{DISCLAIMER}")


def _backtest(argv):
    """Replay the strategies over history. Places nothing, touches no journal."""
    from assistant.backtest import HONEST_LIMITATIONS, engine, report
    from assistant.core.config import load_config
    from assistant import pipeline

    years, csv_path = 10, None
    tickers = []
    i = 0
    while i < len(argv):
        if argv[i] == "--years" and i + 1 < len(argv):
            years = int(argv[i + 1]); i += 2
        elif argv[i] == "--csv" and i + 1 < len(argv):
            csv_path = argv[i + 1]; i += 2
        elif argv[i] == "--universe" and i + 1 < len(argv):
            # A file of tickers (whitespace separated, # for comments) so a big
            # multi-market universe doesn't have to be typed on the command line.
            with open(argv[i + 1]) as handle:
                for line in handle:
                    line = line.split("#")[0]
                    tickers.extend(tok.upper() for tok in line.split())
            i += 2
        else:
            tickers.append(argv[i].upper()); i += 1

    config = load_config()
    router = pipeline.get_router(config)
    tickers = tickers or config.get("watchlist", [])
    if not tickers:
        print("No tickers. Add some to your watchlist or pass them as arguments.")
        return

    period = f"{years}y"
    print(f"Replaying {len(tickers)} instrument(s) over {years} years of history…")
    print("This runs the same strategies your live scan uses. Nothing is traded.\n")

    def prices(ticker):
        return router.get_prices(ticker, period=period)

    def benchmark():
        symbol = (config.get("scanner") or {}).get("benchmark")
        if not symbol:
            return None
        try:
            return router.get_prices(symbol, period=period)["Close"]
        except Exception:
            return None

    def currency_of(ticker):
        return router.get_instrument_currency(ticker)

    def fx_series(from_ccy, to_ccy):
        """Daily rate history, so each trade converts at its own date's rate."""
        if from_ccy == to_ccy:
            return None
        return router.get_prices(f"{from_ccy}{to_ccy}=X", period=period)["Close"]

    done = {"n": 0}

    def progress(ticker, bars, total):
        print(f"  {ticker}: {bars}/{total} bars", end="\r", flush=True)

    result = engine.run_backtest(tickers, config, prices, benchmark, progress,
                                 currency_fn=currency_of, fx_series_fn=fx_series)
    out = report.build(result, config, period=period)
    ccy = out["base_currency"]

    print(" " * 60, end="\r")
    overall, curve = out["overall"], out["equity_curve"]
    print("=" * 82)
    print(f"OVERALL — {overall['trades']} trades across {out['tickers_tested']} instrument(s)")
    print("=" * 82)
    if not overall["trades"]:
        print("\nNo trades. The rules never fully lined up on this sample — that is a")
        print("result, not a failure. Try more instruments or a longer period.")
    else:
        print(f"  Win rate      {_fmt(overall['win_rate_pct'], '%', 1)}"
              f"   ({overall['wins']}W / {overall['losses']}L / {overall['scratches']}S)")
        print(f"  Expectancy    {_fmt(overall['expectancy_r'], 'R')}   "
              "average profit per trade, in units of the risk taken")
        print(f"  Profit factor {_fmt(overall['profit_factor'])}   gross profit / gross loss")
        print(f"  Net P&L       {_fmt(curve['final_pnl'])} {ccy}   "
              f"(costs {_fmt(overall['costs'])} {ccy})")
        print(f"  Max drawdown  {_fmt(curve['max_drawdown'])} {ccy}   "
              "worst peak-to-trough fall")
        print(f"  Exits         {overall['targeted']} target · {overall['stopped']} stop "
              f"· {overall['timed_out']} timed out"
              f"   avg hold {_fmt(overall['avg_bars_held'], ' bars', 1)}")
        print(f"  Best / worst  {_fmt(overall['best_r'], 'R')} / {_fmt(overall['worst_r'], 'R')}")
        fills = out["fills"]
        print(f"  Fills         {fills['traded']} of {fills['signals']} signals traded "
              f"· {fills['missed_fills']} never filled "
              f"· {fills['skipped_risk_collapsed']} skipped (fill drifted onto the stop)")

        _print_group("BY STRATEGY", out["by_strategy"], ccy)
        _print_group("BY MARKET REGIME", out["by_regime"], ccy)
        _print_group("BY STRATEGY x REGIME", out["by_strategy_regime"], ccy)
        _print_group("BY DIRECTION", out["by_direction"], ccy)
        _print_group("BY COUNTRY / EXCHANGE", out["by_market"], ccy, limit=15)
        _print_group("BY CURRENCY", out["by_currency"], ccy, limit=12)
        _print_group("BY YEAR", out["by_year"], ccy)

    if csv_path:
        written = _write_trades_csv(csv_path, out)
        print(f"\n  Wrote {written} trades to {csv_path}")

    for note in out["notes"][:10]:
        print(f"\n  note: {note}")

    print("\n" + "=" * 82)
    print("READ THIS BEFORE BELIEVING ANY NUMBER ABOVE")
    print("=" * 82)
    for limitation in HONEST_LIMITATIONS:
        print(f"  • {limitation}")
    print(f"\n{DISCLAIMER}")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "serve"

    if cmd == "scan":
        from assistant import pipeline
        print("Scanning watchlist…")
        result = pipeline.run_scan()
        print(f"\n{'Ticker':<7}{'Price':>9}{'Chg%':>7}{'Vol x':>7}{'RSI':>5}  "
              f"{'Market':<20}Setups")
        for w in result["watchlist"]:
            if w.get("error"):
                print(f"{w['ticker']:<7}  error: {w['error']}")
                continue
            market = (w.get("regime") or "-").replace("_", " ").lower()
            found = w.get("setup_count", 0)
            outcome = f"{found} setup(s)" if found else ((w.get("router_notes") or ["-"])[0])
            print(f"{w['ticker']:<7}{w['price']:>9}{w['change_pct']:>7}{w['volume_ratio'] or 0:>7}"
                  f"{w['rsi'] or 0:>5}  {market:<20}{outcome}")
        for idea in result["ideas"]:
            _print_idea(idea)
        print(f"\n{DISCLAIMER}")

    elif cmd == "analyze" and len(args) > 1:
        from assistant import pipeline
        from assistant.core.config import load_config
        from assistant.providers.base import ProviderUnavailable
        config = load_config()
        router = pipeline.get_router(config)
        symbol = args[1].upper()
        try:
            router.get_prices(symbol, period="5d")
        except ProviderUnavailable:
            try:
                matches = router.search_symbol(args[1])
            except ProviderUnavailable as exc:
                print(f"Market data is temporarily unavailable: {exc}")
                return
            if not matches:
                print(f"Couldn't find any symbol matching '{args[1]}'")
                return
            symbol = matches[0]["symbol"].upper()
            print(f"'{args[1]}' resolved to {symbol} ({matches[0]['name']})")
        idea = pipeline.analyze_ticker(symbol, config)
        if idea.get("error"):
            print(f"Error: {idea['error']}")
        else:
            _print_idea(idea)
            print(f"\n{DISCLAIMER}")

    elif cmd == "serve":
        from assistant.web import server
        port = int(args[1]) if len(args) > 1 else None
        server.main(port)

    elif cmd == "backtest":
        _backtest(args[1:])

    elif cmd == "paper":
        _paper(args[1:])

    elif cmd == "coverage":
        _coverage(args[1:])

    elif cmd == "intraday":
        _intraday(args[1:])

    elif cmd == "publish":
        _publish(args[1:])

    elif cmd == "sweep":
        _sweep(args[1:])

    elif cmd == "journal":
        from assistant import journal
        print(json.dumps(journal.stats(), indent=2))
        for idea in journal.list_ideas(20):
            print(f"#{idea['id']} {idea['ticker']} {idea['verdict']} -> "
                  f"decision={idea['decision']} outcome={idea['outcome']}")

    elif cmd == "ibkr-data-check":
        _ibkr_data_check()

    elif cmd == "ibkr-check":
        _ibkr_check()

    elif cmd == "selftest":
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "tests", "-q"],
                                 cwd=PROJECT_ROOT))

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
