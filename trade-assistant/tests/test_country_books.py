"""Country books: each its own currency, share list and strategies, and none of
it able to reach the main book."""
import copy

import pytest

from assistant.paper import country_books, screen

ROWS = [
    {"symbol": "HSBA.L", "country": "United Kingdom", "currency": "GBp"},
    {"symbol": "NESN.SW", "country": "Switzerland", "currency": "CHF"},
    {"symbol": "GLEN.L", "country": "Switzerland", "currency": "GBp"},
    {"symbol": "MC.PA", "country": "France", "currency": "EUR"},
    {"symbol": "ENI.MI", "country": "Italy", "currency": "EUR"},
    {"symbol": "SAN.MC", "country": "Spain", "currency": "EUR"},
    {"symbol": "TEL2-B.ST", "country": "Finland", "currency": "SEK"},
]

CONFIG = {
    "base_currency": "GBP",
    "strategies": {"ts_momentum": {"enabled": True, "stop_atr": 3.0},
                   "halloween": {"enabled": True}, "pead_drift": {"enabled": False}},
    "strategy_priority": ["band_reversion", "halloween", "ts_momentum", "pead_drift"],
    "paper": {"trading_currency": "USD", "screen": {"universe": "us_all", "min_price": 5.0}},
    "scanner": {"benchmark": "^GSPC"},
    "kelly": {"enabled": True, "fraction": 0.25},
    "country_books": {
        "enabled": True, "benchmark": "^STOXX", "starting_equity": 100000,
        "books": {
            "uk": {"currency": "GBP", "countries": ["United Kingdom"],
                   "strategies": ["halloween"]},
            "switzerland": {"currency": "CHF", "countries": ["Switzerland"],
                            "strategies": ["band_reversion", "low_volatility"]},
            "southern_europe": {"currency": "EUR", "countries": ["Italy", "Spain", "Portugal"],
                                "strategies": ["ts_momentum"]},
            "france": {"currency": "EUR", "countries": ["France"],
                       "strategies": ["ts_momentum"], "enabled": False},
        },
    },
}


def test_a_book_takes_only_its_countries_in_its_currency():
    spec = CONFIG["country_books"]["books"]
    assert country_books.symbols_for(spec["uk"], ROWS) == ["HSBA.L"]
    # A Swiss company listed in London quotes in pence: not in the CHF book.
    assert country_books.symbols_for(spec["switzerland"], ROWS) == ["NESN.SW"]
    assert country_books.symbols_for(spec["southern_europe"], ROWS) == ["ENI.MI", "SAN.MC"]


def test_only_the_books_strategies_are_switched_on():
    out = country_books.config_for("switzerland", CONFIG, rows=ROWS)
    on = {name for name, block in out["strategies"].items() if block.get("enabled")}
    assert on == {"band_reversion", "low_volatility"}
    assert out["strategy_priority"] == ["band_reversion", "low_volatility"]


def test_settings_other_than_enabled_survive_the_overlay():
    out = country_books.config_for("southern_europe", CONFIG, rows=ROWS)
    assert out["strategies"]["ts_momentum"] == {"enabled": True, "stop_atr": 3.0}


def test_the_book_trades_its_own_currency_and_list_with_no_shared_cache():
    out = country_books.config_for("uk", CONFIG, rows=ROWS)
    assert out["paper"]["trading_currency"] == "GBP"
    assert out["paper"]["screen"]["universe"] == ["HSBA.L"]
    assert out["paper"]["screen"]["include_macro"] is False
    assert out["paper"]["universe_history_cache_hours"] is None
    assert out["paper"]["starting_equity"] == 100000
    assert out["scanner"]["benchmark"] == "^STOXX"
    assert out["kelly"]["enabled"] is False


def test_the_main_config_is_not_modified():
    before = copy.deepcopy(CONFIG)
    country_books.config_for("uk", CONFIG, rows=ROWS)
    assert CONFIG == before


def test_an_unknown_strategy_is_refused():
    config = copy.deepcopy(CONFIG)
    config["country_books"]["books"]["uk"]["strategies"] = ["no_such_rule"]
    with pytest.raises(ValueError):
        country_books.config_for("uk", config, rows=ROWS)


def test_disabled_books_are_skipped():
    assert "france" not in country_books.enabled_books(CONFIG)
    off = dict(CONFIG, country_books={**CONFIG["country_books"], "enabled": False})
    assert country_books.enabled_books(off) == []


def test_each_book_has_its_own_files(tmp_path):
    assert country_books.book_path("uk", str(tmp_path)) != country_books.book_path(
        "france", str(tmp_path))
    assert country_books.ledger_path("uk", str(tmp_path)).endswith("uk_closed.jsonl")
    book = country_books.load_book("uk", str(tmp_path))
    assert book.ledger_path == country_books.ledger_path("uk", str(tmp_path))


def test_status_lists_unstarted_books(tmp_path):
    rows = country_books.status(CONFIG, books_dir=str(tmp_path))
    assert {r["book"] for r in rows} == {"uk", "switzerland", "southern_europe", "france"}
    assert not any(r["started"] for r in rows)


def test_an_explicit_universe_never_reads_or_writes_the_shared_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(screen, "load_cached", lambda: calls.append("load") or
                        {"symbols": ["AAPL"], "report": {}})
    monkeypatch.setattr(screen, "save_cached", lambda *a: calls.append("save"))
    monkeypatch.setattr(screen, "apply", lambda symbols, config, progress_cb=None:
                        (list(symbols), {"considered": len(symbols)}))
    config = country_books.config_for("uk", CONFIG, rows=ROWS)
    symbols, _, cached = screen.tradable_universe(config, router=None)
    assert symbols == ["HSBA.L"] and cached is False
    assert calls == []


def test_a_country_screen_ignores_rows_measured_for_other_runs(monkeypatch):
    """The 13 Oct bug: the shared resume file put US shares into the UK book."""
    from assistant.providers import bulk
    saved = []
    monkeypatch.setattr(screen, "load_partial", lambda: {
        "AAPL": {"price": 250.0, "dollar_volume": 9e9, "bars": 60}})
    monkeypatch.setattr(screen, "save_partial", lambda table: saved.append(table))
    monkeypatch.setattr(bulk, "liquidity_table", lambda symbols, **k: {
        s: {"price": 5.0, "dollar_volume": 5e7, "bars": 60} for s in symbols})
    config = country_books.config_for("uk", CONFIG, rows=ROWS)
    kept, report = screen.apply(["HSBA.L"], config)
    assert kept == ["HSBA.L"]
    assert report["priced"] == 1 and report["no_data"] == 0
    assert saved == []


def test_the_wide_screen_only_keeps_what_it_was_asked_about(monkeypatch):
    from assistant.providers import bulk
    monkeypatch.setattr(screen, "load_partial", lambda: {
        "AAPL": {"price": 250.0, "dollar_volume": 9e9, "bars": 60},
        "OLD": {"price": 50.0, "dollar_volume": 9e9, "bars": 60}})
    monkeypatch.setattr(screen, "save_partial", lambda table: None)
    monkeypatch.setattr(bulk, "liquidity_table", lambda symbols, **k: {})
    config = {"paper": {"screen": {"universe": "us_all"}}}
    kept, _ = screen.apply(["AAPL"], config)
    assert kept == ["AAPL"]


def test_european_books_price_from_yahoo_and_london_from_ibkr():
    config = copy.deepcopy(CONFIG)
    config["providers"] = {"ibkr": {"enabled": True, "port": 7497}}
    config["country_books"]["books"]["uk"]["prices"] = "ibkr"
    uk = country_books.config_for("uk", config, rows=ROWS)
    swiss = country_books.config_for("switzerland", config, rows=ROWS)
    assert uk["providers"]["ibkr"]["enabled"] is True
    assert swiss["providers"]["ibkr"] == {"enabled": False, "port": 7497}
    assert config["providers"]["ibkr"]["enabled"] is True
