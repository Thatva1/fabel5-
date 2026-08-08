"""Realised P&L, R-multiples, and the close-out path that records them.

The recurring failure mode these guard against: a number that cannot be
computed quietly becoming zero. A missing exit price must leave P&L unknown,
not report a losing trade as breakeven and flatter the strategy that produced it.
"""
import os
import tempfile

import pytest

from assistant import closeout, journal
from assistant.risk import pnl


def _plan(direction="long", entry=100.0, stop=95.0, shares=10, currency="GBP"):
    return {"direction": direction, "entry": entry, "stop": stop, "target": 115.0,
            "shares": shares, "currency": currency, "risk_amount": 50.0, "confidence": 70}


@pytest.fixture(autouse=True)
def temp_journal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(journal, "DB_PATH", os.path.join(tmp, "test.db"))
        yield


# --- cash P&L ----------------------------------------------------------------

def test_long_profit_and_loss():
    assert pnl.realised_pnl("long", 100.0, 110.0, 10) == 100.0
    assert pnl.realised_pnl("long", 100.0, 92.0, 10) == -80.0


def test_short_profits_when_price_falls():
    """The sign flip is the whole point of a short and the easiest thing to get
    backwards — a short that made money must never be logged as a loss."""
    assert pnl.realised_pnl("short", 100.0, 90.0, 10) == 100.0
    assert pnl.realised_pnl("short", 100.0, 108.0, 10) == -80.0


def test_share_count_sign_is_ignored():
    """IBKR reports shorts as negative share counts. Direction already carries
    the sign, so counting it twice would invert the P&L."""
    assert pnl.realised_pnl("short", 100.0, 90.0, -10) == 100.0


def test_missing_inputs_are_unknown_not_zero():
    assert pnl.realised_pnl("long", 100.0, None, 10) is None
    assert pnl.realised_pnl("long", None, 110.0, 10) is None
    assert pnl.realised_pnl(None, 100.0, 110.0, 10) is None
    assert pnl.realised_pnl("long", 100.0, "not a number", 10) is None


# --- R-multiples -------------------------------------------------------------

def test_stopping_out_exactly_at_the_stop_is_minus_one_r():
    assert pnl.r_multiple("long", 100.0, 95.0, 95.0) == -1.0
    assert pnl.r_multiple("short", 100.0, 105.0, 105.0) == -1.0


def test_hitting_a_two_r_target_reads_as_two_r():
    assert pnl.r_multiple("long", 100.0, 110.0, 95.0) == 2.0


def test_a_gap_through_the_stop_is_worse_than_minus_one_r():
    """Clipping this at -1R would hide the single most expensive thing that
    happens to a real account: a stop that did not hold."""
    assert pnl.r_multiple("long", 100.0, 80.0, 95.0) == -4.0


def test_r_multiple_is_size_independent():
    """The point of R: two strategies trading wildly different sizes on the same
    move must score identically, so cash totals don't decide which one wins."""
    small = pnl.r_multiple("long", 100.0, 110.0, 95.0)
    big = pnl.r_multiple("long", 1000.0, 1100.0, 950.0)
    assert small == big == 2.0


def test_zero_risk_distance_has_no_r_multiple():
    assert pnl.r_multiple("long", 100.0, 110.0, 100.0) is None


def test_expectancy_averages_r_and_ignores_unknowns():
    assert pnl.expectancy([2.0, -1.0, -1.0, 3.0]) == 0.75
    assert pnl.expectancy([2.0, None, -1.0]) == 0.5
    assert pnl.expectancy([]) is None
    assert pnl.expectancy([None]) is None


def test_close_out_bundles_everything_from_one_exit_price():
    result = pnl.close_out(_plan(), exit_price=110.0)
    assert result["pnl_instrument"] == 100.0
    assert result["r_multiple"] == 2.0
    assert result["entry_used"] == 100.0


def test_close_out_honours_the_actual_fill():
    """Judging a strategy on an entry you never got is judging it on fiction."""
    result = pnl.close_out(_plan(), exit_price=110.0, entry_override=104.0)
    assert result["entry_used"] == 104.0
    assert result["pnl_instrument"] == 60.0


def test_close_out_of_a_watch_item_is_empty():
    assert pnl.close_out(None, exit_price=110.0) == {}


# --- the close-out service ---------------------------------------------------

def _log(plan=_plan(), strategy="momentum"):
    idea_id = journal.add_idea(
        {"ticker": "TSCO.L"}, {"thesis_summary": "t"}, plan,
        {"verdict": "approved_for_review"},
        strategy_idea={"strategy": strategy, "strategy_label": strategy.title(),
                       "regime": "TRENDING_UP", "status": "actionable", "direction": "long"},
        risk_base=50.0)
    journal.set_decision(idea_id, "approved")
    return idea_id


CONFIG = {"base_currency": "GBP"}


def test_closing_records_pnl_and_r_on_the_idea():
    idea_id = _log()
    result = closeout.close_idea(idea_id, "win", exit_price=110.0, config=CONFIG)
    assert result["ok"]
    idea = journal.get_idea(idea_id)
    assert idea["pnl_instrument"] == 100.0
    assert idea["pnl_base"] == 100.0        # already in the base currency
    assert idea["r_multiple"] == 2.0
    assert idea["outcome"] == "win"


def test_foreign_currency_pnl_is_converted_and_the_rate_stored():
    """The rate must be pinned at close time. Re-converting old trades at
    today's rate would silently rewrite your track record every time FX moved."""
    idea_id = _log(plan=_plan(currency="USD"))
    result = closeout.close_idea(idea_id, "win", exit_price=110.0, config=CONFIG,
                                 fx_rates={"USDGBP": 0.80})
    assert result["ok"]
    idea = journal.get_idea(idea_id)
    assert idea["pnl_instrument"] == 100.0
    assert idea["pnl_base"] == 80.0
    assert idea["exit_fx_rate"] == pytest.approx(0.80)


def test_missing_fx_rate_warns_and_keeps_the_instrument_figure():
    idea_id = _log(plan=_plan(currency="JPY"))
    result = closeout.close_idea(idea_id, "loss", exit_price=90.0, config=CONFIG,
                                 fx_rates={})
    idea = journal.get_idea(idea_id)
    assert idea["pnl_instrument"] == -100.0
    assert idea["pnl_base"] is None
    assert any("exchange rate" in w for w in result["warnings"])


def test_closing_without_an_exit_price_warns_and_records_no_pnl():
    idea_id = _log()
    result = closeout.close_idea(idea_id, "win", config=CONFIG)
    idea = journal.get_idea(idea_id)
    assert idea["outcome"] == "win"          # still counts toward hit rate
    assert idea["pnl_base"] is None          # but not toward P&L
    assert any("No exit price" in w for w in result["warnings"])


def test_reopening_an_idea_clears_stale_pnl():
    """Leaving a realised P&L attached to a position you've reopened would put
    profit from a closed trade into your live totals."""
    idea_id = _log()
    closeout.close_idea(idea_id, "win", exit_price=110.0, config=CONFIG)
    closeout.close_idea(idea_id, "open", config=CONFIG)
    idea = journal.get_idea(idea_id)
    assert idea["outcome"] == "open"
    assert idea["pnl_base"] is None and idea["r_multiple"] is None
    assert idea["outcome_price"] is None


def test_closing_a_watch_item_warns_there_is_no_position():
    idea_id = journal.add_idea(
        {"ticker": "TSCO.L"}, {"thesis_summary": "squeeze"}, None,
        {"verdict": "needs_more_research"},
        strategy_idea={"strategy": "squeeze", "strategy_label": "Squeeze",
                       "regime": "VOLATILITY_SQUEEZE", "status": "watch", "direction": None})
    journal.set_decision(idea_id, "approved")
    result = closeout.close_idea(idea_id, "scratch", exit_price=110.0, config=CONFIG)
    assert any("watch item" in w for w in result["warnings"])
    assert journal.get_idea(idea_id)["pnl_base"] is None


def test_closing_a_missing_idea_fails_cleanly():
    result = closeout.close_idea(9999, "win", exit_price=110.0, config=CONFIG)
    assert result["ok"] is False and "does not exist" in result["error"]


# --- aggregation per strategy ------------------------------------------------

def test_pnl_and_expectancy_are_reported_per_strategy():
    for exit_price in (110.0, 110.0, 95.0):        # +2R, +2R, -1R
        closeout.close_idea(_log(strategy="momentum"),
                            "win" if exit_price > 100 else "loss",
                            exit_price=exit_price, config=CONFIG)
    closeout.close_idea(_log(strategy="mean_reversion"), "loss",
                        exit_price=95.0, config=CONFIG)

    stats = journal.stats()
    momentum = stats["by_strategy"]["momentum"]
    assert momentum["pnl"] == pytest.approx(150.0)     # +100 +100 -50
    assert momentum["expectancy_r"] == 1.0             # (2 + 2 - 1) / 3
    assert momentum["profit_factor"] == pytest.approx(4.0)   # 200 profit / 50 loss
    assert stats["by_strategy"]["mean_reversion"]["expectancy_r"] == -1.0


def test_a_strategy_with_no_losses_reports_no_profit_factor():
    """Infinity is not valid JSON and would break the dashboard; 'no losses yet'
    is a sample-size fact, not a performance ratio."""
    closeout.close_idea(_log(), "win", exit_price=110.0, config=CONFIG)
    entry = journal.stats()["by_strategy"]["momentum"]
    assert entry["profit_factor"] is None
    assert entry["no_losses_yet"] is True


def test_pnl_total_is_flagged_partial_when_an_exit_price_is_missing():
    closeout.close_idea(_log(), "win", exit_price=110.0, config=CONFIG)
    closeout.close_idea(_log(), "loss", config=CONFIG)      # no exit price
    entry = journal.stats()["by_strategy"]["momentum"]
    assert entry["closed"] == 2
    assert entry["pnl"] == pytest.approx(100.0)
    assert entry["pnl_partial"] is True


def test_scratches_count_as_closed_but_not_in_the_hit_rate():
    closeout.close_idea(_log(), "win", exit_price=110.0, config=CONFIG)
    closeout.close_idea(_log(), "scratch", exit_price=100.0, config=CONFIG)
    entry = journal.stats()["by_strategy"]["momentum"]
    assert entry["closed"] == 2
    assert entry["win_rate_pct"] == 100.0      # 1 win, 0 losses
    assert entry["scratches"] == 1
