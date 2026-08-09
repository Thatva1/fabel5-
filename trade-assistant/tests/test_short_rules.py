"""Short-selling rules in the Risk Gate, and the borrow check behind them.

The governing principle under test: a short is never allowed to be uncapped.
No stop, confirmed unborrowable, or over the (deliberately tighter) short
exposure limits must all be HARD failures — not warnings a tired human waves
through at 9pm.
"""
import pytest

from assistant.risk import borrow, gate


def _config(**overrides):
    config = {
        "base_currency": "GBP",
        "account": {
            "portfolio_value": 100000,
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 15.0,
            "max_total_exposure_pct": 60.0,
            "max_open_positions": 8,
        },
        "positions": [],
        "short_rules": {},
    }
    config.update(overrides)
    return config


def _plan(**overrides):
    plan = {
        "direction": "short",
        "currency": "GBP",
        "entry": 100.0,
        "stop": 105.0,
        "target": 85.0,
        "shares": 50,
        "position_value": 5000.0,
        "risk_amount": 250.0,
        "confidence": 70,
    }
    plan.update(overrides)
    return plan


def _thesis(bias="bearish"):
    return {"net_bias": bias, "conviction": 60, "supports_setup": True, "source": "claude"}


def _snapshot():
    return {"ticker": "TEST", "_fundamentals": {"sector": None}}


def _evaluate(plan=None, config=None, shortability=None):
    return gate.evaluate(_snapshot(), _thesis(), plan or _plan(), config or _config(),
                         {}, shortability=shortability)


def _borrow(status=borrow.YES, pct=None):
    return {"status": status, "source": "test", "detail": "test detail",
            "crowding": {"short_percent_of_float": pct}}


# --- the non-negotiables -----------------------------------------------------

def test_short_without_a_stop_is_rejected():
    result = _evaluate(_plan(stop=None), shortability=_borrow())
    assert result["verdict"] == "rejected"
    assert any("unlimited theoretical loss" in f for f in result["hard_failures"])


def test_short_stop_below_entry_is_rejected():
    """A 'stop' under the entry on a short would fire instantly and is almost
    always a long/short mix-up somewhere upstream."""
    result = _evaluate(_plan(stop=95.0), shortability=_borrow())
    assert result["verdict"] == "rejected"
    assert any("not above the entry" in f for f in result["hard_failures"])


def test_confirmed_not_borrowable_is_rejected():
    result = _evaluate(shortability=_borrow(status=borrow.NO))
    assert result["verdict"] == "rejected"
    assert any("Not available to short" in f for f in result["hard_failures"])


def test_unknown_borrow_flags_by_default_but_does_not_block():
    result = _evaluate(shortability=_borrow(status=borrow.UNKNOWN))
    assert result["verdict"] != "rejected"
    assert any("Borrow availability unverified" in f for f in result["soft_flags"])


def test_unknown_borrow_can_be_configured_to_block():
    config = _config(short_rules={"block_if_borrow_unknown": True})
    result = _evaluate(config=config, shortability=_borrow(status=borrow.UNKNOWN))
    assert result["verdict"] == "rejected"


def test_shorts_use_a_tighter_position_cap_than_longs():
    """12% of the portfolio is fine for a long (15% cap) and too big for a
    short (8% cap). The same plan must pass one and fail the other."""
    config = _config()
    long_result = _evaluate(_plan(direction="long", position_value=12000, stop=95.0,
                                  target=115.0), config=config)
    assert long_result["verdict"] != "rejected"
    short_result = _evaluate(_plan(position_value=12000), config=config,
                             shortability=_borrow())
    assert short_result["verdict"] == "rejected"
    assert any("short cap" in f for f in short_result["hard_failures"])


def test_total_short_exposure_is_capped_separately():
    positions = [
        {"ticker": "AAA", "shares": -100, "entry_price": 120.0, "currency": "GBP"},
        {"ticker": "BBB", "shares": -80, "entry_price": 110.0, "currency": "GBP"},
    ]   # 12,000 + 8,800 = 20,800 already short
    config = _config(positions=positions,
                     short_rules={"max_total_short_exposure_pct": 25.0,
                                  "max_open_shorts": 5})
    result = _evaluate(_plan(position_value=6000), config=config, shortability=_borrow())
    assert result["verdict"] == "rejected"
    assert any("Total short exposure" in f for f in result["hard_failures"])


def test_max_open_shorts_counts_only_shorts():
    positions = [
        {"ticker": "AAA", "shares": -10, "entry_price": 50.0, "currency": "GBP"},
        {"ticker": "BBB", "shares": -10, "entry_price": 50.0, "currency": "GBP"},
        {"ticker": "CCC", "shares": 500, "entry_price": 20.0, "currency": "GBP"},   # long
    ]
    config = _config(positions=positions, short_rules={"max_open_shorts": 3})
    assert _evaluate(config=config, shortability=_borrow())["verdict"] != "rejected"

    config = _config(positions=positions, short_rules={"max_open_shorts": 2})
    result = _evaluate(config=config, shortability=_borrow())
    assert any("max open shorts" in f for f in result["hard_failures"])


def test_crowded_short_is_flagged_not_blocked():
    result = _evaluate(shortability=_borrow(pct=22.5))
    assert result["verdict"] != "rejected"
    assert any("22.5% of the float" in f for f in result["soft_flags"])


def test_short_rules_do_not_touch_longs():
    """A long with no stop is not the gate's business here — it must not pick up
    a short-only failure."""
    result = _evaluate(_plan(direction="long", stop=None, target=115.0))
    assert not any("unlimited theoretical loss" in f for f in result["hard_failures"])


# --- the borrow lookup -------------------------------------------------------

def test_your_own_override_list_wins_over_everything():
    config = {"short_rules": {"shortable_overrides": {"TSCO.L": False}}}

    class AlwaysYes:
        name = "fake"

        def is_shortable(self, ticker, currency="USD"):
            return {"status": "yes", "detail": "broker says yes"}

    result = borrow.check_shortability("TSCO.L", config, broker=AlwaysYes())
    assert result["status"] == borrow.NO
    assert "config.yaml" in result["source"]


def test_broker_answer_is_used_when_there_is_no_override():
    class Broker:
        name = "ibkr"

        def is_shortable(self, ticker, currency="USD"):
            return {"status": "yes", "source": "IBKR", "detail": "2m shares available"}

    result = borrow.check_shortability("VOD.L", {}, broker=Broker())
    assert result["status"] == borrow.YES
    assert result["source"] == "IBKR"


def test_a_broker_that_raises_becomes_unknown_never_yes():
    """The failure mode that matters: an exception must not be read as
    permission to short."""
    class Broken:
        name = "ibkr"

        def is_shortable(self, ticker, currency="USD"):
            raise ConnectionError("gateway down")

    result = borrow.check_shortability("VOD.L", {}, broker=Broken())
    assert result["status"] == borrow.UNKNOWN
    assert "gateway down" in result["detail"]


def test_no_broker_at_all_is_unknown_with_an_actionable_message():
    result = borrow.check_shortability("VOD.L", {})
    assert result["status"] == borrow.UNKNOWN
    assert "shortable_overrides" in result["detail"]


def test_borrow_takes_short_interest_as_a_percentage_unchanged():
    """B-2: normalisation belongs at the provider boundary, which is the only
    place that knows the source unit. borrow.py must not re-guess it."""
    result = borrow.check_shortability(
        "VOD.L", {}, fundamentals={"short_percent_of_float": {"value": 18.0,
                                                              "source": "Yahoo Finance"}})
    assert result["crowding"]["short_percent_of_float"] == pytest.approx(18.0)


def test_short_interest_already_in_percent_is_left_alone():
    result = borrow.check_shortability(
        "VOD.L", {}, fundamentals={"short_percent_of_float": 22.0})
    assert result["crowding"]["short_percent_of_float"] == pytest.approx(22.0)


def test_a_lightly_shorted_name_is_not_reported_as_crowded():
    """The B-2 symptom: 0.8% of float used to be reported as 80%, well over the
    10% crowding flag — the safest kind of short flagged as the most crowded."""
    result = borrow.check_shortability(
        "VOD.L", {}, fundamentals={"short_percent_of_float": 0.8})
    pct = result["crowding"]["short_percent_of_float"]
    assert pct == pytest.approx(0.8)
    assert pct < gate.SHORT_RULE_DEFAULTS["crowding_flag_pct"]


def test_one_percent_of_float_is_not_reported_as_one_hundred():
    result = borrow.check_shortability(
        "VOD.L", {}, fundamentals={"short_percent_of_float": 1.0})
    assert result["crowding"]["short_percent_of_float"] == pytest.approx(1.0)


def test_missing_short_interest_is_none_not_zero():
    result = borrow.check_shortability("VOD.L", {}, fundamentals={})
    assert result["crowding"]["short_percent_of_float"] is None


# --- strategy vs thesis disagreement ----------------------------------------

def test_strategy_thesis_disagreement_is_flagged_not_vetoed():
    """The strategy found the setup; the LLM's read is a second opinion. A clash
    is worth knowing about, but it must not silently delete the idea."""
    plan = _plan(direction="short", strategy="ts_momentum",
                 strategy_label="Time-series momentum")
    result = gate.evaluate(_snapshot(), _thesis(bias="bullish"), plan, _config(), {},
                           shortability=_borrow())
    assert result["verdict"] != "rejected"
    assert any("disagree" in f for f in result["soft_flags"])


def test_agreement_produces_no_disagreement_flag():
    plan = _plan(direction="short", strategy="ts_momentum",
                 strategy_label="Time-series momentum")
    result = gate.evaluate(_snapshot(), _thesis(bias="bearish"), plan, _config(), {},
                           shortability=_borrow())
    assert not any("disagree" in f for f in result["soft_flags"])


# --- B-3: the borrow check must ask about the right contract ----------------

def test_broker_is_asked_in_the_instruments_currency():
    """IBKR resolved every symbol as USD, so every London/Paris/Frankfurt short
    came back UNKNOWN — a safety feature switched off for most of the markets
    this tool is pointed at."""
    seen = {}

    class RecordingBroker:
        name = "ibkr"

        def is_shortable(self, ticker, currency="USD"):
            seen["ticker"], seen["currency"] = ticker, currency
            return {"status": "yes", "source": "IBKR", "detail": "available"}

    result = borrow.check_shortability("TSCO.L", {}, broker=RecordingBroker(),
                                       currency="GBP")
    assert seen["currency"] == "GBP"
    assert result["status"] == borrow.YES


def test_currency_defaults_to_usd_when_not_supplied():
    seen = {}

    class RecordingBroker:
        name = "ibkr"

        def is_shortable(self, ticker, currency="USD"):
            seen["currency"] = currency
            return {"status": "yes", "source": "IBKR", "detail": "available"}

    borrow.check_shortability("AAPL", {}, broker=RecordingBroker())
    assert seen["currency"] == "USD"


def test_paper_broker_accepts_the_currency_argument():
    """Every implementation of the interface must take it."""
    from assistant.broker.paper import PaperBroker

    answer = PaperBroker({}).is_shortable("TSCO.L", "GBP")
    assert answer["status"] == borrow.UNKNOWN


def test_base_broker_accepts_the_currency_argument():
    from assistant.broker.base import Broker

    assert Broker().is_shortable("TSCO.L", "GBP")["status"] == borrow.UNKNOWN
