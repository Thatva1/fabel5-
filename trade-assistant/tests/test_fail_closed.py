"""Audit findings R-1, R-2, R-3: "we couldn't check" must never mean "proceed".

Each of these was a path where missing data quietly became permission:

  R-1  a missing FX rate treated a $10,000 position as £10,000 and only raised
       a soft flag, so every downstream cap was wrong in the permissive
       direction and the order could still be prepared
  R-2  a broker that reported no account value fell through to the config.yaml
       figure, sizing real money against an account that isn't yours
  R-3  an unreadable current price skipped BOTH the entry-drift guard and the
       "price has already passed your stop" guard
"""
import os
import tempfile

import pytest

from assistant import journal
from assistant.execution import ExecutionRefused
from assistant.risk import gate

from .optional_deps import requires_ib
from .test_execution import (CONFIG_EXEC_ON, FakeBroker, FakeRouter, _prepare,
                             _seed_idea)


@pytest.fixture(autouse=True)
def temp_journal(monkeypatch):
    """Point the journal at a throwaway DB so tests never touch real history."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(journal, "DB_PATH", os.path.join(tmp, "test.db"))
        yield

# A USD plan against a GBP account, with no GBP/USD rate supplied — the exact
# shape of the leak: the user's base currency is GBP, the watchlist is USD.
GBP_CONFIG = {
    "base_currency": "GBP",
    "account": {"portfolio_value": 100000, "risk_per_trade_pct": 1.0,
                "max_position_pct": 15.0, "max_total_exposure_pct": 60.0,
                "max_open_positions": 8},
    "positions": [],
}
USD_PLAN = {"direction": "long", "entry": 100.0, "stop": 82.0, "target": 145.0,
            "shares": 100, "position_value": 10000.0, "risk_amount": 900.0,
            "confidence": 70, "currency": "USD", "reward_risk": 2.5}
SNAPSHOT = {"ticker": "AAPL", "_fundamentals": {"sector": None}}
THESIS = {"source": "claude:test", "conviction": 70, "net_bias": "bullish"}


# ---------- R-1: missing FX rate ----------

def test_missing_fx_rate_is_only_a_flag_on_the_research_path():
    """An FX outage should not blank the whole idea list."""
    result = gate.evaluate(SNAPSHOT, THESIS, USD_PLAN, GBP_CONFIG, fx_rates={})
    assert result["verdict"] != "rejected"
    assert any("No FX rate for USD->GBP" in f for f in result["soft_flags"])


def test_missing_fx_rate_is_a_hard_failure_in_strict_mode():
    """strict_fx is what execution.prepare_ticket uses — real money."""
    result = gate.evaluate(SNAPSHOT, THESIS, USD_PLAN, GBP_CONFIG, fx_rates={},
                           strict_fx=True)
    assert result["verdict"] == "rejected"
    assert any("No FX rate for USD->GBP" in f for f in result["hard_failures"])


def test_strict_mode_is_silent_when_the_rate_is_present():
    result = gate.evaluate(SNAPSHOT, THESIS, USD_PLAN, GBP_CONFIG,
                           fx_rates={"USDGBP": 0.79}, strict_fx=True)
    assert not any("No FX rate" in f for f in result["hard_failures"])


def test_strict_mode_is_silent_when_currencies_match():
    """No conversion is needed, so nothing can be missing."""
    same = {**GBP_CONFIG, "base_currency": "USD"}
    result = gate.evaluate(SNAPSHOT, THESIS, USD_PLAN, same, fx_rates={},
                           strict_fx=True)
    assert not any("No FX rate" in f for f in result["hard_failures"])


def test_missing_rate_is_reported_once_not_once_per_check():
    """The same missing rate is reached by four separate checks."""
    config = {**GBP_CONFIG, "positions": [
        {"ticker": "MSFT", "shares": 10, "entry_price": 400.0, "currency": "USD"}]}
    result = gate.evaluate(SNAPSHOT, THESIS, USD_PLAN, config, fx_rates={},
                           strict_fx=True)
    fx_failures = [f for f in result["hard_failures"] if "No FX rate" in f]
    assert len(fx_failures) == 1


def test_prepare_ticket_refuses_when_the_fx_rate_is_missing(monkeypatch):
    """End to end: the research idea exists, but no order can be built."""
    idea_id = _seed_idea()
    config = {**CONFIG_EXEC_ON, "base_currency": "GBP"}
    with pytest.raises(ExecutionRefused, match="No FX rate"):
        _prepare(idea_id, config=config,
                 broker=FakeBroker(base_currency="GBP"), monkeypatch=monkeypatch)


# ---------- R-2: missing broker account value ----------

def test_prepare_refuses_when_broker_reports_no_account_value(monkeypatch):
    idea_id = _seed_idea()
    with pytest.raises(ExecutionRefused, match="did not report an account value"):
        _prepare(idea_id, broker=FakeBroker(portfolio_value=None),
                 monkeypatch=monkeypatch)


def test_prepare_refuses_on_a_zero_account_value(monkeypatch):
    """0.0 used to be falsy enough to silently become the config figure."""
    idea_id = _seed_idea()
    with pytest.raises(ExecutionRefused, match="account value of"):
        _prepare(idea_id, broker=FakeBroker(portfolio_value=0.0),
                 monkeypatch=monkeypatch)


def test_prepare_refuses_on_a_negative_account_value(monkeypatch):
    idea_id = _seed_idea()
    with pytest.raises(ExecutionRefused, match="account value of"):
        _prepare(idea_id, broker=FakeBroker(portfolio_value=-500.0),
                 monkeypatch=monkeypatch)


def test_prepare_refuses_on_an_unreadable_account_value(monkeypatch):
    idea_id = _seed_idea()
    with pytest.raises(ExecutionRefused, match="unreadable account value"):
        _prepare(idea_id, broker=FakeBroker(portfolio_value="n/a"),
                 monkeypatch=monkeypatch)


def test_the_config_value_is_never_substituted_for_a_missing_one(monkeypatch):
    """The specific fail-open: config says 100,000, broker says nothing.
    No ticket may be created at all."""
    idea_id = _seed_idea()
    before = len(journal.list_orders())
    with pytest.raises(ExecutionRefused):
        _prepare(idea_id, broker=FakeBroker(portfolio_value=None),
                 monkeypatch=monkeypatch)
    assert len(journal.list_orders()) == before, "a ticket was created anyway"


@requires_ib
def test_ibkr_get_account_reports_none_for_a_missing_tag():
    """The broker-side half of R-2, without touching IB."""
    from assistant.broker import ibkr

    class FakeIB:
        def managedAccounts(self):
            return ["DU123456"]

        def accountSummary(self, account=""):
            return []          # NetLiquidation absent

    broker = ibkr.IBKRBroker.__new__(ibkr.IBKRBroker)
    broker.account = ""
    broker.is_paper = False

    import contextlib

    @contextlib.contextmanager
    def fake_session(*args, **kwargs):
        yield FakeIB()

    broker._session = fake_session
    assert broker.get_account()["portfolio_value"] is None


# ---------- R-3: unreadable current price ----------

def test_prepare_refuses_when_the_price_cannot_be_read(monkeypatch):
    idea_id = _seed_idea()
    with pytest.raises(ExecutionRefused, match="Could not read a current price"):
        _prepare(idea_id, monkeypatch=monkeypatch, router=FakeRouter(price=None))


def test_no_ticket_is_created_when_the_price_is_unreadable(monkeypatch):
    idea_id = _seed_idea()
    before = len(journal.list_orders())
    with pytest.raises(ExecutionRefused):
        _prepare(idea_id, monkeypatch=monkeypatch, router=FakeRouter(price=None))
    assert len(journal.list_orders()) == before


def test_drift_guard_still_fires_when_the_price_is_readable(monkeypatch):
    """Guards the premise: the check being enforced is the real one."""
    idea_id = _seed_idea(entry=100.0)
    with pytest.raises(ExecutionRefused, match="away from the planned entry"):
        _prepare(idea_id, monkeypatch=monkeypatch, router=FakeRouter(price=110.0))


def test_stop_already_passed_still_fires(monkeypatch):
    idea_id = _seed_idea(entry=100.0, stop=99.5)
    with pytest.raises(ExecutionRefused, match="already passed the planned"):
        _prepare(idea_id, monkeypatch=monkeypatch, router=FakeRouter(price=99.0))


def test_a_plan_without_a_stop_is_refused_before_the_price_is_consulted(monkeypatch):
    """No stop is unplaceable whatever the market is doing, and the message
    should say that rather than complaining about market data."""
    idea_id = _seed_idea(stop=False)
    with pytest.raises(ExecutionRefused, match="no stop level"):
        _prepare(idea_id, monkeypatch=monkeypatch, router=FakeRouter(price=None))
