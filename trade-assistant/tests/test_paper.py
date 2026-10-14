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
    # The screen keeps only what it asked about, so the request names every row
    # the fake download returns, plus names that come back with no data.
    monkeypatch.setattr(screen, "candidate_symbols",
                        lambda config, router: list(table) + [f"S{i}" for i in range(1_100)])

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


def test_leveraged_and_inverse_funds_are_kept_out_of_the_universe():
    """A 15% position in a 3x fund is a 45% position in what it tracks, and the
    risk gate never sees it. TMF, SOXL, TQQQ, UVXY and SVXY were all inside the
    tradable universe — the volatility ceiling excluded them by luck, not rule.
    """
    class FakeRouter:
        @staticmethod
        def universe_rows():
            return [
                {"symbol": "TMF",  "type": "ETP", "mic": "ARCX",
                 "name": "Dir Dly 20+Yr Tr Bl3X Etf-Ui"},
                {"symbol": "SOXL", "type": "ETP", "mic": "ARCX",
                 "name": "Direx Dail Semi Bu 3X Et-Usd"},
                {"symbol": "TQQQ", "type": "ETP", "mic": "XNAS",
                 "name": "Proshares Ultrapro Qqq"},
                {"symbol": "AMDL", "type": "ETP", "mic": "XNAS",
                 "name": "Graniteshares 2Xlong Amd Etf"},
                {"symbol": "UVXY", "type": "ETP", "mic": "BATS",
                 "name": "Proshares Ultra Vix St Futur"},
                {"symbol": "SVXY", "type": "ETP", "mic": "BATS",
                 "name": "Proshares Short Vix St Futur"},
                # Must survive: ordinary unlevered funds and a real company.
                {"symbol": "TLT",  "type": "ETP", "mic": "XNAS",
                 "name": "Ishares 20+ Year Treasury Bd"},
                {"symbol": "GLD",  "type": "ETP", "mic": "ARCX",
                 "name": "Spdr Gold Shares"},
                {"symbol": "SPY",  "type": "ETP", "mic": "ARCX",
                 "name": "Ss Spdr S&P 500 Etf Trust-Us"},
                # A COMPANY with a leverage-ish word must not be deleted.
                {"symbol": "BULL", "type": "Common Stock", "mic": "XNYS",
                 "name": "Bullish Group Ltd"},
            ]

    kept = set(screen.candidate_symbols({}, FakeRouter()))
    for banned in ("TMF", "SOXL", "TQQQ", "AMDL", "UVXY", "SVXY"):
        assert banned not in kept, f"{banned} is leveraged or inverse"
    for keep in ("TLT", "GLD", "SPY", "BULL"):
        assert keep in kept, f"{keep} must survive the filter"


def test_the_leverage_filter_never_touches_ordinary_shares():
    """These markers are matched against a FUND's name. Applied to companies
    they would quietly delete real businesses."""
    row = {"symbol": "X", "type": "Common Stock", "name": "Ultra Bull 3X Corp"}
    assert screen._is_derivative_fund(row, screen.DEFAULTS) is False


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
    # The across-ticker order shown here is the pre-October one ("arrival");
    # the default now orders by signal strength — see the test below.
    ranked = session._rank_candidates(ideas, {"portfolio": {"tie_break": "arrival"}})

    assert len(ranked) == 3, "one candidate per instrument"
    by_ticker = {i.ticker: i.strategy for i in ranked}
    assert by_ticker["AAA"] == "xs_momentum", "priority decides WITHIN a ticker"
    assert by_ticker["BBB"] == "ts_momentum", \
        "a lower-priority strategy still gets instruments nothing else wants"
    assert ranked[0].ticker == "BBB", "reward:risk decides ACROSS tickers"


def test_the_same_underlying_is_not_bought_twice_under_two_tickers(book_path):
    """Measured on live data: SPY against ES=F correlates 0.98 and the
    correlation cap catches it, but GBPUSD=X against 6B=F measures 0.12 — the
    FX bars close on a different boundary from CME settlement, so the returns
    are computed over offset windows and decorrelate despite tracking one
    exchange rate. A live session opened both and doubled its sterling bet.

    Identity is not a statistic. Where two instruments are the same exposure by
    construction, that is asserted rather than measured.
    """
    book = _started(book_path)
    book.open_position(ticker="GBPUSD=X", direction="long", shares=15031,
                       price=1.35, stop=1.32, target=1.44, strategy="ts_momentum",
                       regime="TRENDING_UP", date="2026-01-05")

    assert session._already_exposed(book, "6B=F"), "the sterling future is the same bet"
    assert not session._already_exposed(book, "EURUSD=X"), "the euro is a different bet"
    assert not session._already_exposed(book, "AAPL")


def test_exposure_groups_pair_each_spot_with_its_future():
    from assistant.markets import exposure_group

    for spot, future in (("GBPUSD=X", "6B=F"), ("EURUSD=X", "6E=F"),
                         ("SPY", "ES=F"), ("GLD", "GC=F"), ("TLT", "ZB=F")):
        assert exposure_group(spot) is not None, f"{spot} must be grouped"
        assert exposure_group(spot) == exposure_group(future), \
            f"{spot} and {future} are one exposure"
    assert exposure_group("AAPL") is None, "an ordinary share is only itself"


def test_every_segment_is_offered_slots_not_just_the_first_listed():
    """The defect this pins, from a live session: 1,711 candidates across
    equities, FX and futures produced a book of nine US equities. Reward:risk
    is a constant here, so the real tie-break is arrival order, and the
    universe lists 1,482 shares ahead of 46 macro instruments — the currencies
    and futures were never reached before the twelve slots were gone.
    """
    class Idea:
        def __init__(self, ticker, reward_risk=3.0):
            self.ticker, self.reward_risk = ticker, reward_risk
            self.strategy = "ts_momentum"

    # Shares first, exactly as the universe presents them.
    ideas = [Idea(f"EQ{i}") for i in range(20)]
    ideas += [Idea("EURUSD=X"), Idea("GBPUSD=X"), Idea("ZN=F"), Idea("GC=F")]

    ordered = session._interleave_segments(ideas)
    from assistant.markets import asset_class_of
    first_twelve = {asset_class_of(i.ticker) for i in ordered[:12]}
    assert len(first_twelve) > 1, "one segment must not take every slot"
    assert "fx" in first_twelve, "a currency pair must be reachable within the slots"


def test_interleaving_never_invents_a_trade_to_fill_a_quota():
    """A segment with nothing to offer drops out rather than being topped up."""
    class Idea:
        def __init__(self, ticker):
            self.ticker, self.reward_risk, self.strategy = ticker, 3.0, "x"

    only_equities = [Idea(f"EQ{i}") for i in range(5)]
    ordered = session._interleave_segments(only_equities)
    assert len(ordered) == 5
    assert {i.ticker for i in ordered} == {f"EQ{i}" for i in range(5)}
    assert session._interleave_segments([]) == []


def test_the_per_market_cap_counts_asset_classes_not_one_big_bucket():
    """Everything used to map to "US", so a cap meant to spread the book across
    markets was instead capping the whole book — 8,371 candidates rejected by a
    rule that was supposed to be diversifying them."""
    from assistant.backtest.engine import _market_of

    buckets = {_market_of(t) for t in ("AAPL", "EURUSD=X", "ZN=F", "GC=F", "TLT")}
    assert len(buckets) > 1, "a share, a currency and a bond future are not one market"
    assert _market_of("EURUSD=X") == "fx"
    assert _market_of("ZN=F") == "rate_future"
    assert _market_of("TSCO.L") == ".L", "geography still works for non-US listings"


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


def test_kelly_falls_back_rather_than_refusing_to_trade(monkeypatch):
    """"Kelly has nothing to say about this strategy yet" and "Kelly says do
    not bet" are different answers. Collapsing them would silently stop trading
    every strategy the study has not yet measured out of sample."""
    class Idea:
        ticker, strategy, entry, stop = "AAPL", "unmeasured", 100.0, 90.0

    units = session._kelly_units(Idea(), equity=1_000_000, fill=100.0,
                                 multiplier=1.0, config={}, edges={},
                                 max_position=150_000, room=150_000,
                                 group_room=None)
    assert units is None, "no edge estimate means fall back, not stand down"


def test_kelly_sizes_from_an_out_of_sample_edge_when_one_exists():
    class Idea:
        ticker, strategy, entry, stop = "AAPL", "ts_momentum", 100.0, 90.0

    edges = {"ts_momentum": [0.032, -0.028] * 500}
    units = session._kelly_units(Idea(), equity=1_000_000, fill=100.0,
                                 multiplier=1.0,
                                 config={"kelly": {"max_position_fraction": 1.0}},
                                 edges=edges, max_position=1_000_000,
                                 room=1_000_000, group_room=None)
    assert units is not None and units > 0


def test_the_group_room_shrinks_as_an_exposure_fills_up(book_path):
    """Spot sterling and the sterling future are one bet. Without this each
    would be sized as if it were the only claim on that risk."""
    book = _started(book_path, equity=1_000_000)
    limits = {"max_position_pct": 15.0}
    empty = session._group_room(book, "GBPUSD=X", 1_000_000, limits)
    assert empty == pytest.approx(150_000)

    book.open_position(ticker="GBPUSD=X", direction="long", shares=100_000,
                       price=1.35, stop=1.32, target=1.44, strategy="x",
                       regime="TRENDING_UP", date="2026-01-05")
    after = session._group_room(book, "6B=F", 1_000_000, limits)
    assert after < empty, "the sterling future must see less room once spot is held"
    assert session._group_room(book, "AAPL", 1_000_000, limits) is None


# --- how old is a position, really -------------------------------------------
#
# The book runs several times a day. Ageing a position once per RUN instead of
# once per BAR made a four-day-old position read as eight bars old, and since
# `max_holding_bars` is read off that counter, it time-exited every position at
# half its intended horizon — on a schedule that moved with the dashboard's
# restarts rather than with the market.

def test_a_position_ages_once_per_bar_not_once_per_run():
    position = {"bars_held": 0, "bar_date": "2026-08-18"}

    # Three runs on the same trading day: the rebalance windows plus the mark
    # after the close. The market produced one bar, so the position is 0 old.
    for _ in range(3):
        session._age_by_one_bar(position, "2026-08-18")
    assert position["bars_held"] == 0

    session._age_by_one_bar(position, "2026-08-19")
    session._age_by_one_bar(position, "2026-08-19")
    assert position["bars_held"] == 1

    session._age_by_one_bar(position, "2026-08-20")
    assert position["bars_held"] == 2
    assert position["bar_date"] == "2026-08-20"


def test_a_weekend_of_reruns_does_not_age_a_position():
    """Sessions run on Saturday and Sunday mark against Friday's bar. Nothing
    traded, so nothing may age — otherwise a weekend costs two of the ten bars
    a position is allowed to live."""
    position = {"bars_held": 4, "bar_date": "2026-08-21"}
    for _ in range(6):
        session._age_by_one_bar(position, "2026-08-21")
    assert position["bars_held"] == 4


def test_a_position_marked_on_its_entry_bar_is_not_yet_a_bar_old():
    """`open_position` records the entry bar's date, so the same day's later
    session must find nothing new to count."""
    position = {"bars_held": 0, "bar_date": "2026-08-21"}
    session._age_by_one_bar(position, "2026-08-21")
    assert position["bars_held"] == 0


# --- one strategy's share of the book -------------------------------------------

def _share_setup(book_path, share):
    from assistant.backtest import portfolio
    from assistant.strategies.base import StrategyIdea

    def idea(ticker, strategy):
        return StrategyIdea(ticker=ticker, strategy=strategy, strategy_label=strategy,
                            regime="TRENDING_UP", direction="long", entry=100.0,
                            stop=95.0, target=115.0, risk_per_share=5.0,
                            reward_risk=3.0)

    config = {"account": {"portfolio_value": 100_000, "risk_per_trade_pct": 1.0,
                          "max_position_pct": 15.0},
              # Arrival order, so the tie-break between equal ideas is fixed. The
              # default hashes the ticker with TODAY's date, which made this test
              # pass or fail depending on the day it ran.
              "portfolio": {"max_gross_exposure_pct": 60.0, "max_open_positions": 8,
                            "max_strategy_share_pct": share, "tie_break": "arrival"},
              "strategy_priority": ["ts_momentum", "low_beta"], "paper": {}}
    index = pd.bdate_range("2026-01-01", periods=30)
    frames = {t: pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(30)]}, index=index)
              for t in ("AAA", "BBB", "CCC")}
    ideas = [idea("AAA", "ts_momentum"), idea("BBB", "ts_momentum"),
             idea("CCC", "ts_momentum"), idea("CCC", "low_beta")]
    return (_started(book_path), ideas, frames, config, session.settings(config),
            portfolio.settings(config))


def test_the_live_book_caps_one_strategy_and_passes_the_instrument_on(book_path):
    """The same rule the backtest applies, so the two cannot disagree. Each
    position is 15% of the book and the budget is 60%; at a 50% share, two
    time-series positions fill that strategy and the third instrument goes to
    the other strategy that flagged it."""
    book, ideas, frames, config, cfg, limits = _share_setup(book_path, share=50)
    out = {"opened": []}

    session._open_positions(book, session._rank_candidates(ideas, config), frames,
                            config, cfg, limits, "2026-02-12", out,
                            alternates=session._claim_order(ideas, config))

    # Chosen at the rebalance, queued for the next open.
    assert {o["ticker"]: o["strategy"] for o in out["queued"]} == {
        "AAA": "ts_momentum", "BBB": "ts_momentum", "CCC": "low_beta"}
    assert out["opened"] == [] and [o["ticker"] for o in book.pending] == ["AAA", "BBB", "CCC"]


def test_with_no_share_set_the_book_chooses_exactly_as_before(book_path):
    book, ideas, frames, config, cfg, limits = _share_setup(book_path, share=None)
    out = {"opened": []}

    session._open_positions(book, session._rank_candidates(ideas, config), frames,
                            config, cfg, limits, "2026-02-12", out,
                            alternates=session._claim_order(ideas, config))

    assert [o["strategy"] for o in out["queued"]] == ["ts_momentum"] * 3
    assert out["skipped"]["strategy_share"] == 0


def test_the_live_book_orders_instruments_by_signal_strength(book_path):
    """The same order the backtest uses: across instruments, the candidate its
    own rule rates highest goes first, whatever its reward:risk."""
    from assistant.strategies.base import StrategyIdea

    def idea(ticker, strength, reward_risk):
        return StrategyIdea(ticker=ticker, strategy="ts_momentum",
                            strategy_label="ts", regime="TRENDING_UP",
                            direction="long", entry=100.0, stop=95.0, target=115.0,
                            risk_per_share=5.0, reward_risk=reward_risk,
                            meta={"strength": strength})

    ideas = [idea("BIG_RR", 0.05, 9.0), idea("STRONG", 0.80, 3.0), idea("MID", 0.30, 3.0)]
    ranked = session._rank_candidates(ideas, {})
    assert [i.ticker for i in ranked] == ["STRONG", "MID", "BIG_RR"]



def test_the_session_says_when_the_strategy_ranking_is_due(tmp_path):
    """strategy_priority is ranked from measured edges and goes stale. Six
    months on, the session says so rather than trusting an old ranking."""
    import json as _json

    fresh, old = tmp_path / "fresh.json", tmp_path / "old.json"
    fresh.write_text(_json.dumps({"measured_at": "2026-10-04T00:00:00+00:00"}))
    old.write_text(_json.dumps({"measured_at": "2025-01-01T00:00:00+00:00"}))
    assert session._strategy_ranking_age_days(str(old)) > session.RERANK_AFTER_DAYS
    assert session._strategy_ranking_age_days(str(fresh)) < \
        session._strategy_ranking_age_days(str(old))
    assert session._strategy_ranking_age_days(str(tmp_path / "missing.json")) is None


# --- filling at the next open ---------------------------------------------------

def _order_book(book_path, equity=100_000):
    """A started book holding nothing, and the frames for one rising share."""
    book = _started(book_path, equity)
    index = pd.bdate_range("2026-01-05", periods=3)
    frames = {"AAA": pd.DataFrame({"Open": [100.0, 101.0, 104.0],
                                   "High": [101.0, 103.0, 106.0],
                                   "Low": [99.0, 100.5, 103.0],
                                   "Close": [100.5, 102.0, 105.0]}, index=index)}
    return book, frames


def _queued(ticker="AAA", signal_bar="2026-01-05", stop=95.0, signal_price=100.5,
            shares=10, strategy="ts_momentum"):
    return {"ticker": ticker, "direction": "long", "strategy": strategy,
            "regime": "TRENDING_UP", "headline": "", "meta": {},
            "stop": stop, "target": 130.0, "signal_price": signal_price,
            "signal_bar": signal_bar, "queued_on": signal_bar, "shares": shares,
            "multiplier": 1.0, "notional": shares * signal_price,
            "capital": shares * signal_price, "risk_budget": 1000.0,
            "sized_by": "fixed_risk"}


def _fill(book, frames, today="2026-01-07"):
    from assistant.backtest import portfolio
    config = {"account": {"portfolio_value": 100_000, "risk_per_trade_pct": 1.0,
                          "max_position_pct": 15.0}, "paper": {}}
    out = {"opened": [], "closed": [], "cancelled": [], "realised_today": 0.0,
           "costs_today": 0.0}
    session._fill_pending(book, frames, config, session.settings(config),
                          portfolio.settings(config), today, out)
    return out


def test_an_order_fills_at_the_open_after_its_signal_bar(book_path):
    """Chosen from the 5th's close, bought at the 6th's open — the price a real
    order placed overnight would get, which is what the backtest assumes."""
    book, frames = _order_book(book_path)
    book.pending = [_queued()]
    out = _fill(book, frames)

    assert book.pending == []
    position = book.positions[0]
    assert position["entry_date"] == "2026-01-06"
    assert position["entry_price"] == pytest.approx(101.0 * 1.0005)   # open + slippage
    assert position["last_price"] == 105.0                                 # marked to today
    assert [o["ticker"] for o in out["opened"]] == ["AAA"]


def test_only_the_queued_stocks_are_bought(book_path):
    """Two orders, one cancelled at the open: its room is NOT handed to some
    other stock. The open is not where anything is chosen."""
    book, frames = _order_book(book_path)
    frames["BBB"] = frames["AAA"].copy()
    frames["BBB"]["Open"] = [100.0, 90.0, 92.0]      # gaps below its stop at the open
    book.pending = [_queued("AAA"), _queued("BBB", stop=95.0)]
    out = _fill(book, frames)

    assert [p["ticker"] for p in book.positions] == ["AAA"]
    assert [(c["ticker"], c["reason"]) for c in out["cancelled"]] == [
        ("BBB", "opened beyond its stop")]
    assert book.pending == []


def test_an_open_too_close_to_the_stop_is_not_taken(book_path):
    """The backtest's rule: if the fill leaves under half the risk the trade was
    sized on, it is a different trade."""
    book, frames = _order_book(book_path)
    frames["AAA"]["Open"] = [100.0, 96.5, 104.0]     # stop 95: 1.5 of a planned 5.5
    book.pending = [_queued()]
    out = _fill(book, frames)
    assert book.positions == []
    assert "too close to its stop" in out["cancelled"][0]["reason"]


def test_an_order_waits_until_its_next_open_has_happened(book_path):
    book, frames = _order_book(book_path)
    book.pending = [_queued(signal_bar="2026-01-07")]   # the latest bar: no open after it yet
    _fill(book, frames, today="2026-01-07")
    assert book.positions == [] and len(book.pending) == 1


def test_bars_after_the_fill_are_checked_for_exits(book_path):
    """Filled on the 6th; the 7th's low went through the stop. The book was not
    running that day, but the stop was live at the broker, so it exits."""
    book, frames = _order_book(book_path)
    frames["AAA"]["Low"] = [99.0, 100.5, 94.0]
    book.pending = [_queued()]
    out = _fill(book, frames)
    assert book.positions == []
    assert [c["reason"] for c in out["closed"]] == ["stop"]


def test_queued_orders_take_their_room_in_the_book(book_path):
    """A second rebalance the same morning must not choose the same room
    twice: a queued order counts against every limit as if it were held."""
    book, _ = _order_book(book_path)
    book.pending = [_queued(shares=500)]                 # 50,250 of notional reserved
    view = session._Reserved(book)
    assert len(view.positions) == 1
    assert view.gross_exposure() == pytest.approx(500 * 100.5)
    assert view.cash == pytest.approx(book.cash - 500 * 100.5)

