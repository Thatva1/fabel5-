"""Layer 2 safety tests — every path that could place an order without explicit
human intent must be refused. These run against a temporary journal DB and a
fake broker; no network, no IB Gateway, no real orders.
"""
import json
import os
import tempfile
import time

import pytest

from assistant import journal
from assistant.broker.base import ExecutionNotEnabled
from assistant.broker.paper import PaperBroker
from assistant.core.models import OrderIntent
from assistant.providers.base import ProviderUnavailable


@pytest.fixture(autouse=True)
def temp_journal(monkeypatch):
    """Point the journal at a throwaway DB so tests never touch real history."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(journal, "DB_PATH", os.path.join(tmp, "test.db"))
        yield


class FakeBroker:
    """Stands in for IBKRBroker: records orders instead of sending them."""
    is_paper = True

    def __init__(self, portfolio_value=100000, positions=None, base_currency="USD"):
        self.placed = []
        self._value = portfolio_value
        self._positions = positions or []
        self._base_currency = base_currency

    def get_account(self):
        return {"portfolio_value": self._value, "base_currency": self._base_currency,
                "source": "FakeBroker"}

    def get_positions(self):
        return list(self._positions)

    def place_order(self, intent):
        self.placed.append(intent)
        return {"ib_order_id": 999, "status": "Submitted", "paper": True,
                "detail": f"{intent.side} {intent.quantity} {intent.ticker}"}


class FakeRouter:
    """Stands in for the DataRouter.

    get_prices matters: prepare_ticket refuses to build a ticket when the
    current price can't be read (audit finding R-3), so a router that returns
    nothing is a router that blocks every order. `price=None` simulates exactly
    that outage; the default sits on the seeded plan's entry so the drift check
    passes and the other safety properties can be tested in isolation.
    """

    def __init__(self, price=100.0):
        self._price = price

    def get_fx_rates(self, currencies, base):
        return {}

    def get_prices(self, ticker, period="1y"):
        if self._price is None:
            raise ProviderUnavailable(f"no market data for {ticker}")
        import pandas as pd
        return pd.DataFrame({"Close": [self._price] * 5})


CONFIG_EXEC_ON = {
    "base_currency": "USD",
    "execution": {"enabled": True, "port": 7497},
    "account": {"portfolio_value": 100000, "risk_per_trade_pct": 1.0,
                "max_position_pct": 15.0, "max_total_exposure_pct": 60.0,
                "max_open_positions": 8},
    "positions": [],
}
CONFIG_EXEC_OFF = {**CONFIG_EXEC_ON, "execution": {"enabled": False}}


def _seed_idea(decision="approved", verdict="approved_for_review", with_plan=True,
               shares=50, entry=100.0, risk_amount=900.0, stop=None):
    snapshot = {"ticker": "TEST", "price": entry, "atr": 4.0, "signals": [],
                "_fundamentals": {"sector": None}}
    thesis = {"source": "claude:test", "conviction": 70, "supports_setup": True,
              "net_bias": "bullish", "thesis_summary": "test"}
    stop = entry - 18 if stop is None else stop
    plan = ({"direction": "long", "entry": entry, "stop": stop, "target": entry + 45,
             "shares": shares, "position_value": shares * entry, "risk_amount": risk_amount,
             "confidence": 70, "currency": "USD", "reward_risk": 2.5}
            if with_plan else None)
    gate_result = {"verdict": verdict, "hard_failures": [], "soft_flags": [], "exposure": {}}
    idea_id = journal.add_idea(snapshot, thesis, plan, gate_result)
    journal.set_decision(idea_id, decision)
    return idea_id


def _prepare(idea_id, config=CONFIG_EXEC_ON, broker=None, monkeypatch=None,
             router=None):
    from assistant import execution
    broker = broker or FakeBroker()
    monkeypatch.setattr("assistant.broker.factory.get_execution_broker", lambda cfg: broker)
    return execution.prepare_ticket(idea_id, config, router or FakeRouter()), broker


# ---------- The Layer 1 guarantee ----------

def test_paper_broker_always_refuses_orders():
    broker = PaperBroker(CONFIG_EXEC_ON)
    with pytest.raises(ExecutionNotEnabled):
        broker.place_order(OrderIntent(ticker="TEST", side="buy", quantity=1))


def test_execution_disabled_blocks_prepare():
    from assistant.execution import ExecutionRefused, prepare_ticket
    idea_id = _seed_idea()
    with pytest.raises(ExecutionRefused, match="disabled"):
        prepare_ticket(idea_id, CONFIG_EXEC_OFF, FakeRouter())


def test_execution_disabled_blocks_confirm(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea()
    ticket, _ = _prepare(idea_id, monkeypatch=monkeypatch)
    with pytest.raises(execution.ExecutionRefused, match="disabled"):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_OFF)


# ---------- Human approval is mandatory ----------

@pytest.mark.parametrize("decision", ["pending", "needs_research", "rejected"])
def test_unapproved_ideas_cannot_be_prepared(decision, monkeypatch):
    from assistant.execution import ExecutionRefused
    idea_id = _seed_idea(decision=decision)
    with pytest.raises(ExecutionRefused, match="not 'approved'"):
        _prepare(idea_id, monkeypatch=monkeypatch)


def test_gate_rejected_idea_cannot_be_prepared(monkeypatch):
    from assistant.execution import ExecutionRefused
    idea_id = _seed_idea(verdict="rejected")
    with pytest.raises(ExecutionRefused, match="rejected by the risk gate"):
        _prepare(idea_id, monkeypatch=monkeypatch)


def test_idea_without_plan_cannot_be_prepared(monkeypatch):
    from assistant.execution import ExecutionRefused
    idea_id = _seed_idea(with_plan=False)
    with pytest.raises(ExecutionRefused, match="no trade plan"):
        _prepare(idea_id, monkeypatch=monkeypatch)


def test_missing_idea_refused(monkeypatch):
    from assistant.execution import ExecutionRefused
    with pytest.raises(ExecutionRefused, match="does not exist"):
        _prepare(99999, monkeypatch=monkeypatch)


# ---------- Preparing a ticket places nothing ----------

def test_prepare_does_not_place_any_order(monkeypatch):
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    assert broker.placed == []
    assert ticket["ticket_id"]
    assert journal.get_order(ticket["ticket_id"])["status"] == "pending_confirmation"


def test_prepare_reruns_risk_against_live_portfolio(monkeypatch):
    """An idea sized for a big account must be refused against a small one."""
    from assistant.execution import ExecutionRefused
    idea_id = _seed_idea(shares=50, entry=100.0)   # 5,000 position, 900 risk
    small_account = FakeBroker(portfolio_value=10000)  # 5,000 = 50% >> 15% cap
    with pytest.raises(ExecutionRefused, match="CURRENT portfolio"):
        _prepare(idea_id, broker=small_account, monkeypatch=monkeypatch)
    assert small_account.placed == []


# ---------- Confirmation must be typed exactly ----------

@pytest.mark.parametrize("typed", ["", "yes", "OK", "TES", "TESTX", "confirm"])
def test_wrong_confirmation_text_places_nothing(typed, monkeypatch):
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    with pytest.raises(execution.ExecutionRefused, match="did not match"):
        execution.confirm_ticket(ticket["ticket_id"], typed, CONFIG_EXEC_ON)
    assert broker.placed == []
    assert journal.get_order(ticket["ticket_id"])["status"] == "pending_confirmation"


def test_correct_confirmation_places_exactly_one_order(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    result = execution.confirm_ticket(ticket["ticket_id"], "test", CONFIG_EXEC_ON)  # case-insensitive
    assert len(broker.placed) == 1
    intent = broker.placed[0]
    assert (intent.ticker, intent.side, intent.quantity) == ("TEST", "buy", 50)
    assert intent.order_type == "limit" and intent.limit_price == 100.0
    assert result["status"] == "Submitted"
    # IB's "Submitted" means accepted by the broker; we record that, not a
    # blanket "submitted" for every placeOrder call.
    assert journal.get_order(ticket["ticket_id"])["status"] == "accepted"


def test_ticket_is_single_use(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    with pytest.raises(execution.ExecutionRefused, match="single-use"):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert len(broker.placed) == 1  # not doubled


def test_unapproving_idea_after_ticket_blocks_placement(monkeypatch):
    """Changing your mind between ticket and confirm must stop the order."""
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    journal.set_decision(idea_id, "rejected")
    with pytest.raises(execution.ExecutionRefused, match="not 'approved'"):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert broker.placed == []


def test_cancelled_ticket_cannot_be_confirmed(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    execution.cancel_ticket(ticket["ticket_id"])
    with pytest.raises(execution.ExecutionRefused, match="single-use"):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert broker.placed == []


def test_stale_ticket_expires_instead_of_placing(monkeypatch):
    """A ticket's limit price comes from a quote at prepare time; after the
    expiry window it must be refused rather than sent at a stale price."""
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    monkeypatch.setattr(execution, "_ticket_age_seconds",
                        lambda t: execution.TICKET_MAX_AGE_SECONDS + 60)
    with pytest.raises(execution.ExecutionRefused, match="expired"):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert broker.placed == []
    assert journal.get_order(ticket["ticket_id"])["status"] == "cancelled"


def test_fresh_ticket_is_not_expired(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea()
    ticket, broker = _prepare(idea_id, monkeypatch=monkeypatch)
    execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert len(broker.placed) == 1


def test_broker_failure_is_recorded_and_raised(monkeypatch):
    from assistant import execution

    class BrokenBroker(FakeBroker):
        def place_order(self, intent):
            raise RuntimeError("gateway died mid-order")

    idea_id = _seed_idea()
    broker = BrokenBroker()
    ticket, _ = _prepare(idea_id, broker=broker, monkeypatch=monkeypatch)
    with pytest.raises(execution.ExecutionRefused, match="was NOT placed"):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert journal.get_order(ticket["ticket_id"])["status"] == "error"


# ---------- Concurrency: one ticket must never place two orders ----------

def test_concurrent_confirmations_place_only_one_order(monkeypatch):
    """Regression: a double click / retry / two tabs must not double the trade.

    Both threads pass the status read; only the atomic DB claim decides.
    """
    import threading

    from assistant import execution

    idea_id = _seed_idea()
    barrier = threading.Barrier(2)

    class SlowBroker(FakeBroker):
        def place_order(self, intent):
            # Widen the race window so an unguarded implementation fails loudly.
            time.sleep(0.2)
            return super().place_order(intent)

    broker = SlowBroker()
    ticket, _ = _prepare(idea_id, broker=broker, monkeypatch=monkeypatch)

    results, errors = [], []

    def confirm():
        barrier.wait()  # fire both at the same instant
        try:
            results.append(execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON))
        except execution.ExecutionRefused as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(broker.placed) == 1, f"orders_sent={len(broker.placed)} — duplicate order!"
    assert len(results) == 1
    assert len(errors) == 1 and "already being submitted" in errors[0]


def test_claim_is_single_winner():
    """The DB claim itself: exactly one caller may transition the ticket."""
    idea_id = _seed_idea()
    ticket_id = journal.create_order_ticket(idea_id, "buy", "TEST", 10, 100.0, "USD", 900, 82.0)
    assert journal.claim_order_for_submission(ticket_id) is True
    assert journal.claim_order_for_submission(ticket_id) is False
    assert journal.get_order(ticket_id)["status"] == "submitting"


def test_release_claim_restores_pending():
    idea_id = _seed_idea()
    ticket_id = journal.create_order_ticket(idea_id, "buy", "TEST", 10, 100.0, "USD", 900, 82.0)
    journal.claim_order_for_submission(ticket_id)
    journal.release_order_claim(ticket_id)
    assert journal.get_order(ticket_id)["status"] == "pending_confirmation"
    assert journal.claim_order_for_submission(ticket_id) is True


def test_broker_error_does_not_reopen_ticket(monkeypatch):
    """After a broker failure we cannot know if IB got the order, so the ticket
    must stay consumed rather than inviting an automatic second attempt."""
    from assistant import execution

    class BrokenBroker(FakeBroker):
        def place_order(self, intent):
            raise RuntimeError("connection dropped after send")

    idea_id = _seed_idea()
    ticket, _ = _prepare(idea_id, broker=BrokenBroker(), monkeypatch=monkeypatch)
    with pytest.raises(execution.ExecutionRefused):
        execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert journal.get_order(ticket["ticket_id"])["status"] == "error"
    assert journal.claim_order_for_submission(ticket["ticket_id"]) is False


# ---------- Order status must reflect what IBKR actually said ----------

@pytest.mark.parametrize("ib_status,expected", [
    ("Submitted", "accepted"),
    ("PreSubmitted", "submitting"),
    ("PendingSubmit", "submitting"),
    ("Filled", "filled"),
    ("Cancelled", "cancelled"),
    ("ApiCancelled", "cancelled"),
    ("Inactive", "rejected"),      # IB's rejection state
    ("SomethingNew", "submitting"),  # unknown -> not claimed as accepted
    (None, "submitting"),
])
def test_broker_status_mapping(ib_status, expected):
    from assistant.execution import _map_broker_status
    assert _map_broker_status(ib_status) == expected


def test_rejected_order_is_not_recorded_as_accepted(monkeypatch):
    from assistant import execution

    class RejectingBroker(FakeBroker):
        def place_order(self, intent):
            self.placed.append(intent)
            return {"ib_order_id": 7, "status": "Inactive", "detail": "rejected by IB"}

    idea_id = _seed_idea()
    ticket, _ = _prepare(idea_id, broker=RejectingBroker(), monkeypatch=monkeypatch)
    result = execution.confirm_ticket(ticket["ticket_id"], "TEST", CONFIG_EXEC_ON)
    assert result["recorded_status"] == "rejected"
    assert journal.get_order(ticket["ticket_id"])["status"] == "rejected"


# ---------- Protective stop ----------

def test_ticket_carries_a_stop_price(monkeypatch):
    idea_id = _seed_idea()
    ticket, _ = _prepare(idea_id, monkeypatch=monkeypatch)
    assert ticket["stop_price"] == 82.0
    assert journal.get_order(ticket["ticket_id"])["stop_price"] == 82.0


def test_plan_without_stop_is_refused(monkeypatch):
    """No stop means the max-loss figure is unenforceable — refuse outright."""
    from assistant import execution
    idea_id = _seed_idea()
    idea = journal.get_idea(idea_id)
    payload = idea["payload"]
    payload["plan"]["stop"] = None
    with journal._connect() as conn:
        conn.execute("UPDATE ideas SET payload=? WHERE id=?",
                     (json.dumps(payload), idea_id))
    with pytest.raises(execution.ExecutionRefused, match="no stop level"):
        _prepare(idea_id, monkeypatch=monkeypatch)


# ---------- Price drift at ticket time ----------

class PricedRouter:
    """Router stub returning a fixed current price."""
    def __init__(self, price):
        self.price = price

    def get_fx_rates(self, currencies, base):
        return {}

    def get_prices(self, ticker, period="1y"):
        import pandas as pd
        return pd.DataFrame({"Close": [self.price]})


def test_price_drift_beyond_limit_is_refused(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea(entry=100.0)
    broker = FakeBroker()
    monkeypatch.setattr("assistant.broker.factory.get_execution_broker", lambda cfg: broker)
    with pytest.raises(execution.ExecutionRefused, match="away from the planned entry"):
        execution.prepare_ticket(idea_id, CONFIG_EXEC_ON, PricedRouter(110.0))  # +10%


def test_price_past_stop_is_refused(monkeypatch):
    """Tight stop: price is within the drift limit but has already broken the
    invalidation level, so the setup is dead even though the price looks close."""
    from assistant import execution
    idea_id = _seed_idea(entry=100.0, stop=99.5)
    broker = FakeBroker()
    monkeypatch.setattr("assistant.broker.factory.get_execution_broker", lambda cfg: broker)
    with pytest.raises(execution.ExecutionRefused, match="already passed the planned stop"):
        execution.prepare_ticket(idea_id, CONFIG_EXEC_ON, PricedRouter(99.0))  # -1% drift


def test_small_drift_is_allowed(monkeypatch):
    from assistant import execution
    idea_id = _seed_idea(entry=100.0)
    broker = FakeBroker()
    monkeypatch.setattr("assistant.broker.factory.get_execution_broker", lambda cfg: broker)
    ticket = execution.prepare_ticket(idea_id, CONFIG_EXEC_ON, PricedRouter(101.0))  # +1%
    assert ticket["price_drift_pct"] == 1.0
    assert ticket["current_price"] == 101.0


# ---------- Paper vs live detection (must never mislabel real money) ----------

@pytest.mark.parametrize("accounts,expected", [
    (["DU1234567"], True),            # standard paper account
    (["DF1234567"], True),            # advisor paper account
    (["du1234567"], True),            # case-insensitive
    (["U7654321"], False),            # live individual — the case that matters
    (["F1234567"], False),            # live advisor
    ([], False),                      # unknown -> assume live
    (None, False),
    (["DU1111111", "U2222222"], False),  # mixed -> assume live
])
def test_paper_detection_uses_account_id(accounts, expected):
    from assistant.broker.ibkr import accounts_are_paper
    assert accounts_are_paper(accounts) is expected


def test_ticket_defaults_to_live_when_paper_unknown(monkeypatch):
    """A broker that reports nothing about paper status must NOT be shown as paper."""
    from assistant import execution

    class SilentBroker:
        """No is_paper attribute and no 'paper' key in get_account()."""
        def __init__(self):
            self.placed = []

        def get_account(self):
            return {"portfolio_value": 100000, "base_currency": "USD"}

        def get_positions(self):
            return []

        def place_order(self, intent):
            self.placed.append(intent)
            return {"ib_order_id": 1, "status": "Submitted", "detail": ""}

    idea_id = _seed_idea()
    ticket, _ = _prepare(idea_id, broker=SilentBroker(), monkeypatch=monkeypatch)
    assert ticket["paper"] is False


# ---------- Status reporting ----------

def test_execution_status_off_by_default():
    from assistant.execution import execution_status
    status = execution_status(CONFIG_EXEC_OFF)
    assert status["enabled"] is False and status["connected"] is False


def test_execution_status_handles_unreachable_gateway():
    from assistant.execution import execution_status
    # Port 9 (discard) is never an IB Gateway; must degrade, not crash.
    status = execution_status({**CONFIG_EXEC_ON, "execution": {"enabled": True, "port": 9}})
    assert status["enabled"] is True and status["connected"] is False
