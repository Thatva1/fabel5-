"""Dashboard web server (Layer 1). Local, single-user. No order routing exists
anywhere in this app; the human-approval endpoints only record decisions."""
import os
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request, url_for

from .. import closeout, execution, journal, pipeline
from ..core.config import (DISCLAIMER, add_to_watchlist, load_config,
                           remove_from_watchlist, set_execution_enabled)
from ..execution import ExecutionRefused
from ..providers.base import ProviderUnavailable
from ..strategies import registry as strategy_registry

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
app.config["TEMPLATES_AUTO_RELOAD"] = True
# Never let a browser hold on to app.js or app.css. The dashboard is a local
# single-user tool that is edited constantly, and a cached script is the worst
# possible failure here: the page still renders, so it looks like working
# software reporting stale numbers rather than like an old file. Combined with
# the version stamp below, a reload cannot serve yesterday's code.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.context_processor
def _asset_version():
    """Stamp every static URL with the file's own modification time.

    `url_for('static', ...)` alone yields a stable URL, so a browser that has
    cached it may keep using it. Appending the mtime changes the URL the moment
    the file changes, which is the only cache-busting that does not depend on
    the browser choosing to revalidate.
    """
    def static_url(filename):
        path = os.path.join(app.static_folder or "", filename)
        try:
            stamp = int(os.path.getmtime(path))
        except OSError:
            stamp = 0
        return url_for("static", filename=filename, v=stamp)
    return {"static_url": static_url}


@app.after_request
def _no_store_on_api(response):
    """API responses must never be cached.

    A 304 on /api/state would hand the page a snapshot of the book from
    whenever the browser last asked, which is indistinguishable from the app
    having frozen.
    """
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

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
          # A session run started from THIS process's /api/paper/run. The
          # scheduler keeps its own flag for the runs it starts; a manual exit
          # has to respect both, because either one holds the book in memory
          # and saves it whole at the end.
          "progress": None, "cancel": False, "paper_running": False}
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
    # The build stamp is printed in the footer so "which version are you
    # looking at" is answerable. Two browsers disagreeing about the same server
    # is almost always one of them holding a cached script, and without a
    # visible version there is no way for the reader to tell me which.
    try:
        stamp = int(os.path.getmtime(os.path.join(app.static_folder or "", "app.js")))
    except OSError:
        stamp = 0
    return render_template("dashboard.html", disclaimer=DISCLAIMER, build_stamp=stamp)


STALE_AFTER_HOURS = 24
STALE_DRIFT_PCT = 2.0


def _mark_stale(ideas, router):
    """Flag ideas whose plan no longer matches the market.

    Two checks, and they cost very different amounts:

      * AGE is arithmetic on a stored timestamp. Free, so every idea gets it.
      * PRICE DRIFT needs a quote per instrument. Paid, so only ideas that
        could actually become an order get it.

    Those were previously conflated, and the whole function returned early for
    anything the user had not approved. The result was that a journal of ideas
    12 to 21 days old rendered with exactly one staleness warning on it — the
    single approved one — while eighty-one plans built against three-week-old
    prices displayed as though they were current. The saving was never the
    reason to skip the age check; it was only ever the reason to skip the fetch.
    """
    for idea in ideas:
        idea["stale"] = False
        idea["stale_reason"] = None
        idea["age_hours"] = None

        try:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(idea["created_at"])).total_seconds() / 3600
        except Exception:
            age_h = None
        idea["age_hours"] = round(age_h, 1) if age_h is not None else None

        if age_h is not None and age_h > STALE_AFTER_HOURS:
            idea["stale"] = True
            days = age_h / 24
            idea["stale_reason"] = (
                f"researched {days:.0f} days ago" if days >= 2
                else f"researched {int(age_h)}h ago")
            # Already stale on age; re-pricing it cannot make it less so.
            continue

        plan = (idea.get("payload") or {}).get("plan")
        if not plan or idea.get("decision") != "approved":
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


def _reporting_currency(book, summary, config):
    """The book's value in the ACCOUNT's currency, at today's rate.

    Why both numbers exist, and why neither replaces the other:

      * The book runs in USD because it holds one cash balance and most of its
        instruments are dollar-denominated. Its USD return is the STRATEGY's
        return — no exchange rate in it. That is the number to judge the rules
        by, and the number a backtest is comparable to.

      * The account is in GBP. What the holder can actually spend is the USD
        equity converted at today's rate, which moves with sterling whether or
        not a single position does. That is the number that matters to a UK
        reader, and it is not a measure of the strategy.

    Reporting one and hiding the other misleads in a different direction each
    way: USD alone tells a UK investor nothing about their money, while GBP
    alone credits or blames the strategy for currency moves it never took a
    position on. So both are served, labelled, and the FX contribution between
    them is stated outright.
    """
    account_ccy = (config.get("base_currency") or "GBP").upper()
    book_ccy = (summary.get("base_currency") or "USD").upper()
    if account_ccy == book_ccy:
        return None

    try:
        router = pipeline.get_router(config)
        rates = router.get_fx_rates({book_ccy, account_ccy}, account_ccy)
        rate = rates.get(f"{book_ccy}{account_ccy}")
        if not rate:
            return None
    except Exception:
        return None

    equity = summary["equity"] * rate
    # The opening balance was converted at the rate on the day the book began.
    # Reconstructing it from that rate — rather than today's — is what makes the
    # difference between the two return figures the FX contribution.
    opening_rate = None
    for session in book.sessions:
        note = session.get("note") or ""
        if "converted" in note and "at " in note:
            try:
                opening_rate = float(note.rsplit("at ", 1)[1].split()[0].rstrip("."))
            except (ValueError, IndexError):
                opening_rate = None
            break

    start_account = (summary["starting_equity"] * (1 / opening_rate)
                     if opening_rate else summary["starting_equity"] * rate)
    return_pct = ((equity / start_account - 1) * 100) if start_account else None

    return {
        "currency": account_ccy,
        "rate": round(rate, 6),
        "pair": f"{book_ccy}{account_ccy}",
        "equity": round(equity, 2),
        "starting_equity": round(start_account, 2),
        "return_pct": round(return_pct, 2) if return_pct is not None else None,
        "strategy_return_pct": summary["return_pct"],
        "fx_contribution_pct": (round(return_pct - summary["return_pct"], 2)
                                if return_pct is not None
                                and summary["return_pct"] is not None else None),
        "note": (f"Book runs in {book_ccy}, so its {book_ccy} return is the "
                 f"strategy's alone. The {account_ccy} figure converts at today's "
                 f"rate and therefore also moves with sterling."),
    }


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

    # Realised P&L per ticker, from the full archive. A row's unrealised figure
    # answers "what is this worth now"; realised answers "what has this
    # instrument actually banked", and they are different questions — an
    # instrument can be red today and net positive across the book's life.
    from ..paper import closed_archive
    realised_by_ticker = {}
    for closed_trade in closed_archive.merged(book):
        ticker = closed_trade.get("ticker")
        realised_by_ticker[ticker] = (realised_by_ticker.get(ticker, 0.0)
                                      + (closed_trade.get("pnl") or 0.0))

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
            "realised": round(realised_by_ticker.get(p["ticker"], 0.0), 2),
            # The exit levels fixed when the position opened. Both were always
            # stored and only the stop was ever shown, so the take-profit — the
            # number that decides when this trade is finished — was invisible.
            "target": p.get("target"),
            "target_pct": (round((p["target"] / p["entry_price"] - 1) * 100
                                 * (1 if p.get("direction") == "long" else -1), 2)
                           if p.get("target") and p.get("entry_price") else None),
            "stop_pct": (round((p["stop"] / p["entry_price"] - 1) * 100
                               * (1 if p.get("direction") == "long" else -1), 2)
                         if p.get("stop") and p.get("entry_price") else None),
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
        "realised_total": round(sum(realised_by_ticker.values()), 2),
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


@app.get("/api/paper/live")
def api_paper_live():
    """The book repriced to where the market is now. Persists nothing.

    Deliberately a separate endpoint from /api/paper: the book's own marks are
    the record of what the rules achieved and must not move when a page
    refreshes, while this answers the different question of what the positions
    are worth at this moment.
    """
    from ..paper import live
    from ..paper.book import Book

    book = Book.load()
    if not book.started:
        return jsonify({"started": False, "available": False, "positions": []})
    force = request.args.get("force") in ("1", "true", "yes")
    out = live.snapshot(book, load_config(), force=force)
    out["started"] = True
    return jsonify(out)


@app.get("/api/paper/journal")
def api_paper_journal():
    """Why each closed trade was taken, and why it won or lost.

    Everything here is derived from what the strategy recorded BEFORE the
    outcome was known. No language model is involved: a narrative written after
    seeing the result will always find a reason the result was foreseeable,
    which is the bias this record exists to defend against.
    """
    from ..paper import intraday_book, tradelog
    from ..paper.book import Book

    # Two books now, and they answer different questions. `?book=intraday` is
    # the flat-by-close record; the default is the daily one. Kept as one
    # endpoint because the record's SHAPE is identical — what was opened, why,
    # and what it did — and duplicating the reporting would let the two drift.
    which = (request.args.get("book") or "daily").lower()
    book = (Book.load(intraday_book.BOOK_PATH) if which == "intraday"
            else Book.load())
    if not book.started:
        return jsonify({"started": False, "book": which,
                        "entries": [], "daily": []})

    out = tradelog.report(book)
    out["started"] = True
    out["book"] = which
    out["summary"] = book.summary()
    # Newest day first — the question is almost always "what happened today".
    out["daily"] = book.daily[::-1]
    return jsonify(out)


@app.get("/api/paper/archives")
def api_paper_archives():
    """Books retired by `run.py paper --reset`, newest first.

    A reset archives rather than deletes precisely so a track record cannot be
    quietly restarted after a bad run. That guarantee is only worth anything if
    the old books are visible — an archive nobody can open is indistinguishable
    from a deletion.
    """
    import glob
    import json as jsonlib

    from ..paper.book import BOOK_PATH, Book

    pattern = f"{BOOK_PATH.rsplit('.json', 1)[0]}-archived-*.json"
    out = []
    for path in sorted(glob.glob(pattern), reverse=True):
        try:
            with open(path) as handle:
                book = Book(jsonlib.load(handle))
            summary = book.summary()
            out.append({
                "file": os.path.basename(path),
                "archived_at": os.path.basename(path).split("-archived-")[-1][:-5],
                "started_at": summary.get("started_at"),
                "days": summary.get("days"),
                "starting_equity": summary.get("starting_equity"),
                "equity": summary.get("equity"),
                "return_pct": summary.get("return_pct"),
                "open_positions": summary.get("open_positions"),
                "closed_trades": summary.get("closed_trades"),
                "win_rate_pct": summary.get("win_rate_pct"),
                "max_drawdown_pct": summary.get("max_drawdown_pct"),
                "base_currency": summary.get("base_currency"),
                "price_sources": book.price_sources(),
            })
        except Exception as exc:
            # A corrupt archive is itself worth showing, rather than vanishing
            # from a list whose whole purpose is that nothing vanishes.
            out.append({"file": os.path.basename(path),
                        "error": f"{type(exc).__name__}: {exc}"})
    return jsonify({"archives": out, "count": len(out)})


@app.get("/api/paper/archives/<path:filename>")
def api_paper_archive_detail(filename):
    """One archived book in full — positions, closed trades, session log."""
    import json as jsonlib

    from ..paper.book import BOOK_PATH, Book

    # Only ever open a file matching the archive pattern inside the data
    # directory. `filename` arrives from the URL, so joining it straight onto a
    # path would let "../../.env" out of this endpoint.
    if not (filename.startswith("paper_book-archived-") and filename.endswith(".json")):
        return jsonify({"error": "not an archived book"}), 404
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "not an archived book"}), 404

    path = os.path.join(os.path.dirname(BOOK_PATH), filename)
    if not os.path.isfile(path):
        return jsonify({"error": f"{filename} does not exist"}), 404

    try:
        with open(path) as handle:
            book = Book(jsonlib.load(handle))
    except Exception as exc:
        return jsonify({"error": f"could not read {filename}: {exc}"}), 500

    return jsonify({
        "file": filename,
        "summary": book.summary(),
        "price_sources": book.price_sources(),
        "positions": book.positions,
        "closed": book.closed[::-1],
        "curve": book.curve,
        "sessions": book.sessions[::-1],
        "last_rebalance": book.last_rebalance,
    })


@app.get("/api/intraday")
def api_intraday():
    """The intraday book, today's working set, and how the loop is doing.

    Served separately from /api/paper because they are two different books with
    two different clocks. Collapsing them into one payload would invite the
    dashboard to add their equity together, and one is a holding book measured
    in weeks while the other is flat by every bell.
    """
    from ..core import market_clock
    from ..paper import intraday_book, intraday_watchlist
    from ..paper.book import Book
    from . import scheduler

    book = Book.load(intraday_book.BOOK_PATH)
    working = intraday_watchlist.load()
    cfg = intraday_book.settings(load_config())

    venues = {"US": market_clock_phase("US", cfg), "LSE": market_clock_phase("LSE", cfg)}

    payload = {
        "started": book.started,
        "loop": scheduler.intraday_status(),
        "phases": venues,
        "working_set": ({"size": working["size"], "symbols": working["symbols"],
                         "detail": working.get("detail") or [],
                         "age_hours": working.get("age_hours"),
                         "summary": intraday_watchlist.describe(working)}
                        if working else None),
        "delay_note": ("Bars are IBKR historical intraday data, roughly 10-15 "
                       "minutes behind. This account carries no streaming "
                       "subscription, so nothing here is a live trading result."),
    }
    if not book.started:
        payload["message"] = ("No intraday book yet. It opens on the first tick "
                              "with a market open.")
        return jsonify(payload)

    payload.update({
        "summary": book.summary(),
        "positions": [{**p, "held_minutes": _held_minutes(p)} for p in book.positions],
        "closed": book.closed[-40:][::-1],
        "daily": book.daily[::-1],
        "curve": book.curve,
        # The claim the whole engine rests on, checked rather than asserted.
        "flat": not book.positions,
        "overnight_risk": [p["ticker"] for p in book.positions
                           if not any(m["open"] for m in market_clock.summary().values())],
    })
    return jsonify(payload)


def market_clock_phase(venue, cfg):
    from ..core import market_clock as clock
    return {
        "phase": clock.session_phase(
            venue, entry_cutoff_minutes=cfg["no_new_entries_minutes_before_close"],
            flat_minutes=cfg["flat_by_minutes_before_close"]),
        "minutes_to_close": clock.minutes_to_close(venue),
        "open": clock.is_open(venue),
    }


def _held_minutes(position):
    from datetime import datetime, timezone
    opened = (position.get("meta") or {}).get("opened_at")
    if not opened:
        return None
    try:
        return round((datetime.now(timezone.utc)
                      - datetime.fromisoformat(opened)).total_seconds() / 60, 1)
    except (TypeError, ValueError):
        return None


@app.post("/api/intraday/tick")
def api_intraday_tick():
    """Run one pass now. `force` fetches even with every market closed."""
    from . import scheduler

    force = bool((request.get_json(silent=True) or {}).get("force"))
    ok, detail = scheduler.run_intraday_tick(force=force)
    return jsonify({"ok": ok, "detail": detail}), (200 if ok else 409)


@app.post("/api/intraday/prepare")
def api_intraday_prepare():
    """Choose today's working set. Minutes, not seconds — it walks the pool."""
    from ..paper import intraday_runner

    try:
        out = intraday_runner.prepare(load_config())
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    if out.get("error"):
        return jsonify(out), 400
    return jsonify(out)


@app.post("/api/intraday/close")
def api_intraday_close():
    """Go flat now, by hand. The same escape hatch the daily book has."""
    from ..paper import intraday_book, manual
    from ..paper.book import Book

    payload = request.get_json(silent=True) or {}
    tickers = payload.get("tickers") or []
    if not tickers and not payload.get("all"):
        return jsonify({"error": "Name at least one ticker, or pass all: true."}), 400

    book = Book.load(intraday_book.BOOK_PATH)
    if not book.started or not book.positions:
        return jsonify({"error": "The intraday book holds no open positions."}), 400

    config = load_config()
    try:
        result = (manual.close_all(book, config) if payload.get("all")
                  else manual.close_positions(book, tickers, config))
    except manual.RefusedClose as exc:
        return jsonify({"error": str(exc), "refused": True}), 409
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    if not result["closed"]:
        return jsonify({"error": "Nothing matched: "
                                 + ", ".join(result["missing"] or tickers)}), 404

    book.save(intraday_book.BOOK_PATH)
    result["summary"] = book.summary()
    return jsonify(result)


@app.get("/api/options/<path:ticker>")
def api_options(ticker):
    """One expiry's chain, with the volatility each strike implies.

    Slow and broker-bound, so it is fetched on demand from its own tab rather
    than folded into /api/state. A chain is dozens of market-data lines; a
    dashboard that pulled one every fifteen seconds would spend an account's
    entire quote budget on a page nobody was looking at.
    """
    from datetime import date, datetime

    from ..providers.base import ProviderUnavailable
    from ..providers.ibkr_provider import IBKRDataProvider
    from ..research import option_chain
    from ..risk import options as opt

    config = load_config()
    ticker = ticker.upper()
    wanted = (request.args.get("expiry") or "").replace("-", "") or None
    width = max(2, min(30, int(request.args.get("strikes") or 10)))

    # The rate is a MEASURED input. Wrong by a percentage point and every
    # implied vol on the page moves, so where it came from is shown.
    rate, rate_source = 0.04, "fallback — FRED unreachable"
    try:
        series = (pipeline.get_router(config).get_macro() or {}).get("series") or {}
        raw = (series.get("three_month_yield") or {}).get("value")
        if raw is not None:
            rate, rate_source = float(raw) / 100.0, "FRED DTB3, 3-month bill"
    except Exception:
        pass

    provider = IBKRDataProvider(config)
    try:
        chain = provider.option_chain(ticker, max_expiries=10)
    except (ProviderUnavailable, Exception) as exc:
        return jsonify({"ticker": ticker,
                        "error": f"{type(exc).__name__}: {exc}"}), 200

    expiries = chain.get("expiries") or []
    if not expiries:
        return jsonify({"ticker": ticker, "error": "No listed expiries."}), 200
    expiry = wanted if wanted in expiries else expiries[0]

    try:
        spot = float(pipeline.get_router(config)
                     .get_prices(ticker, period="5d")["Close"].iloc[-1])
    except Exception as exc:
        return jsonify({"ticker": ticker,
                        "error": f"Could not price {ticker}: {exc}"}), 200

    strikes = sorted(sorted(chain["strikes"],
                            key=lambda k: abs(k - spot))[:width * 2])
    days = (datetime.strptime(expiry, "%Y%m%d").date() - date.today()).days
    years = opt.years_to_expiry(days)

    head = {"ticker": ticker, "spot": round(spot, 4), "expiry": expiry,
            "expiries": expiries, "days": days,
            "trading_class": chain.get("trading_class"),
            "chains_offered": chain.get("chains_offered"),
            "rate": rate, "rate_source": rate_source,
            "straddles_spot": bool(strikes and min(strikes) <= spot <= max(strikes))}

    if years <= 0:
        return jsonify({**head, "error": "That expiry is today or past."}), 200

    try:
        quotes = provider.option_quotes(
            ticker, expiry, strikes,
            multiplier=chain.get("multiplier") or "100",
            trading_class=chain.get("trading_class"))
    except Exception as exc:
        return jsonify({**head, "error": f"{type(exc).__name__}: {exc}"}), 200

    built = option_chain.build(quotes["rows"], spot, years, rate)
    return jsonify({**head, "quote_note": quotes.get("note"),
                    "forward": built["forward"], "carry": built["carry"],
                    "carry_source": built["carry_source"],
                    "surface": built["surface"], "rows": built["rows"],
                    "parity_violations": option_chain.parity_violations(
                        built["rows"], spot, years, rate, built["carry"])})


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
    with _lock:
        if _state["paper_running"]:
            return jsonify({"error": "A session is already running."}), 409
        _state["paper_running"] = True
    try:
        result = paper_session.run(rebalance_override=True if force else None)
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        with _lock:
            _state["paper_running"] = False
    return jsonify({k: result.get(k) for k in
                    ("date", "rebalanced", "opened", "closed", "skipped",
                     "notes", "universe", "price_source", "summary")})


@app.post("/api/paper/close")
def api_paper_close():
    """Close positions by hand — the exit the rules cannot decide for you.

    Two shapes, one endpoint:

        {"tickers": ["GE", "RTX"]}   close these
        {"all": true}                go flat

    `all` is required to be explicit rather than inferred from an empty ticker
    list, because "close everything" and "the UI sent me nothing" must never be
    the same request.

    Refused while a session is running. A session holds the book in memory and
    saves it at the end, so a manual close landing mid-run would be silently
    overwritten by the session's own save — the position would reappear, closed
    trade and all, with no error anywhere.
    """
    from ..paper import manual
    from ..paper.book import Book

    payload = request.get_json(silent=True) or {}
    tickers = payload.get("tickers") or []
    close_everything = bool(payload.get("all"))

    if not tickers and not close_everything:
        return jsonify({"error": "Name at least one ticker, or pass all: true."}), 400

    from . import scheduler

    with _lock:
        busy = _state["paper_running"]
    busy = busy or scheduler.status().get("running")
    if busy:
        return jsonify({"error": "A session is running. Closing a position now "
                                 "would be overwritten when it saves the book. "
                                 "Try again in a moment."}), 409

    book = Book.load()
    if not book.started:
        return jsonify({"error": "There is no paper book to close anything in."}), 400
    if not book.positions:
        return jsonify({"error": "The book holds no open positions."}), 400

    try:
        result = (manual.close_all(book, load_config()) if close_everything
                  else manual.close_positions(book, tickers, load_config()))
    except manual.RefusedClose as exc:
        # A refusal is not an error in the code; it is the code working.
        return jsonify({"error": str(exc), "refused": True}), 409
    except Exception as exc:
        # Nothing is saved on the way out, so a failure leaves the book exactly
        # as it was rather than half-closed.
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    if not result["closed"]:
        return jsonify({"error": "Nothing matched: "
                                 + ", ".join(result["missing"] or tickers)}), 404

    book.save()
    result["summary"] = book.summary()
    return jsonify(result)


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


def _attach_verdicts(ideas, config):
    """Give every journalled idea its one-word answer.

    Free: the stored payload already holds the thesis, gate, plan and strategy
    idea, so this is dict arithmetic over data the journal fetched once. No
    price call and no model call, which is what makes it affordable to show on
    every row rather than one ticker at a time.

    News is absent here — it is not persisted with the idea — so the panel
    reads pros and cons from the stored thesis and leaves headlines to
    /api/verdict/<ticker>, which re-runs the pipeline live.
    """
    from ..research import verdict as verdict_module

    for idea in ideas:
        payload = idea.get("payload") or {}
        try:
            idea["judgment"] = verdict_module.for_idea({
                "ticker": idea.get("ticker"),
                "thesis": payload.get("thesis"),
                "gate": payload.get("gate"),
                "plan": payload.get("plan"),
                "strategy_idea": payload.get("strategy_idea"),
                "snapshot": payload.get("snapshot"),
                "context": None,
            }, config)
        except Exception as exc:
            # A malformed row must not take the whole dashboard down with it.
            idea["judgment"] = {"action": "AVOID", "headline": "Could not judge this idea.",
                                "because": [f"{type(exc).__name__}: {exc}"],
                                "pros": [], "cons": [], "news": [], "confidence": "low"}
    return ideas


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

    from ..core import market_clock
    from ..paper.book import Book

    # The paper book's own equity, served alongside the config figure.
    #
    # These are different things and the dashboard conflated them. The card
    # showed account.portfolio_value — a fixed number in config.yaml used to
    # size risk — while the book that had actually traded sat elsewhere with a
    # different balance. So a profitable day left the headline reading exactly
    # £1,000,000, which looks like a page that has stopped updating.
    book = Book.load()
    paper_summary = None
    if book.started:
        summary = book.summary()
        today_row = book.daily[-1] if book.daily else None
        paper_summary = {
            "reporting": _reporting_currency(book, summary, config),
            "equity": summary["equity"],
            "starting_equity": summary["starting_equity"],
            "return_pct": summary["return_pct"],
            "cash": summary["cash"],
            "open_positions": summary["open_positions"],
            "closed_trades": summary["closed_trades"],
            "base_currency": summary["base_currency"],
            "exposure_pct": summary["exposure_pct"],
            "as_of": book.as_of,
            "today": today_row,
        }

    with _lock:
        scan, scanning, error = _state["scan"], _state["scanning"], _state["error"]
        progress = dict(_state["progress"]) if _state["progress"] else None

    # Fall back to the snapshot on disk. A scan started from the button lives in
    # THIS process's memory; one started by cron runs in another process
    # entirely, so without this a scheduled scan filled the journal and left the
    # watchlist page reporting "not scanned yet" on every row.
    if scan is None:
        scan = pipeline.load_scan_snapshot()

    # Served so the page can stop polling when nothing can change. Prices only
    # move while a market trades; refreshing a closed book on a timer re-fetches
    # figures that are identical by definition, and does it forever.
    markets = market_clock.summary()
    return jsonify({
        "disclaimer": DISCLAIMER,
        "markets": markets,
        "paper": paper_summary,
        "any_market_open": any(m["open"] for m in markets.values()),
        "next_market_open": min(
            (m["next_open"] for m in markets.values() if m["next_open"]), default=None),
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
        "ideas": _attach_verdicts(_mark_stale(journal.list_ideas(), router), config),
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


@app.get("/api/schedule")
def api_schedule():
    """When the trading day next runs, and how the last runs went."""
    from . import scheduler
    return jsonify(scheduler.status())


@app.post("/api/schedule/run")
def api_schedule_run():
    """Run a scheduled job now. Places no orders."""
    from . import scheduler

    mode = (request.get_json(silent=True) or {}).get("mode", "both")
    if mode not in ("session", "scan", "both"):
        return jsonify({"error": "mode must be session, scan, or both"}), 400

    # Started on a thread so the request returns immediately: a full session
    # can take half an hour, which no browser will wait for.
    threading.Thread(target=scheduler.run_job, args=(mode,), daemon=True).start()
    return jsonify({"status": "started", "mode": mode})


def main(port=None):
    if port is None:
        port = load_config().get("server", {}).get("port", 5002)

    # Scheduling runs in this process because macOS refuses cron and launchd
    # any access to ~/Desktop, where the project lives. See scheduler.py.
    from . import scheduler
    scheduler.start()

    # The intraday loop is its own thread on its own clock: every bar while a
    # market is open, rather than four times a day. Switched on from config so
    # a machine that only wants the daily book does not open a broker socket
    # every five minutes all afternoon.
    config = load_config()
    intraday_cfg = config.get("intraday") or {}
    if intraday_cfg.get("enabled") and intraday_cfg.get("live_loop", True):
        bar_minutes = {"1 min": 1, "5 mins": 5, "15 mins": 15, "30 mins": 30,
                       "1 hour": 60}.get(intraday_cfg.get("bar_size"), 5)
        scheduler.start_intraday(bar_minutes)

    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
