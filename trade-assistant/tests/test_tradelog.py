"""Daily P&L and the trade journal — why each trade did what it did."""
from assistant.paper import tradelog
from assistant.paper.book import Book


def started_book(equity=1_000_000.0):
    book = Book()
    book.start(equity, "USD", "2026-08-10")
    return book


def add_trade(book, *, ticker="NVDA", entry=200.0, stop=185.0, target=245.0,
              exit_price=245.0, reason="target", strategy="ts_momentum",
              regime="TRENDING_UP", shares=100, direction="long",
              headline="Trend-following long", meta=None, bars_held=6):
    book.open_position(ticker=ticker, direction=direction, shares=shares,
                       price=entry, stop=stop, target=target, strategy=strategy,
                       regime=regime, date="2026-08-10", headline=headline,
                       meta=meta or {}, price_source="ibkr", bar_date="2026-08-10")
    position = book.positions[-1]
    position["bars_held"] = bars_held
    book.close_position(position, exit_price, "2026-08-17", reason)
    return position


# -- daily record ----------------------------------------------------------

def test_a_day_splits_realised_from_unrealised():
    """The split is the whole point. A day can be up on paper while every trade
    it actually closed lost money, and one equity number hides which."""
    book = started_book()
    add_trade(book, exit_price=245.0, reason="target")     # +4,500 realised
    realised = book.closed[-1]["pnl"]
    book.cash -= 3.0                                       # commission actually charged
    row = book.record_day("2026-08-17", realised=realised, costs=3.0, closed=1)

    assert row["realised"] == 4500.0
    # Everything closed, so nothing is left to drift: the unrealised part is nil.
    assert row["unrealised_change"] == 0.0
    assert row["change"] == 4497.0                         # realised less the commission


def test_an_open_position_drifting_shows_as_unrealised():
    book = started_book()
    book.open_position(ticker="AAPL", direction="long", shares=100, price=300.0,
                       stop=280.0, target=350.0, strategy="ts_momentum",
                       regime="TRENDING_UP", date="2026-08-10", headline="h")
    book.positions[0]["last_price"] = 310.0                # +1,000 on paper
    row = book.record_day("2026-08-17", realised=0.0, costs=0.0)
    assert row["realised"] == 0.0
    assert row["unrealised_change"] == 1000.0


def test_rerunning_a_session_does_not_double_count_the_day():
    book = started_book()
    add_trade(book)
    realised = book.closed[-1]["pnl"]
    first = book.record_day("2026-08-17", realised=realised, closed=1)
    second = book.record_day("2026-08-17", realised=realised, closed=1)
    assert len(book.daily) == 1
    # The day is still measured against where it OPENED, not against itself.
    assert second["previous_equity"] == first["previous_equity"]
    assert second["change"] == first["change"]


def test_the_second_day_measures_against_the_first():
    book = started_book()
    add_trade(book)
    book.record_day("2026-08-17", realised=book.closed[-1]["pnl"], closed=1)
    book.record_day("2026-08-18", realised=0.0)
    assert book.daily[1]["previous_equity"] == book.daily[0]["equity"]


def test_daily_rows_survive_a_save_and_load(tmp_path):
    path = str(tmp_path / "book.json")
    book = started_book()
    book.record_day("2026-08-17", realised=0.0)
    book.save(path)
    assert len(Book.load(path).daily) == 1


# -- entry reasons ---------------------------------------------------------

def test_the_entry_reason_is_quoted_from_before_the_outcome():
    book = started_book()
    add_trade(book, headline="Trend-following long — 12-month return +20.4%")
    e = tradelog.entry(book.closed[-1])
    assert "12-month return +20.4%" in e["why_entered"][0]


def test_the_planned_reward_to_risk_is_recorded():
    book = started_book()
    add_trade(book, entry=200.0, stop=185.0, target=245.0)
    e = tradelog.entry(book.closed[-1])
    assert any("3.0:1" in line for line in e["why_entered"])


def test_the_measurements_behind_the_signal_are_shown():
    """Without them an entry reason is a slogan rather than something checkable."""
    book = started_book()
    add_trade(book, meta={"trailing_return_pct": 20.38, "lookback_bars": 252})
    e = tradelog.entry(book.closed[-1])
    assert any("Trailing return" in m for m in e["measurements"])


# -- outcome attribution ---------------------------------------------------

def test_a_target_is_reported_as_the_shape_the_rules_want():
    book = started_book()
    add_trade(book, exit_price=245.0, reason="target")
    e = tradelog.entry(book.closed[-1])
    assert e["outcome_title"] == "Target reached"
    assert e["won"] is True
    assert any("3.00 times the amount budgeted" in x for x in e["why_this_outcome"])


def test_a_stop_is_reported_as_the_budgeted_loss():
    book = started_book()
    add_trade(book, exit_price=185.0, reason="stop")
    e = tradelog.entry(book.closed[-1])
    assert e["outcome_title"] == "Stopped out"
    assert e["won"] is False
    assert any("budgeted" in x for x in e["why_this_outcome"])


def test_a_time_exit_points_at_the_holding_period_not_the_direction():
    book = started_book()
    add_trade(book, exit_price=205.0, reason="time", bars_held=10)
    e = tradelog.entry(book.closed[-1])
    assert e["outcome_title"] == "Time exit"
    assert any("holding period" in x for x in e["why_this_outcome"])


def test_a_small_winner_is_not_flattered():
    """A win smaller than the risk taken is a warning, not a success."""
    book = started_book()
    add_trade(book, entry=200.0, stop=185.0, target=245.0, exit_price=203.0,
              reason="time")
    e = tradelog.entry(book.closed[-1])
    assert any("does not compound" in x for x in e["why_this_outcome"])


# -- breakdown -------------------------------------------------------------

def test_the_breakdown_separates_winning_from_losing_strategies():
    book = started_book()
    add_trade(book, ticker="NVDA", strategy="ts_momentum", exit_price=245.0, reason="target")
    add_trade(book, ticker="XOM", strategy="band_reversion", entry=110.0, stop=104.0,
              target=128.0, exit_price=104.0, reason="stop", shares=200)
    by = tradelog.breakdown(book.closed)
    rows = {r["key"]: r for r in by["by_strategy"]}
    assert rows["ts_momentum"]["pnl"] > 0
    assert rows["band_reversion"]["pnl"] < 0
    # Worst first, so the thing costing money is the thing you read.
    assert by["by_strategy"][0]["key"] == "band_reversion"


def test_the_breakdown_cuts_by_regime_exit_and_segment():
    book = started_book()
    add_trade(book)
    by = tradelog.breakdown(book.closed)
    assert {"by_strategy", "by_regime", "by_exit", "by_segment"} <= set(by)
    assert by["by_exit"][0]["key"] == "target"


# -- observations ----------------------------------------------------------

def test_a_small_sample_says_so_before_anything_else():
    book = started_book()
    add_trade(book)
    report = tradelog.report(book)
    assert "provisional" in report["observations"][0]


def test_no_closed_trades_makes_no_claims():
    report = tradelog.report(started_book())
    assert report["totals"]["closed"] == 0
    assert "Nothing here can be measured" in report["observations"][0]


def test_profit_factor_and_averages_are_reported():
    book = started_book()
    add_trade(book, ticker="NVDA", exit_price=245.0, reason="target")
    add_trade(book, ticker="XOM", entry=110.0, stop=104.0, target=128.0,
              exit_price=104.0, reason="stop", shares=200)
    totals = tradelog.report(book)["totals"]
    assert totals["closed"] == 2
    assert totals["wins"] == 1 and totals["losses"] == 1
    assert totals["profit_factor"] == round(4500 / 1200, 2)
