"""Journal strategy/regime tagging and the per-strategy feedback loop.

Every test runs against a throwaway database — the real journal is the user's
trading record and must never be written to by a test run.
"""
import os
import tempfile

import pytest

from assistant import journal
from assistant.strategies.base import STATUS_ACTIONABLE, STATUS_WATCH, Strategy, StrategyContext
from assistant.research import regime


@pytest.fixture(autouse=True)
def temp_journal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(journal, "DB_PATH", os.path.join(tmp, "test.db"))
        yield


def _add(strategy=None, market=None, outcome=None, risk_base=None, status=STATUS_ACTIONABLE,
         ticker="TEST", decision="approved"):
    tag = ({"strategy": strategy, "strategy_label": (strategy or "").title(),
            "regime": market, "status": status, "direction": "long"} if strategy else None)
    plan = {"direction": "long", "entry": 100.0, "stop": 95.0, "target": 115.0,
            "shares": 10, "risk_amount": 50.0, "confidence": 70}
    idea_id = journal.add_idea({"ticker": ticker}, {"thesis_summary": "t"}, plan,
                               {"verdict": "approved_for_review"},
                               strategy_idea=tag, risk_base=risk_base)
    journal.set_decision(idea_id, decision)
    if outcome:
        journal.record_outcome(idea_id, outcome)
    return idea_id


def test_tags_are_stored_and_read_back():
    idea_id = _add(strategy="momentum", market=regime.TRENDING_UP)
    idea = journal.get_idea(idea_id)
    assert idea["strategy"] == "momentum"
    assert idea["regime"] == regime.TRENDING_UP
    assert idea["status"] == STATUS_ACTIONABLE


def test_untagged_ideas_still_log():
    """Ideas from the standalone 'analyse this ticker' path have no strategy.
    They must record cleanly rather than blow up on the new columns."""
    idea_id = _add(strategy=None)
    idea = journal.get_idea(idea_id)
    assert idea["strategy"] is None and idea["regime"] is None


def test_hit_rate_is_reported_per_strategy():
    for _ in range(3):
        _add(strategy="momentum", market=regime.TRENDING_UP, outcome="win", risk_base=100)
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="loss", risk_base=100)
    _add(strategy="ts_momentum", market=regime.SIDEWAYS, outcome="loss", risk_base=50)

    stats = journal.stats()
    momentum = stats["by_strategy"]["momentum"]
    assert (momentum["wins"], momentum["losses"]) == (3, 1)
    assert momentum["win_rate_pct"] == 75.0
    assert stats["by_strategy"]["ts_momentum"]["win_rate_pct"] == 0.0


def test_hit_rate_is_reported_per_strategy_and_regime_combination():
    """The combination is the interesting number: a strategy can be excellent in
    one market and a disaster in another, and a blended average hides both."""
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="win", risk_base=100)
    _add(strategy="momentum", market=regime.SIDEWAYS, outcome="loss", risk_base=100)
    combos = journal.stats()["by_strategy_regime"]
    assert combos["momentum · TRENDING_UP"]["win_rate_pct"] == 100.0
    assert combos["momentum · SIDEWAYS"]["win_rate_pct"] == 0.0


def test_risk_totals_use_the_base_currency_column():
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="win", risk_base=120.5)
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="loss", risk_base=80.0)
    entry = journal.stats()["by_strategy"]["momentum"]
    assert entry["risked"] == pytest.approx(200.5)
    assert entry["risked_partial"] is False


def test_risk_total_is_flagged_partial_when_some_ideas_predate_the_column():
    """Ideas logged before base-currency recording existed have NULL there.
    Their loss must not be quietly counted as zero — the total says 'partial'."""
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="win", risk_base=100.0)
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="loss", risk_base=None)
    entry = journal.stats()["by_strategy"]["momentum"]
    assert entry["closed"] == 2
    assert entry["risked"] == pytest.approx(100.0)
    assert entry["risked_partial"] is True


def test_only_your_approved_ideas_count_toward_hit_rate():
    _add(strategy="momentum", market=regime.TRENDING_UP, outcome="win", decision="rejected")
    assert "momentum" not in journal.stats()["by_strategy"]


def test_ideas_produced_counts_everything_including_rejected():
    """Separate from hit rate on purpose: knowing a strategy fires 40 times and
    you approve none of them is itself a finding."""
    _add(strategy="low_beta", market=regime.SIDEWAYS, decision="rejected")
    _add(strategy="low_beta", market=regime.SIDEWAYS, decision="pending")
    produced = {(r["strategy"], r["regime"]): r["count"]
                for r in journal.stats()["ideas_produced"]}
    assert produced[("low_beta", regime.SIDEWAYS)] == 2


def test_watch_items_are_recorded_with_no_plan():
    idea_id = journal.add_idea(
        {"ticker": "TSCO.L"}, {"thesis_summary": "squeeze"}, None,
        {"verdict": "needs_more_research"},
        strategy_idea={"strategy": "squeeze", "strategy_label": "Squeeze",
                       "regime": regime.VOLATILITY_SQUEEZE, "status": STATUS_WATCH,
                       "direction": None})
    idea = journal.get_idea(idea_id)
    assert idea["status"] == STATUS_WATCH
    assert idea["direction"] is None
    assert idea["entry"] is None and idea["shares"] is None


def test_reasons_are_deduplicated_on_the_idea():
    """The regime explains itself, then the strategy adds its checks. When a
    strategy restates something the regime already said, the user should see it
    once — a repeat reads as two independent pieces of evidence."""
    class Repeater(Strategy):
        name, label, description = "rep", "Rep", "test"
        regimes = (regime.SIDEWAYS,)

        def detect(self, ctx):
            return [self.build(ctx, "long", 100, 95, 115,
                               reasons=["Prevailing bias is up", "Something else",
                                        "prevailing bias is up  "])]

    ctx = StrategyContext(ticker="T", df=None, snapshot={},
                          regime={"regime": regime.SIDEWAYS, "reasons": []})
    idea = Repeater().detect(ctx)[0]
    assert idea.reasons == ["Prevailing bias is up", "Something else"]
