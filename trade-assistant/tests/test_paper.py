"""Paper trader — book state, universe screening, and the session loop.

The negative assertions matter most here, and they are of a different kind to
the backtest's. A backtest that is wrong reports a bad number. A paper trader
that is wrong CORRUPTS STATE — it double-buys, loses a position, or resets the
balance — and every subsequent session inherits the damage. So the tests
concentrate on the things that must survive being run twice, interrupted, or
run on a day when nothing should happen.
"""
import json
import os

import pandas as pd
import pytest

from assistant.paper import screen, session
from assistant.paper.book import Book


@pytest.fixture(autouse=True)
def isolated_caches(tmp_path, monkeypatch):
    """Keep every test out of the real data directory.

    Both of the screen's caches live in DATA_DIR by default. Without this the
    suite writes into the user's actual universe and partial-liquidity files —
    and worse, tests leak into each other: one test's 537 synthetic survivors
    become the next test's resumed measurements, which is exactly how the cap
    test started reporting 1,447 capped symbols out of two.
    """
    monkeypatch.setattr(screen, "CACHE_PATH", str(tmp_path / "universe.json"))
    monkeypatch.setattr(screen, "PARTIAL_PATH", str(tmp_path / "partial.json"))


@pytest.fixture
def book_path(tmp_path):
    return str(tmp_path / "book.json")


def _started(path, equity=100_000):
    book = Book.load(path)
    book.start(equity, "GBP", "2026-01-05")
    book.save(path)
    return book


# --- the book ----------------------------------------------------------------

def test_a_fresh_book_is_empty_rather_than_an_error():
    book = Book.load("/nonexistent/path/book.json")
    assert not book.started and book.positions == [] and book.equity() == 0.0


def test_a_book_survives_a_round_trip(book_path):
    book = _started(book_path)
    book.open_position(ticker="AAPL", direction="long", shares=10, price=100.0,
                       stop=90.0, target=130.0, strategy="xs_momentum",
                       regime="TRENDING_UP", date="2026-01-05")
    book.save(book_path)

    reloaded = Book.load(book_path)
    assert reloaded.started
    assert len(reloaded.positions) == 1
    assert reloaded.cash == pytest.approx(99_000.0)
    assert reloaded.equity() == pytest.approx(100_000.0)


def test_an_interrupted_save_does_not_destroy_the_previous_book(book_path, monkeypatch):
    """The book is the only record of what the trader has done. A crash
    mid-write must not leave a truncated file that reads as a fresh account
    with the starting balance restored — that silently erases the experiment."""
    book = _started(book_path)
    book.open_position(ticker="MSFT", direction="long", shares=5, price=200.0,
                       stop=180.0, target=260.0, strategy="ts_momentum",
                       regime="SIDEWAYS", date="2026-01-05")
    book.save(book_path)

    real_replace = os.replace

    def explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        book.save(book_path)
    monkeypatch.setattr(os, "replace", real_replace)

    survivor = Book.load(book_path)
    assert len(survivor.positions) == 1
    assert survivor.cash == pytest.approx(99_000.0)
    with open(book_path) as handle:
        json.load(handle)          # still valid JSON, not a truncated stub


def test_closing_a_position_books_cash_and_an_r_multiple(book_path):
    book = _started(book_path)
    book.open_position(ticker="KO", direction="long", shares=10, price=100.0,
                       stop=90.0, target=130.0, strategy="low_beta",
                       regime="SIDEWAYS", date="2026-01-05")
    position = book.positions[0]
    book.close_position(position, 130.0, "2026-02-02", "target")

    assert book.positions == []
    assert book.cash == pytest.approx(100_300.0)
    assert book.closed[0]["pnl"] == pytest.approx(300.0)
    assert book.closed[0]["r_multiple"] == pytest.approx(3.0)   # risked 10/share


def test_the_curve_keeps_one_row_per_day(book_path):
    book = _started(book_path)
    book.mark("2026-01-06")
    book.mark("2026-01-06")
    book.mark("2026-01-07")
    assert [row["date"] for row in book.curve] == ["2026-01-05", "2026-01-06", "2026-01-07"]


def test_drawdown_is_measured_from_the_peak_not_the_start(book_path):
    book = _started(book_path)
    book.curve = [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 200.0},
                  {"date": "d3", "equity": 150.0}]
    assert book.summary()["max_drawdown_pct"] == pytest.approx(25.0)


# --- the universe screen -----------------------------------------------------

def test_the_screen_rejects_what_cannot_honestly_be_traded(monkeypatch):
    """28,192 symbols is not a universe until the untradeable ones are gone. A
    momentum ranking over the raw list puts a sub-dollar shell that tripled on
    one news day at the top, every single month."""
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda symbols, **kw: {
        "GOOD":  {"price": 50.0, "dollar_volume": 90e6, "bars": 60},
        "CHEAP": {"price": 0.40, "dollar_volume": 90e6, "bars": 60},
        "THIN":  {"price": 50.0, "dollar_volume": 1_000.0, "bars": 60},
        "NEW":   {"price": 50.0, "dollar_volume": 90e6, "bars": 5},
    })
    kept, report = screen.apply(["GOOD", "CHEAP", "THIN", "NEW", "DEAD"], {})
    assert kept == ["GOOD"]
    assert report["rejected_price"] == 1
    assert report["rejected_volume"] == 1
    assert report["rejected_history"] == 1
    assert report["no_data"] == 1


def test_the_cap_keeps_the_most_liquid_names_not_the_alphabet(monkeypatch):
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda symbols, **kw: {
        "AAA": {"price": 50.0, "dollar_volume": 10e6, "bars": 60},
        "ZZZ": {"price": 50.0, "dollar_volume": 900e6, "bars": 60},
    })
    kept, report = screen.apply(["AAA", "ZZZ"], {"paper": {"screen": {"max_symbols": 1}}})
    assert kept == ["ZZZ"]
    assert report["capped"] == 1


def test_a_throttled_screen_refuses_to_cache_itself(monkeypatch, tmp_path):
    """The bug this pins, exactly as it happened: a full 28,192-symbol run
    returned 537 "tradable" names with NVDA, MSFT, SPY, QQQ, META and GOOGL all
    missing and 88% marked "no data". yfinance had rate-limited partway through
    and whole chunks came back empty. Nothing noticed, so the wreckage was
    cached and every later session would have traded an arbitrary slice of the
    market believing it was the market.
    """
    survivors = {f"JUNK{i}": {"price": 50.0, "dollar_volume": 90e6, "bars": 60}
                 for i in range(537)}
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda s, **kw: survivors)
    monkeypatch.setattr(screen, "candidate_symbols",
                        lambda config, router: [f"S{i}" for i in range(28_192)])

    with pytest.raises(screen.bulk.ThrottleSuspected) as caught:
        screen.tradable_universe({}, None, force_refresh=True)
    assert "rate-limited" in str(caught.value)
    assert not os.path.exists(screen.CACHE_PATH), "a bad universe must not be cached"


def test_a_healthy_wide_screen_is_accepted(monkeypatch, tmp_path):
    table = {name: {"price": 100.0, "dollar_volume": 900e6, "bars": 60}
             for name in screen.SANITY_CANARIES}
    table.update({f"OK{i}": {"price": 50.0, "dollar_volume": 20e6, "bars": 60}
                  for i in range(900)})
    monkeypatch.setattr(screen.bulk, "liquidity_table", lambda s, **kw: table)
    monkeypatch.setattr(screen, "candidate_symbols",
                        lambda config, router: [f"S{i}" for i in range(2_000)])

    symbols, report, cached = screen.tradable_universe({}, None, force_refresh=True)
    assert cached is False
    assert "NVDA" in symbols
    assert os.path.exists(screen.CACHE_PATH)


def test_a_throttled_run_keeps_the_measurements_it_already_took(monkeypatch):
    """Rate limiting is not an edge case at this width — it is what happens.
    Losing an hour of successful measurements every time the limiter trips
    makes the screen impossible to finish rather than merely slow."""
    def die_after_measuring(symbols, period=None, progress_cb=None, on_batch=None):
        if on_batch:
            on_batch({"AAPL": {"price": 200.0, "dollar_volume": 90e6, "bars": 60}})
        raise screen.bulk.ThrottleSuspected("limiter tripped")

    monkeypatch.setattr(screen.bulk, "liquidity_table", die_after_measuring)
    with pytest.raises(screen.bulk.ThrottleSuspected):
        screen.apply(["AAPL", "MSFT"], {})

    assert screen.load_partial()["AAPL"]["price"] == 200.0


def test_a_resumed_run_does_not_remeasure_what_it_already_has(monkeypatch):
    screen.save_partial({"AAPL": {"price": 200.0, "dollar_volume": 90e6, "bars": 60}})
    asked = {}

    def record(symbols, **kw):
        asked["symbols"] = list(symbols)
        return {"MSFT": {"price": 400.0, "dollar_volume": 90e6, "bars": 60}}

    monkeypatch.setattr(screen.bulk, "liquidity_table", record)
    kept, report = screen.apply(["AAPL", "MSFT"], {})

    assert asked["symbols"] == ["MSFT"], "already-measured symbols must be skipped"
    assert set(kept) == {"AAPL", "MSFT"}
    assert report["resumed_from"] == 1 and report["measured_now"] == 1


def test_the_over_the_counter_tail_is_dropped_before_any_request():
    """17,608 of the 30,935 symbols Finnhub lists for "US" are OOTC. Screening
    them spends the whole rate limit on foreign shells nobody can trade, which
    is what exhausted it and lost NVDA, MSFT and SPY from the universe."""
    class FakeRouter:
        @staticmethod
        def universe_rows():
            return [{"symbol": "AAPL", "type": "Common Stock", "mic": "XNAS"},
                    {"symbol": "SPY", "type": "ETP", "mic": "ARCX"},
                    {"symbol": "YMECF", "type": "Common Stock", "mic": "OOTC"},
                    {"symbol": "ZAUIF", "type": "Common Stock", "mic": "OOTC"}]

    assert screen.candidate_symbols({}, FakeRouter()) == ["AAPL", "SPY"]

    keep_all = {"paper": {"screen": {"exclude_exchanges": []}}}
    assert len(screen.candidate_symbols(keep_all, FakeRouter())) == 4


def test_the_canary_check_does_not_fire_on_a_deliberately_small_universe(monkeypatch):
    """A hand-picked 20-name universe has no reason to contain SPY. Failing it
    there would make the guard useless noise."""
    report = {"considered": 20, "no_data": 0, "kept": 20}
    assert screen.sanity_check(["AAPL", "KO"], report, {}) is None


def test_turning_the_screen_off_says_so_out_loud():
    kept, report = screen.apply(["ANYTHING"], {"paper": {"screen": {"enabled": False}}})
    assert kept == ["ANYTHING"]
    assert "could not have traded" in screen.describe(report)


# --- the session -------------------------------------------------------------

def test_the_session_hands_its_calendar_to_the_strategies():
    """Two clocks that disagree. A strategy decides it is a rebalance bar from
    its own price frame; the session decides from the book. Left alone the
    session declares a rebalance day and every strategy refuses, because today
    is the 9th and the last bar is a Friday mid-month — nothing ever trades.
    """
    from assistant.strategies import registry

    opened = session._hand_the_calendar_to_the_session({"strategies": {}})
    calendar_strategies = [s for s in registry.all_strategies()
                           if "rebalance" in s.defaults]
    assert calendar_strategies, "the fixture assumes at least one calendar strategy"
    for strategy in calendar_strategies:
        assert opened["strategies"][strategy.name]["rebalance"] == "any"


def test_handing_over_the_calendar_changes_nothing_else():
    config = {"strategies": {"xs_momentum": {"top_n": 5, "enabled": False}}}
    opened = session._hand_the_calendar_to_the_session(config)
    assert opened["strategies"]["xs_momentum"]["top_n"] == 5
    assert opened["strategies"]["xs_momentum"]["enabled"] is False
    assert config["strategies"]["xs_momentum"] == {"top_n": 5, "enabled": False}, \
        "the caller's config must not be mutated"


def test_a_rebalance_is_due_once_a_month_not_once_a_day(book_path):
    book = _started(book_path)
    assert session._rebalance_due(book, "2026-01-05") is True      # never run

    book.last_rebalance = "2026-01-05"
    assert session._rebalance_due(book, "2026-01-06") is False
    assert session._rebalance_due(book, "2026-01-30") is False
    assert session._rebalance_due(book, "2026-02-02") is True


def test_a_trader_switched_off_for_months_rebalances_when_it_returns(book_path):
    """Keyed on the book, not the calendar. A trader that missed March must
    rebalance the day it comes back rather than hold a stale book until April."""
    book = _started(book_path)
    book.last_rebalance = "2026-01-05"
    assert session._rebalance_due(book, "2026-06-17") is True


def test_a_brand_new_book_rebalances_even_though_opening_it_wrote_a_note(book_path):
    """The bug this pins, from the first real session. Opening the book logs a
    note about the FX conversion, and the rebalance check read that same session
    log — so the very first session concluded it had already rebalanced this
    month and opened nothing. The clock now keys on actual rebalances, so
    anything else writing a log line cannot move it."""
    book = _started(book_path)
    book.sessions.append({"date": "2026-01-05", "note": "opened with GBP->USD"})
    assert session._rebalance_due(book, "2026-01-05") is True


def test_a_mark_only_session_does_not_consume_the_month(book_path):
    """A book marked daily through January must still rebalance in February;
    the daily marks must not look like a rebalance."""
    book = _started(book_path)
    book.last_rebalance = "2026-01-05"
    for day in range(6, 32):
        book.mark(f"2026-01-{day:02d}", "marked only")
    assert session._rebalance_due(book, "2026-02-02") is True


def test_the_rebalance_clock_survives_a_reload(book_path):
    book = _started(book_path)
    book.last_rebalance = "2026-01-05"
    book.save(book_path)
    assert Book.load(book_path).last_rebalance == "2026-01-05"


def test_a_book_refuses_to_hold_two_currencies_in_one_balance():
    """The book has ONE cash balance. Buying a US share subtracts its dollar
    cost from it, so a universe spanning London and New York would add pounds
    to dollars and produce a number that looks precise and means nothing —
    the exact error convert_trades exists to prevent in the backtest."""
    mixed = {"paper": {"screen": {"universe": ["AAPL", "TSCO.L"]}}}
    with pytest.raises(ValueError, match="one cash balance"):
        session.trading_currency(mixed)

    us_only = {"paper": {"screen": {"universe": ["AAPL", "MSFT"]}}}
    assert session.trading_currency(us_only) == "USD"

    declared = {"paper": {"trading_currency": "gbp",
                          "screen": {"universe": ["TSCO.L", "BP.L"]}}}
    assert session.trading_currency(declared) == "GBP"


def test_the_opening_balance_is_converted_once_and_says_so():
    """The account is in whatever the user banks in; the book trades in whatever
    the market prices in. Converting once at the start keeps a currency return
    from being buried inside a measurement of the strategy."""
    class FakeRouter:
        @staticmethod
        def get_fx_rates(currencies, base):
            return {"GBPUSD": 1.25}          # core.fx keys on the PAIR

    config = {"base_currency": "GBP", "paper": {"trading_currency": "USD"}}
    equity, currency, note = session._opening_balance(
        config, FakeRouter(), {"starting_equity": 100_000})

    assert currency == "USD"
    assert equity == pytest.approx(125_000.0)
    assert "GBP" in note and "USD" in note


def test_the_rate_is_asked_for_from_both_directions():
    """get_fx_rates builds its pairs from {base, "USD"}, so asking for GBP->USD
    with USD as the base collapses to one pair — and the router swallows a
    failed fetch, so one transient miss returns an empty dict rather than an
    error. Asked the other way the same call returns both legs. That asymmetry
    stopped the very first paper session from starting."""
    class LopsidedRouter:
        @staticmethod
        def get_fx_rates(currencies, base):
            if base == "USD":
                return {}                       # the collapsed, unlucky direction
            return {"GBPUSD": 1.35, "USDGBP": 0.74}

    rates = session._rates_both_ways(LopsidedRouter(), "GBP", "USD")
    assert rates["GBPUSD"] == 1.35

    config = {"base_currency": "GBP", "paper": {"trading_currency": "USD"}}
    equity, currency, note = session._opening_balance(
        config, LopsidedRouter(), {"starting_equity": 100_000})
    assert currency == "USD" and equity == pytest.approx(135_000.0)


def test_a_book_will_not_start_without_a_rate_to_convert_at():
    class BrokenRouter:
        @staticmethod
        def get_fx_rates(currencies, base):
            raise RuntimeError("no rate")

    config = {"base_currency": "GBP", "paper": {"trading_currency": "USD"}}
    with pytest.raises(ValueError, match="two currencies added together"):
        session._opening_balance(config, BrokenRouter(), {"starting_equity": 100_000})


def test_candidate_ranking_mirrors_the_backtest_in_both_stages():
    """Two live sessions failed in opposite directions here.

    Sorting on reward:risk alone let the most FREQUENT signal take every slot —
    ts_momentum fires on hundreds of names, xs_momentum on twelve — and the
    book opened eleven trend-following positions and no rotation. Then sorting
    on strategy priority globally let the top-RANKED strategy take every slot
    instead: twelve rotation positions and nothing else.

    The backtest applies priority within a ticker and reward:risk across
    tickers. Only that shape gives each instrument to its best strategy and
    then lets instruments compete.
    """
    class Idea:
        def __init__(self, ticker, strategy, reward_risk):
            self.ticker, self.strategy = ticker, strategy
            self.reward_risk = reward_risk

    # AAA is wanted by both strategies; BBB only by the lower-priority one.
    ideas = [Idea("AAA", "ts_momentum", 3.0), Idea("AAA", "xs_momentum", 3.0),
             Idea("BBB", "ts_momentum", 3.5), Idea("CCC", "low_beta", 2.5)]
    ranked = session._rank_candidates(ideas, {})

    assert len(ranked) == 3, "one candidate per instrument"
    by_ticker = {i.ticker: i.strategy for i in ranked}
    assert by_ticker["AAA"] == "xs_momentum", "priority decides WITHIN a ticker"
    assert by_ticker["BBB"] == "ts_momentum", \
        "a lower-priority strategy still gets instruments nothing else wants"
    assert ranked[0].ticker == "BBB", "reward:risk decides ACROSS tickers"


def test_the_session_breaks_ties_the_same_way_the_backtest_does():
    """The bug this pins, from the first real session. Every strategy targets a
    fixed multiple of its stop, so xs_momentum and ts_momentum both score
    exactly 3.0 on reward:risk and the tie fell to iteration order. ts_momentum
    fires on any name with a positive year — hundreds — while xs_momentum takes
    the top twelve of fifteen hundred, so the book filled entirely with
    ts_momentum and opened not one rotation position: the strategy with the best
    measured expectancy got starved by the one that simply fires more often.
    """
    from assistant.backtest import engine

    priority = engine.DEFAULTS["strategy_priority"]
    assert priority.index("xs_momentum") < priority.index("ts_momentum")

    class Idea:
        def __init__(self, strategy, reward_risk):
            self.strategy, self.reward_risk = strategy, reward_risk

    ideas = [Idea("ts_momentum", 3.0), Idea("low_beta", 2.5),
             Idea("xs_momentum", 3.0), Idea("ts_momentum", 3.0)]
    ranks = {name: i for i, name in enumerate(priority)}
    ideas.sort(key=lambda i: (ranks.get(i.strategy, len(ranks)), -(i.reward_risk or 0)))
    assert [i.strategy for i in ideas][0] == "xs_momentum", \
        "the rarer, better-ranked strategy must be offered a slot first"


def test_a_token_position_is_refused(book_path):
    """The first real session opened NOK at ONE share — nine dollars inside a
    $135,000 book, left over when the exposure cap was nearly full. It cannot
    move the result, its commission is a pure loss, and it takes a slot."""
    cfg = session.settings({"paper": {}, "account": {"portfolio_value": 100_000}})
    equity = 135_000.0
    floor = equity * cfg["min_position_pct"] / 100

    assert 1 * 9.36 < floor, "a one-share NOK position must fall under the floor"
    assert 48 * 313.49 > floor, "a real AAPL position must clear it"


def test_slippage_always_hurts():
    assert session._slipped(100.0, "long", 50.0, opening=True) > 100.0
    assert session._slipped(100.0, "long", 50.0, opening=False) < 100.0
    assert session._slipped(100.0, "short", 50.0, opening=True) < 100.0
    assert session._slipped(100.0, "short", 50.0, opening=False) > 100.0


def test_a_bar_covering_both_levels_is_scored_as_the_stop():
    """No intrabar data means the order is unknowable. Assuming the target
    converts every ambiguous day into a winner."""
    position = {"direction": "long", "stop": 90.0, "target": 110.0}
    bar = pd.Series({"High": 115.0, "Low": 85.0, "Close": 100.0})
    assert session._exit_reason(position, bar)[0] == "stop"

    short = {"direction": "short", "stop": 110.0, "target": 90.0}
    assert session._exit_reason(short, bar)[0] == "stop"


def test_a_position_that_touched_nothing_stays_open():
    position = {"direction": "long", "stop": 90.0, "target": 110.0}
    bar = pd.Series({"High": 105.0, "Low": 95.0, "Close": 100.0})
    assert session._exit_reason(position, bar) == (None, None)


def test_the_paper_package_cannot_reach_a_broker():
    """The strongest guarantee available is not a flag that is switched off, it
    is the absence of the code that would place an order.

    Checked on the parsed module rather than by searching the text, so the
    prose explaining that there is no broker does not itself trip the test.
    """
    import ast
    import pathlib

    package = pathlib.Path(session.__file__).parent
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "broker" not in alias.name, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert "broker" not in (node.module or ""), \
                    f"{path.name} imports from {node.module}"
                for alias in node.names:
                    assert "roker" not in alias.name, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.Attribute):
                assert node.attr != "place_order", f"{path.name} calls place_order"
            elif isinstance(node, ast.Name):
                assert node.id != "place_order", f"{path.name} calls place_order"
