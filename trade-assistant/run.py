#!/usr/bin/env python3
"""Trade Assistant CLI (Layer 1 — research only).

  python run.py serve [port]     dashboard (default port from config.yaml: 5002)
  python run.py scan             scan watchlist, full pipeline on flagged tickers
  python run.py analyze TICKER   force the full pipeline on one ticker/name
  python run.py backtest [TICKERS...]   replay the strategies over history
                                 (defaults to your watchlist; --years N,
                                  --csv FILE to write every trade to a spreadsheet)
  python run.py journal          journal stats in the terminal
  python run.py selftest         run the deterministic-math unit tests
  python run.py ibkr-check       test the IB Gateway/TWS connection (places nothing)

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

    elif cmd == "journal":
        from assistant import journal
        print(json.dumps(journal.stats(), indent=2))
        for idea in journal.list_ideas(20):
            print(f"#{idea['id']} {idea['ticker']} {idea['verdict']} -> "
                  f"decision={idea['decision']} outcome={idea['outcome']}")

    elif cmd == "ibkr-check":
        _ibkr_check()

    elif cmd == "selftest":
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "tests", "-q"],
                                 cwd=PROJECT_ROOT))

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
