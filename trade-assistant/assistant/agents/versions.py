"""The sub-versions: what each one is, how much money it runs, and running it.

A sub-version's configuration is built in three layers, and only three:

    1. the main version            config.yaml, every feature and default
    2. the market's facts          currency, share list, index, price feed
    3. this country's tuning       data/agents/<name>/tuning.json, empty at start

Layer 2 is the same kind of fact for every country, so on day one every
sub-version trades exactly the main version's rules on its own market. Only
layer 3 makes a country different, and only for that country.
"""
import copy
import csv
import os
import time

from ..core.config import DATA_DIR, PROJECT_ROOT, load_config
from ..paper.book import Book
from . import tuning

MAIN_BOOK = "main"      # the US version keeps the original book file and dashboard

# Yahoo's minor-unit spellings; a version's currency is always the major unit.
MAJOR = {"GBP": "GBP", "GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


def settings(config):
    return (config or {}).get("agents") or {}


def specs(config):
    return dict(settings(config).get("versions") or {})


def enabled_versions(config):
    if not settings(config).get("enabled"):
        return []
    return [n for n, s in specs(config).items() if (s or {}).get("enabled", True)]


def book_path(name, config, agents_dir=None):
    """None for the US version (the original paper book), else its own file."""
    if specs(config)[name].get("book") == MAIN_BOOK:
        return None
    return os.path.join(tuning.version_dir(name, agents_dir), "book.json")


def ledger_path(name, config, agents_dir=None):
    if specs(config)[name].get("book") == MAIN_BOOK:
        return None
    return os.path.join(tuning.version_dir(name, agents_dir), "closed.jsonl")


def load_book(name, config, agents_dir=None):
    path = book_path(name, config, agents_dir)
    if path is None:
        return Book.load()
    book = Book.load(path)
    book.ledger_path = ledger_path(name, config, agents_dir)
    return book


# --- money --------------------------------------------------------------------

def weights(config):
    """{name: share of the firm's capital}, normalised over enabled versions."""
    names = enabled_versions(config)
    raw = {n: float(specs(config)[n].get("weight") or 0.0) for n in names}
    total = sum(raw.values())
    return {n: (w / total if total else 0.0) for n, w in raw.items()}


def allocation(config):
    """{name: {"total", "daily", "intraday"}} in the account's currency."""
    capital = float(settings(config).get("total_capital")
                    or (config.get("account") or {}).get("portfolio_value") or 0.0)
    out = {}
    for name, share in weights(config).items():
        amount = capital * share
        intraday = specs(config)[name].get("intraday_share") or 0.0
        intraday = float(intraday) if specs(config)[name].get("intraday") else 0.0
        out[name] = {"total": amount, "intraday": amount * intraday,
                     "daily": amount * (1 - intraday)}
    return out


_FX = {}


def fx_rate(router, from_ccy, to_ccy, max_age=3600):
    """from -> to, cached for an hour. None when no rate can be found."""
    if from_ccy == to_ccy:
        return 1.0
    key = (from_ccy, to_ccy)
    hit = _FX.get(key)
    if hit and time.time() - hit[1] < max_age:
        return hit[0]
    from ..core import fx
    from ..paper.session import _rates_both_ways
    try:
        rate = fx.convert(1.0, from_ccy, to_ccy, _rates_both_ways(router, from_ccy, to_ccy))
    except Exception:
        return hit[0] if hit else None
    _FX[key] = (rate, time.time())
    return rate


# --- configuration --------------------------------------------------------------

def universe_rows(config):
    path = settings(config).get("universe_file") or "reports/universe-europe.csv"
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    with open(path) as handle:
        return list(csv.DictReader(handle))


def symbols_for(spec, rows):
    """Shares listed in the version's countries AND quoted in its currency.

    Both conditions: a Swiss company listed in London quotes in pence, and a
    Finnish one listed in Stockholm in kronor, and either would put a second
    currency into a one-currency cash balance.
    """
    countries = set(spec.get("countries") or ())
    currency = str(spec["currency"]).upper()
    return [row["symbol"] for row in rows
            if row.get("country") in countries
            and MAJOR.get(row.get("currency"), str(row.get("currency") or "").upper()) == currency]


def config_for(name, config, rows=None, agents_dir=None):
    """The main version, this market's facts, then this country's tuning."""
    spec = specs(config)[name]
    out = copy.deepcopy(config)
    money = allocation(config)[name]

    paper = dict(out.get("paper") or {})
    paper["trading_currency"] = str(spec["currency"]).upper()
    # The version's share of the firm, in the account's currency; the session
    # converts it once into the version's currency when its book starts.
    paper["starting_equity"] = round(money["daily"], 2)
    # A version's capital is set by its weight, never inherited from an
    # archived book, so every version starts from the figure the plan gives it.
    paper["carry_forward_equity"] = False
    if spec.get("countries"):
        rows = rows if rows is not None else universe_rows(config)
        screen = dict(paper.get("screen") or {})
        screen.update({
            "universe": symbols_for(spec, rows),
            "include_macro": False,        # FX pairs and futures: the US version
            "include_lse": False,
            # Already the largest companies in each country; the US $5 floor
            # would drop most of London, where large companies trade at £1-2.
            "min_price": 1.0,
            "min_dollar_volume": 1_000_000,
        })
        paper["screen"] = screen
        paper["universe_history_cache_hours"] = None   # never overwrite the US cache
    out["paper"] = paper

    if spec.get("benchmark"):
        out["scanner"] = {**(out.get("scanner") or {}), "benchmark": spec["benchmark"]}

    # Kelly sizes from the main study's out-of-sample edges, which are US
    # evidence. Only the US version may use them until a country has its own.
    if spec.get("countries"):
        out["kelly"] = {**(out.get("kelly") or {}), "enabled": False}

    if str(spec.get("prices") or "ibkr").lower() != "ibkr":
        providers = dict(out.get("providers") or {})
        providers["ibkr"] = {**(providers.get("ibkr") or {}), "enabled": False}
        out["providers"] = providers

    if spec.get("intraday"):
        out["intraday"] = {**(out.get("intraday") or {}),
                           "starting_equity_base": round(money["intraday"], 2)}

    return tuning.overlay(name, out, agents_dir)


def intraday_config(config, router=None):
    """The US version's configuration for the intraday engine, with its money
    converted once into the engine's trading currency."""
    for name in enabled_versions(config):
        if specs(config)[name].get("intraday"):
            out = config_for(name, config)
            block = dict(out.get("intraday") or {})
            base = config.get("base_currency", "USD")
            ccy = block.get("trading_currency", "USD")
            amount = block.get("starting_equity_base")
            if amount and router is not None:
                rate = fx_rate(router, base, ccy)
                if rate:
                    block["starting_equity"] = round(amount * rate, 2)
            out["intraday"] = block
            return out
    return config


# --- running --------------------------------------------------------------------

def _router_for(version_config):
    from ..providers.router import DataRouter
    return DataRouter(version_config)


def run_version(name, config=None, router=None, progress_cb=None, agents_dir=None):
    from ..paper import session

    config = config or load_config()
    if name not in specs(config):
        raise ValueError(f"no version called {name!r}; known: {', '.join(specs(config))}")
    version_config = config_for(name, config, agents_dir=agents_dir)
    path = book_path(name, config, agents_dir)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    router = router or _router_for(version_config)
    report = session.run(version_config, router=router, progress_cb=progress_cb,
                         book_path=path, ledger_path=ledger_path(name, config, agents_dir))
    report["version"] = name
    if not ((version_config.get("providers") or {}).get("ibkr") or {}).get("enabled"):
        report.setdefault("notes", []).insert(0, (
            "Priced from Yahoo, not Interactive Brokers: the account holds no "
            "market-data permission for this exchange."))
    return report


def run_all(config=None, progress_cb=None, agents_dir=None, only=None):
    """Every enabled version once, the US first. One failure stops nothing else."""
    config = config or load_config()
    names = enabled_versions(config)
    names.sort(key=lambda n: 0 if specs(config)[n].get("book") == MAIN_BOOK else 1)
    results = {}
    for name in names:
        if only and name not in only:
            continue
        try:
            results[name] = run_version(name, config, progress_cb=progress_cb,
                                        agents_dir=agents_dir)
        except Exception as exc:
            results[name] = {"version": name, "error": f"{type(exc).__name__}: {exc}"}
    return results


# --- the firm view --------------------------------------------------------------

def _positions(book):
    for position in book.positions:
        price = position.get("last_price") or position.get("entry_price") or 0.0
        value = abs(float(position.get("shares") or 0)) * float(price) * \
            float(position.get("multiplier") or 1.0)
        yield position, value


def firm(config=None, router=None, agents_dir=None):
    """Where all the money is: every version and the intraday engine, in the
    account's currency, with what each holds by strategy."""
    config = config or load_config()
    base = config.get("base_currency", "GBP")
    if router is None:
        from .. import pipeline
        router = pipeline.get_router(config)
    money = allocation(config)
    rows, by_strategy, by_country = [], {}, {}
    total = {"equity": 0.0, "invested": 0.0, "cash": 0.0, "allocated": 0.0}
    missing_fx = []

    def add(row, book, ccy, rate, allocated):
        equity = book.equity() if book.started else None
        if not book.started:
            # Money allocated to an engine that has not traded yet is still the
            # firm's: it counts as cash. Leaving it out of the value while the
            # capital includes it would show a loss that never happened.
            row.update({"equity_base": round(allocated, 2), "invested_base": 0.0,
                        "cash_base": round(allocated, 2)})
            total["equity"] += allocated
            total["cash"] += allocated
            by_country[row["label"].split(" · ")[0]] = by_country.get(row["label"].split(" · ")[0], 0.0) + allocated
        elif rate:
            invested = sum(v for _, v in _positions(book))
            row.update({"equity_base": round(equity * rate, 2),
                        "invested_base": round(invested * rate, 2),
                        "cash_base": round((book.cash or 0.0) * rate, 2)})
            total["equity"] += equity * rate
            total["invested"] += invested * rate
            total["cash"] += (book.cash or 0.0) * rate
            by_country[row["label"].split(" · ")[0]] = by_country.get(row["label"].split(" · ")[0], 0.0) + equity * rate
            for position, value in _positions(book):
                key = position.get("strategy") or "unknown"
                by_strategy[key] = by_strategy.get(key, 0.0) + value * rate
        total["allocated"] += allocated
        return equity

    for name in enabled_versions(config):
        spec = specs(config)[name]
        ccy = str(spec["currency"]).upper()
        rate = fx_rate(router, ccy, base)
        if rate is None:
            missing_fx.append(ccy)
        book = load_book(name, config, agents_dir)
        summary = book.summary() if book.started else {}
        row = {"version": name, "label": spec.get("label", name), "currency": ccy,
               "engine": "daily", "weight": round(weights(config)[name], 4),
               "allocated_base": round(money[name]["daily"], 2),
               "started": book.started,
               "equity": summary.get("equity"), "starting_equity": summary.get("starting_equity"),
               "return_pct": (round((summary["equity"] / summary["starting_equity"] - 1) * 100, 2)
                              if summary.get("starting_equity") else None),
               "open_positions": summary.get("open_positions"),
               "closed_trades": summary.get("closed_trades"),
               "pending": len(book.pending) if book.started else 0,
               "as_of": book.as_of,
               "tuned": tuning.differences(name, config, agents_dir),
               "changes": tuning.history(name, agents_dir, limit=20)[::-1],
               "journal": journal(name, agents_dir, limit=5)}
        add(row, book, ccy, rate, money[name]["daily"])
        rows.append(row)

        if spec.get("intraday"):
            from ..paper import intraday_book
            ibook = intraday_book.load_book()
            iccy = (config.get("intraday") or {}).get("trading_currency", "USD")
            irate = fx_rate(router, iccy, base)
            isum = ibook.summary() if ibook.started else {}
            irow = {"version": name, "label": f"{spec.get('label', name)} · intraday",
                    "currency": iccy, "engine": "intraday",
                    "allocated_base": round(money[name]["intraday"], 2),
                    "started": ibook.started, "equity": isum.get("equity"),
                    "starting_equity": isum.get("starting_equity"),
                    "return_pct": (round((isum["equity"] / isum["starting_equity"] - 1) * 100, 2)
                                   if isum.get("starting_equity") else None),
                    "open_positions": isum.get("open_positions"),
                    "closed_trades": isum.get("closed_trades"), "pending": 0,
                    "as_of": ibook.as_of, "tuned": [], "changes": [], "journal": []}
            add(irow, ibook, iccy, irate, money[name]["intraday"])
            rows.append(irow)

    def shares(mapping):
        whole = sum(mapping.values()) or 1.0
        return [{"name": k, "value": round(v, 2), "share": round(v / whole, 4)}
                for k, v in sorted(mapping.items(), key=lambda kv: -kv[1])]

    return {"base_currency": base,
            "total": {k: round(v, 2) for k, v in total.items()},
            "return_pct": (round((total["equity"] / total["allocated"] - 1) * 100, 2)
                           if total["allocated"] and total["equity"] else None),
            "versions": rows, "by_country": shares(by_country),
            "by_strategy": shares(by_strategy), "missing_fx": sorted(set(missing_fx))}


def journal(name, agents_dir=None, limit=None):
    """The learning agent's daily notes for one version, newest first."""
    import json
    path = os.path.join(tuning.version_dir(name, agents_dir), "journal.jsonl")
    rows = []
    try:
        with open(path) as handle:
            for line in handle:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    rows = rows[::-1]
    return rows[:limit] if limit else rows
