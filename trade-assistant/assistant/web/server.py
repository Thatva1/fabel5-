"""Dashboard web server (Layer 1). Local, single-user. No order routing exists
anywhere in this app; the human-approval endpoints only record decisions."""
import os
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from .. import closeout, execution, journal, pipeline
from ..core.config import (DISCLAIMER, add_to_watchlist, load_config,
                           remove_from_watchlist, set_execution_enabled)
from ..execution import ExecutionRefused
from ..providers.base import ProviderUnavailable
from ..strategies import registry as strategy_registry

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Hostnames this server will answer to. Binding to 127.0.0.1 keeps other
# machines out, but it does NOT stop DNS rebinding: a page on evil.example
# whose DNS record flips to 127.0.0.1 becomes same-origin with the dashboard
# and can then POST JSON to these endpoints. Nothing here is authenticated, so
# that page could read /api/state for a ticker and drive the order flow with
# it. Checking the Host header defeats rebinding outright — a rebound request
# still carries the attacker's hostname.
ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _hostname_of(host_header):
    """Strip the port, keeping an IPv6 literal's brackets: '[::1]:5002' -> '[::1]'."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]" if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


@app.before_request
def _reject_foreign_host_headers():
    hostname = _hostname_of(request.host)
    if hostname not in ALLOWED_HOSTNAMES:
        return jsonify({
            "error": f"Refused: this server only answers to localhost, not '{hostname}'. "
                     "If you did not type this address yourself, a web page may be "
                     "trying to reach your dashboard."}), 403

_state = {"scan": None, "scanning": False, "error": None,
          "progress": None, "cancel": False}
_lock = threading.Lock()


def _run_scan_background():
    def progress_cb(done, total, ticker, stage):
        with _lock:
            _state["progress"].update(
                {"done": done, "total": total, "current_ticker": ticker,
                 "current_stage": stage})

    def should_cancel():
        with _lock:
            return _state["cancel"]

    try:
        result = pipeline.run_scan(progress_cb=progress_cb, should_cancel=should_cancel)
        with _lock:
            _state["scan"], _state["error"] = result, None
    except Exception as exc:
        with _lock:
            _state["error"] = str(exc)
    finally:
        with _lock:
            _state["scanning"] = False
            _state["cancel"] = False
            _state["progress"] = None


@app.get("/")
def index():
    return render_template("dashboard.html", disclaimer=DISCLAIMER)


STALE_AFTER_HOURS = 24
STALE_DRIFT_PCT = 2.0


def _mark_stale(ideas, router):
    """Flag ideas whose plan no longer matches the market, so the UI can warn
    without every client re-pricing all of them. Cheap: only checks ideas the
    user has approved (the only ones that can become orders) and relies on the
    router's price cache."""
    for idea in ideas:
        idea["stale"] = False
        idea["stale_reason"] = None
        plan = (idea.get("payload") or {}).get("plan")
        if not plan or idea.get("decision") != "approved":
            continue
        try:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(idea["created_at"])).total_seconds() / 3600
        except Exception:
            age_h = None
        if age_h is not None and age_h > STALE_AFTER_HOURS:
            idea["stale"] = True
            idea["stale_reason"] = f"plan is {int(age_h)}h old"
            continue
        try:
            df = router.get_prices(idea["ticker"], period="5d")
            last = float(df["Close"].iloc[-1])
            drift = (last / plan["entry"] - 1) * 100
            if abs(drift) > STALE_DRIFT_PCT:
                idea["stale"] = True
                idea["stale_reason"] = (f"price {last:.2f} is {drift:+.1f}% from the "
                                        f"planned entry {plan['entry']:.2f}")
        except Exception:
            pass
    return ideas


@app.get("/api/paper")
def api_paper():
    """The paper book: cash, open positions, and the session log.

    Separate from /api/state, which serves the journal — ideas a human decides
    on. This is what the RULES did with nobody intervening, and mixing the two
    would make it impossible to tell a good month from good judgement.
    """
    from ..core import market_clock
    from ..markets import asset_class_of, exposure_group
    from ..paper.book import Book

    book = Book.load()
    if not book.started:
        return jsonify({"started": False,
                        "message": "No paper book yet. Run a session to start one."})

    positions = []
    for p in book.positions:
        freshness = market_clock.bar_status(p["ticker"], p.get("bar_date"))
        value = book.position_value(p)
        notional = abs(p["last_price"] * float(p.get("multiplier", 1.0) or 1.0)
                       * p["shares"])
        move = ((p["last_price"] / p["entry_price"] - 1) * 100
                if p["entry_price"] else 0.0)
        positions.append({
            **{k: p.get(k) for k in ("ticker", "direction", "shares", "entry_price",
                                     "last_price", "stop", "target", "strategy",
                                     "entry_date", "bars_held", "headline")},
            "segment": asset_class_of(p["ticker"]),
            "exposure_group": exposure_group(p["ticker"]),
            "value": round(value, 2),
            "notional": round(notional, 2),
            "move_pct": round(move, 2),
            "unrealised": round(value - float(p.get("committed", 0) or 0), 2),
            # Provenance travels with every row. A price with no feed name and
            # no bar date beside it cannot be checked by the person reading it,
            # and this dashboard spent its whole life showing exactly that.
            "price_source": p.get("price_source"),
            "bar_date": p.get("bar_date"),
            "priced_at": p.get("priced_at"),
            "mark_failed": bool(p.get("mark_failed")),
            "freshness": freshness["label"],
            "current": freshness["current"],
            "sessions_behind": freshness["sessions_behind"],
        })
    positions.sort(key=lambda x: -abs(x["notional"]))

    staleness = book.staleness()
    sources = book.price_sources()
    return jsonify({
        "started": True,
        "summary": book.summary(),
        "gross_exposure": round(book.gross_exposure(), 2),
        "positions": positions,
        "closed": book.closed[-25:][::-1],
        "curve": book.curve[-120:],
        "sessions": book.sessions[-10:][::-1],
        # --- provenance and freshness, the headline facts about this book ---
        "as_of": book.as_of,
        "price_sources": sources,
        "licensed": sources == ["ibkr"],
        "markets": market_clock.summary(),
        "staleness": staleness,
        "banner": _book_banner(book, sources, staleness),
    })


def _book_banner(book, sources, staleness):
    """One sentence saying whether these numbers can be trusted right now.

    Ranked worst-first and only ONE is returned, because a row of warnings is
    read as decoration. The distinction that matters most is the one the
    original complaint turned on: a book showing Friday's close on a Monday
    morning is correct and must not be labelled stale, while a book that
    skipped a session it should have run is a real problem wearing the same
    dates.
    """
    if not book.positions:
        return {"level": "ok", "message": "No open positions."}

    failed = [p["ticker"] for p in book.positions if p.get("mark_failed")]
    if failed:
        return {"level": "error",
                "message": f"{len(failed)} position(s) could not be priced at the "
                           f"last run and are carried at an old mark: "
                           f"{', '.join(failed[:6])}. Treat their value as unknown."}

    if not staleness["all_current"]:
        behind = staleness["worst_sessions_behind"]
        return {"level": "warn",
                "message": f"Prices are {behind} trading session(s) behind. Run a "
                           f"session to mark the book to the latest close."}

    unlicensed = [s for s in sources if s != "ibkr"]
    if unlicensed:
        return {"level": "warn",
                "message": f"Some positions are priced by {', '.join(unlicensed)}, "
                           f"not the licensed IBKR feed. Those numbers are not "
                           f"licensed for commercial use."}

    return {"level": "ok",
            "message": "All positions marked to the latest close on licensed "
                       "IBKR data."}


@app.get("/api/coverage")
def api_coverage():
    """What the licensed feed can price, and what a subscription would unlock."""
    from ..providers import coverage

    report = coverage.load()
    summary = coverage.subscription_summary(report)
    if report:
        summary["tradable_count"] = len(report.get("tradable") or {})
        summary["tradable"] = sorted((report.get("tradable") or {}).keys())
    return jsonify(summary)


@app.post("/api/coverage/refresh")
def api_coverage_refresh():
    """Re-measure coverage against the account that is logged in right now.

    Synchronous and slow (about a second per instrument) because it is a
    deliberate action taken rarely, not something a page load triggers.
    """
    from ..providers import coverage

    config = load_config()
    symbols = (request.get_json(silent=True) or {}).get("symbols") \
        or config.get("watchlist", [])
    try:
        report = coverage.probe_symbols(symbols, config)
    except ProviderUnavailable as exc:
        return jsonify({"error": str(exc), "retryable": True}), 503
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    coverage.save(report)
    return jsonify(coverage.subscription_summary(report))


@app.post("/api/paper/run")
def api_paper_run():
    """Run one session on demand. Places no orders — there is no broker in it."""
    from ..paper import session as paper_session

    force = bool((request.get_json(silent=True) or {}).get("rebalance"))
    try:
        result = paper_session.run(rebalance_override=True if force else None)
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({k: result.get(k) for k in
                    ("date", "rebalanced", "opened", "closed", "skipped",
                     "notes", "universe", "price_source", "summary")})


@app.get("/api/news/<path:ticker>")
def api_news(ticker):
    """Headlines for one instrument, shown beside the position that holds it.

    Context for a human reading the book, not an input to any decision. No
    strategy consumes this and no position is sized by it.
    """
    from ..providers.base import ProviderUnavailable
    from ..providers.ibkr_provider import IBKRDataProvider

    try:
        return jsonify(IBKRDataProvider(load_config()).get_news(ticker, limit=8))
    except ProviderUnavailable as exc:
        return jsonify({"ticker": ticker, "headlines": [], "error": str(exc)})
    except Exception as exc:
        return jsonify({"ticker": ticker, "headlines": [],
                        "error": f"{type(exc).__name__}: {exc}"})


@app.get("/api/verdict/<path:ticker>")
def api_verdict(ticker):
    """Buy / sell / avoid on one instrument, with the reasoning behind it.

    Runs the full research pipeline, so it costs a data fetch and (when AI is
    enabled) one model call. That expense is why it is an explicit request
    rather than something computed for every row of the dashboard.
    """
    from ..research import verdict as verdict_module

    config = load_config()
    symbol = ticker.strip().upper()
    try:
        analysis = pipeline.analyze_ticker(symbol, config)
    except ProviderUnavailable as exc:
        return jsonify({"error": f"Market data unavailable: {exc}",
                        "retryable": True}), 503
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    if analysis.get("error"):
        return jsonify({"error": analysis["error"]}), 422

    out = verdict_module.for_idea(analysis, config)
    out["disclaimer"] = DISCLAIMER
    return jsonify(out)


@app.get("/api/paper/verdicts")
def api_paper_verdicts():
    """Hold or close, for every position currently in the paper book.

    Cheap: reads the levels fixed when each position was opened against its
    last mark. No fetch, no model call — which is what lets it be shown next to
    every row rather than requested one at a time.
    """
    from ..paper.book import Book
    from ..research import verdict as verdict_module

    config = load_config()
    book = Book.load()
    if not book.started:
        return jsonify({"started": False, "verdicts": []})
    verdicts = [verdict_module.for_position(p, config=config) for p in book.positions]
    return jsonify({"started": True, "verdicts": verdicts,
                    "summary": verdict_module.summarise(verdicts),
                    "disclaimer": DISCLAIMER})


@app.get("/api/state")
def api_state():
    try:
        config = load_config()
    except ValueError as exc:
        return jsonify({"config_error": str(exc), "disclaimer": DISCLAIMER})
    router = pipeline.get_router(config)
    account = config.get("account", {})
    positions = config.get("positions", [])
    base_ccy = config.get("base_currency", "USD")
    portfolio_value = account.get("portfolio_value", 0) or 1

    from ..risk.gate import exposure_summary
    fx_rates = router.get_fx_rates(
        {base_ccy} | {p.get("currency", base_ccy) for p in positions}, base_ccy)
    exposure = exposure_summary(positions, portfolio_value, None, base_ccy, fx_rates)

    with _lock:
        scan, scanning, error = _state["scan"], _state["scanning"], _state["error"]
        progress = dict(_state["progress"]) if _state["progress"] else None
    return jsonify({
        "disclaimer": DISCLAIMER,
        "ai_ready": bool(os.environ.get("ANTHROPIC_API_KEY")) and config.get("ai", {}).get("enabled", True),
        "providers": router.provider_status(),
        "base_currency": base_ccy,
        "watchlist_symbols": config.get("watchlist", []),
        "scan": scan,
        "scanning": scanning,
        "scan_error": error,
        "portfolio": {
            "value": portfolio_value,
            "positions": positions,
            "exposure_value": exposure["current_value"],
            "exposure_pct": exposure["current_pct"],
            "risk_per_trade_pct": account.get("risk_per_trade_pct"),
            "max_total_exposure_pct": account.get("max_total_exposure_pct"),
        },
        "scan_progress": progress,
        "strategies": strategy_registry.describe(config),
        "ideas": _mark_stale(journal.list_ideas(), router),
        "stats": journal.stats(),
        "execution": execution.execution_status(config),
        "orders": journal.list_orders(),
    })


@app.post("/api/scan")
def api_scan():
    from datetime import datetime, timezone
    with _lock:
        if _state["scanning"]:
            return jsonify({"status": "already running"}), 409
        _state["scanning"] = True
        _state["cancel"] = False
        _state["progress"] = {
            "done": 0, "total": len(load_config().get("watchlist", [])),
            "current_ticker": None, "current_stage": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    threading.Thread(target=_run_scan_background, daemon=True).start()
    return jsonify({"status": "started"})


@app.post("/api/scan/cancel")
def api_scan_cancel():
    """Stop a running scan. Work already completed is kept, not discarded."""
    with _lock:
        if not _state["scanning"]:
            return jsonify({"error": "no scan is running"}), 409
        _state["cancel"] = True
    return jsonify({"status": "cancelling"})


@app.post("/api/analyze/<query>")
def api_analyze(query):
    config = load_config()
    router = pipeline.get_router(config)
    symbol = query.strip().upper()
    resolved_note = None
    try:
        try:
            router.get_prices(symbol, period="5d")
        except ProviderUnavailable:
            # Unknown symbol OR upstream outage. The search call distinguishes
            # them: if that fails too, the data source itself is down.
            try:
                matches = router.search_symbol(query.strip())
            except ProviderUnavailable as exc:
                return jsonify({
                    "error": "Market data is temporarily unavailable — the data provider "
                             f"could not be reached. Try again shortly. ({exc})",
                    "retryable": True}), 503
            if not matches:
                return jsonify({"error": f"couldn't find any symbol matching '{query}'"}), 404
            best = matches[0]
            symbol = best["symbol"].upper()
            resolved_note = f"'{query}' resolved to {symbol} ({best['name']})"
        result = pipeline.analyze_ticker(symbol, config)
        if result.get("error"):
            return jsonify(result), 422
        if resolved_note:
            result["resolved_note"] = resolved_note
        return jsonify(result)
    except ProviderUnavailable as exc:
        return jsonify({"error": f"Market data is temporarily unavailable: {exc}",
                        "retryable": True}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/symbols")
def api_symbols():
    """Type-ahead over the whole tradable universe (~18k US symbols).
    Read-only and free — no AI credits, no scan."""
    q = (request.args.get("q") or "").strip()
    router = pipeline.get_router(load_config())
    if not q:
        return jsonify({"results": [], "universe_size": router.universe_size()})
    return jsonify({"results": router.suggest(q, limit=int(request.args.get("limit", 8))),
                    "universe_size": router.universe_size()})


@app.post("/api/watchlist/add")
def api_watchlist_add():
    """Add a symbol to the scan list. Accepts a ticker OR a company name —
    'goldman sachs' resolves to GS rather than being rejected for the space."""
    raw = ((request.json or {}).get("ticker") or "").strip()
    if not raw:
        return jsonify({"error": "enter a ticker or company name"}), 400
    config = load_config()
    router = pipeline.get_router(config)

    ticker = raw.upper()
    looks_like_symbol = len(ticker) <= 12 and all(c.isalnum() or c in ".-^=" for c in ticker)
    if not looks_like_symbol:
        matches = router.suggest(raw, limit=1)
        if not matches:
            return jsonify({"error": f"couldn't find a symbol matching '{raw}'"}), 404
        ticker = matches[0]["symbol"].upper()

    try:
        router.get_prices(ticker, period="5d")
    except Exception:
        # Might be a name that looked symbol-ish (e.g. "Nvidia") — try resolving.
        matches = router.suggest(raw, limit=1)
        if matches and matches[0]["symbol"].upper() != ticker:
            ticker = matches[0]["symbol"].upper()
            try:
                router.get_prices(ticker, period="5d")
            except Exception:
                return jsonify({"error": f"no market data found for {raw}"}), 404
        else:
            return jsonify({"error": f"no market data found for {raw}"}), 404
    watchlist = add_to_watchlist(ticker)
    return jsonify({"status": "ok", "ticker": ticker, "watchlist": watchlist})


@app.post("/api/watchlist/remove")
def api_watchlist_remove():
    ticker = ((request.json or {}).get("ticker") or "").strip().upper()
    removed, watchlist = remove_from_watchlist(ticker)
    if not removed:
        return jsonify({"error": "not on watchlist"}), 404
    return jsonify({"status": "ok", "watchlist": watchlist})


@app.post("/api/ideas/<int:idea_id>/decision")
def api_decision(idea_id):
    decision = (request.json or {}).get("decision")
    if decision not in ("approved", "needs_research", "rejected", "pending"):
        return jsonify({"error": "invalid decision"}), 400
    if not journal.set_decision(idea_id, decision):
        return jsonify({"error": f"idea #{idea_id} does not exist"}), 404
    return jsonify({"status": "ok"})


@app.post("/api/ideas/<int:idea_id>/outcome")
def api_outcome(idea_id):
    body = request.json or {}
    outcome = body.get("outcome")
    if outcome not in ("open", "win", "loss", "scratch"):
        return jsonify({"error": "invalid outcome"}), 400

    price, entry_override = body.get("price"), body.get("entry")
    for label, value in (("exit price", price), ("entry price", entry_override)):
        if value in ("", None):
            continue
        try:
            if float(value) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": f"The {label} must be a positive number."}), 400

    result = closeout.close_idea(
        idea_id, outcome,
        exit_price=float(price) if price not in ("", None) else None,
        notes=body.get("notes"),
        entry_override=float(entry_override) if entry_override not in ("", None) else None)
    if not result.get("ok"):
        return jsonify({"error": result.get("error", f"idea #{idea_id} does not exist")}), 404
    return jsonify({"status": "ok", "realised": result["realised"],
                    "warnings": result["warnings"]})


@app.post("/api/execution/toggle")
def api_execution_toggle():
    """Turn the execution layer on/off without restarting.

    Asymmetric on purpose: turning OFF is instant (the safe direction should
    never be obstructed), turning ON requires typing ENABLE. This only controls
    whether order entry is *possible* — it bypasses none of the per-order gates.
    """
    body = request.json or {}
    want_enabled = bool(body.get("enabled"))
    if want_enabled and (body.get("confirmation") or "").strip().upper() != "ENABLE":
        return jsonify({"error": "To turn execution ON, type ENABLE to confirm."}), 400

    set_execution_enabled(want_enabled)
    # Re-read so the status reflects what was actually persisted, not what we
    # intended to persist.
    status = execution.execution_status(load_config())
    return jsonify({"status": "ok", "enabled": want_enabled, "execution": status})


@app.post("/api/ideas/<int:idea_id>/prepare-order")
def api_prepare_order(idea_id):
    """Step 1 of 2: build a reviewable ticket. Places nothing."""
    config = load_config()
    try:
        ticket = execution.prepare_ticket(idea_id, config, pipeline.get_router(config))
        return jsonify(ticket)
    except ExecutionRefused as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        # Broker/network problems are service-unavailable, not app crashes.
        if type(exc).__name__ in ("GatewayUnreachable", "AccountMismatch", "ProviderUnavailable"):
            return jsonify({"error": str(exc), "retryable": True}), 503
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/orders/<int:ticket_id>/confirm")
def api_confirm_order(ticket_id):
    """Step 2 of 2: place the order — requires the typed ticker confirmation."""
    typed = (request.json or {}).get("confirmation", "")
    try:
        config = load_config()
        # The router lets confirm re-check the live price before placing.
        result = execution.confirm_ticket(ticket_id, typed, config,
                                          router=pipeline.get_router(config))
        return jsonify(result)
    except ExecutionRefused as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        # Broker/network problems are service-unavailable, not app crashes.
        if type(exc).__name__ in ("GatewayUnreachable", "AccountMismatch", "ProviderUnavailable"):
            return jsonify({"error": str(exc), "retryable": True}), 503
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/orders/<int:ticket_id>/cancel")
def api_cancel_order(ticket_id):
    try:
        return jsonify(execution.cancel_ticket(ticket_id))
    except ExecutionRefused as exc:
        return jsonify({"error": str(exc)}), 409


def main(port=None):
    if port is None:
        port = load_config().get("server", {}).get("port", 5002)
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
