"""One paper book per country, each in its own currency.

The main book trades US shares in dollars. These books trade the European
markets the country test (reports/COUNTRY-TEST.md) measured, each with only the
strategies that passed in that country. They reuse the main paper session
unchanged: what differs is handed to it as configuration — the share list, the
currency, which strategies are on, the benchmark — plus a book file and a
closed-trade ledger of its own.

Nothing here can reach the main book. Every path is under data/books/, the
universe is an explicit list (which screen.py keeps off the shared universe
cache), and the history cache is switched off for these runs so the main book's
38-minute download is never overwritten by a 50-name one.
"""
import copy
import csv
import os

from ..core.config import DATA_DIR, PROJECT_ROOT, load_config
from .book import Book

BOOKS_DIR = os.path.join(DATA_DIR, "books")

# Yahoo's minor-unit spellings; the book's currency is always the major unit.
MAJOR = {"GBP": "GBP", "GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


def settings(config):
    return (config or {}).get("country_books") or {}


def specs(config):
    """{name: spec} for every book in config, enabled or not."""
    return dict(settings(config).get("books") or {})


def book_path(name, books_dir=None):
    return os.path.join(books_dir or BOOKS_DIR, f"{name}.json")


def ledger_path(name, books_dir=None):
    return os.path.join(books_dir or BOOKS_DIR, f"{name}_closed.jsonl")


def universe_rows(config):
    path = settings(config).get("universe_file") or "reports/universe-europe.csv"
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    with open(path) as handle:
        return list(csv.DictReader(handle))


def symbols_for(spec, rows):
    """The book's shares: listed in its countries AND quoted in its currency.

    Both conditions, because neither alone is enough: a Swiss company listed in
    London quotes in pence, and a Finnish one listed in Stockholm in kronor.
    Either would put a second currency into a one-currency cash balance.
    """
    countries = set(spec.get("countries") or ())
    currency = str(spec["currency"]).upper()
    out = []
    for row in rows:
        quoted = MAJOR.get(row.get("currency"), str(row.get("currency") or "").upper())
        if row.get("country") in countries and quoted == currency:
            out.append(row["symbol"])
    return out


def config_for(name, config, rows=None):
    """The main config with this book's differences laid over it."""
    spec = specs(config)[name]
    books = settings(config)
    rows = rows if rows is not None else universe_rows(config)
    chosen = [str(s) for s in spec.get("strategies") or ()]

    from ..strategies import registry
    known = {s.name for s in registry.all_strategies()}
    unknown = sorted(set(chosen) - known)
    if unknown:
        raise ValueError(f"country book {name}: unknown strategies {unknown}")

    out = copy.deepcopy(config)
    strategies = out.setdefault("strategies", {})
    for strategy in known:
        block = dict(strategies.get(strategy) or {})
        block["enabled"] = strategy in chosen
        strategies[strategy] = block
    out["strategy_priority"] = (
        [s for s in (config.get("strategy_priority") or []) if s in chosen]
        + [s for s in chosen if s not in (config.get("strategy_priority") or [])])

    paper = dict(out.get("paper") or {})
    paper["trading_currency"] = str(spec["currency"]).upper()
    paper["starting_equity"] = spec.get("starting_equity",
                                        books.get("starting_equity", 100000))
    paper["universe_history_cache_hours"] = None
    screen = dict(paper.get("screen") or {})
    screen.update({
        "universe": symbols_for(spec, rows),
        # Shares only. FX pairs and futures belong to the main book.
        "include_macro": False,
        "include_lse": False,
        # The list is already the largest companies in each country, and the
        # main book's $5 floor would drop most of London, where large companies
        # routinely trade at a pound or two.
        "min_price": 1.0,
        "min_dollar_volume": 1_000_000,
    })
    paper["screen"] = screen
    out["paper"] = paper

    scanner = dict(out.get("scanner") or {})
    scanner["benchmark"] = spec.get("benchmark") or books.get("benchmark") or "^STOXX"
    out["scanner"] = scanner
    out["kelly"] = {**(out.get("kelly") or {}), "enabled": False}

    # Which feed prices this book. "ibkr" only where the account holds that
    # exchange's market-data permission (London does; on 13 October 2026 Paris,
    # Xetra, Milan, Madrid, Amsterdam, Brussels, SIX and Stockholm returned
    # nothing). Elsewhere Yahoo, which the session report names on every run.
    prices = str(spec.get("prices") or books.get("prices") or "yfinance").lower()
    if prices != "ibkr":
        providers = dict(out.get("providers") or {})
        providers["ibkr"] = {**(providers.get("ibkr") or {}), "enabled": False}
        out["providers"] = providers
    return out


def enabled_books(config):
    if not settings(config).get("enabled"):
        return []
    return [name for name, spec in specs(config).items()
            if (spec or {}).get("enabled", True)]


def load_book(name, books_dir=None):
    """A country book attached to its own ledger. Read it only through here."""
    book = Book.load(book_path(name, books_dir))
    book.ledger_path = ledger_path(name, books_dir)
    return book


def run_book(name, config=None, router=None, progress_cb=None, books_dir=None):
    from . import session

    config = config or load_config()
    if name not in specs(config):
        raise ValueError(f"no country book called {name!r}; "
                         f"known: {', '.join(specs(config)) or 'none'}")
    os.makedirs(books_dir or BOOKS_DIR, exist_ok=True)
    book_config = config_for(name, config)
    # Its own router, not the process-wide one: that one was built from the
    # main config and would price this book from the main book's feed.
    if router is None:
        from ..providers.router import DataRouter
        router = DataRouter(book_config)
    report = session.run(book_config, router=router, progress_cb=progress_cb,
                         book_path=book_path(name, books_dir),
                         ledger_path=ledger_path(name, books_dir))
    report["book"] = name
    if not ((book_config.get("providers") or {}).get("ibkr") or {}).get("enabled"):
        report.setdefault("notes", []).insert(0, (
            "Priced from Yahoo, not Interactive Brokers: the account holds no "
            "market-data permission for this exchange. Unlicensed data, fine for "
            "a paper experiment, not for anything shown as a licensed result."))
    return report


def run_all(config=None, router=None, progress_cb=None, books_dir=None, only=None):
    """Run each enabled book once. One book failing does not stop the others."""
    config = config or load_config()
    results = {}
    for name in enabled_books(config):
        if only and name not in only:
            continue
        try:
            results[name] = run_book(name, config, router=router,
                                     progress_cb=progress_cb, books_dir=books_dir)
        except Exception as exc:
            results[name] = {"book": name, "error": f"{type(exc).__name__}: {exc}"}
    return results


def status(config=None, books_dir=None):
    """Plain summary rows for the dashboard and the command line."""
    config = config or load_config()
    rows = []
    for name, spec in specs(config).items():
        row = {"book": name, "label": spec.get("label", name),
               "currency": spec.get("currency"),
               "strategies": list(spec.get("strategies") or ()),
               "enabled": name in enabled_books(config), "started": False}
        book = load_book(name, books_dir)
        if book.started:
            summary = book.summary()
            row.update({
                "started": True,
                "started_at": summary.get("started_at"),
                "starting_equity": summary.get("starting_equity"),
                "equity": summary.get("equity"),
                "return_pct": (round((summary["equity"] / summary["starting_equity"] - 1) * 100, 2)
                               if summary.get("starting_equity") else None),
                "open_positions": summary.get("open_positions"),
                "closed_trades": summary.get("closed_trades"),
                "pending": len(book.pending),
                "as_of": book.as_of,
            })
        rows.append(row)
    return rows
