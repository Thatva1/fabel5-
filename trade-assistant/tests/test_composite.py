"""Twelve strategies combined into one ranking, from measured evidence.

What this replaces is a hand-ordered list and a tie-break that discriminates
nothing, so the tests are mostly about the properties the list could not
express: that agreement counts, that correlated agreement counts for much less,
and that a library arguing with itself produces no trade rather than an
arbitrary one.
"""
import math

import pytest

from assistant.strategies import composite
from assistant.strategies.base import StrategyIdea

EDGES = {"ts_momentum": 0.0011, "dual_momentum": 0.0012,
         "long_reversal": 0.0009, "xs_momentum": 0.0006,
         "band_reversion": 0.0003}

# ts and dual are both trend: they lose at the same time. Reversal does not.
MATRIX = {
    "ts_momentum": {"dual_momentum": 0.78, "long_reversal": 0.27,
                    "xs_momentum": 0.73, "band_reversion": 0.40},
    "dual_momentum": {"ts_momentum": 0.78, "long_reversal": 0.41,
                      "xs_momentum": 0.73, "band_reversion": 0.38},
    "long_reversal": {"ts_momentum": 0.27, "dual_momentum": 0.41,
                      "xs_momentum": 0.30, "band_reversion": 0.27},
    "xs_momentum": {"ts_momentum": 0.73, "dual_momentum": 0.73,
                    "long_reversal": 0.30, "band_reversion": 0.29},
    "band_reversion": {"ts_momentum": 0.40, "dual_momentum": 0.38,
                       "long_reversal": 0.27, "xs_momentum": 0.29},
}


def idea(ticker, strategy, direction="long", reward_risk=3.0):
    return StrategyIdea(ticker=ticker, strategy=strategy,
                        strategy_label=strategy, regime="TRENDING_UP",
                        direction=direction, entry=100.0, stop=95.0,
                        target=115.0, risk_per_share=5.0,
                        reward_risk=reward_risk)


# --- the combination ---------------------------------------------------------

def test_one_strategy_alone_scores_one():
    """The natural unit: a single signal is worth exactly itself, whatever its
    edge. Otherwise a strategy with a bigger measured edge would outrank a
    genuine consensus purely on scale."""
    out = composite.combine({"ts_momentum": 1}, EDGES, MATRIX)
    assert out["score"] == pytest.approx(1.0)
    assert out["effective_signals"] == pytest.approx(1.0)
    assert out["direction"] == "long"


def test_two_correlated_strategies_agreeing_are_barely_more_than_one():
    """ts and dual momentum correlate at 0.78 — they lose at the same time, so
    their agreement is nearly one opinion held twice."""
    out = composite.combine({"ts_momentum": 1, "dual_momentum": 1}, EDGES, MATRIX)
    assert 1.0 < out["score"] < 1.12
    assert out["effective_signals"] < 1.2


def test_two_uncorrelated_strategies_agreeing_are_genuinely_stronger():
    """Momentum and reversal correlate at 0.27. When they agree, that is two
    different reasons rather than one reason twice."""
    correlated = composite.combine({"ts_momentum": 1, "dual_momentum": 1},
                                   EDGES, MATRIX)
    diverse = composite.combine({"ts_momentum": 1, "long_reversal": 1},
                                EDGES, MATRIX)
    assert diverse["score"] > correlated["score"]
    assert diverse["effective_signals"] > correlated["effective_signals"]


def test_perfectly_correlated_agreement_adds_nothing_at_all():
    """The limit that says the formula is right. If everything agreeing is the
    same bet, the combination is worth exactly one signal."""
    identical = {a: {b: 1.0 for b in EDGES} for a in EDGES}
    out = composite.combine({"ts_momentum": 1, "dual_momentum": 1,
                             "xs_momentum": 1}, EDGES, identical)
    assert out["score"] == pytest.approx(1.0, abs=1e-9)
    assert out["effective_signals"] == pytest.approx(1.0, abs=1e-6)


def test_independent_agreement_grows_like_the_square_root_of_the_count():
    """Four genuinely independent equal signals are worth two, not four."""
    flat = {a: {b: (1.0 if a == b else 0.0) for b in EDGES} for a in EDGES}
    equal = {k: 0.001 for k in EDGES}
    out = composite.combine({k: 1 for k in list(EDGES)[:4]}, equal, flat)
    assert out["score"] == pytest.approx(math.sqrt(4), abs=1e-6)
    assert out["effective_signals"] == pytest.approx(4.0, abs=1e-6)


def test_a_library_arguing_with_itself_produces_almost_nothing():
    """Handled by the same arithmetic rather than a special case: opposing
    votes cancel in the numerator. The priority list could not express this at
    all — it would simply have picked whichever rule sat higher."""
    out = composite.combine({"ts_momentum": 1, "long_reversal": -1}, EDGES, MATRIX)
    assert abs(out["score"]) < 0.15
    assert out["agreeing"] == 1 and out["dissenting"] == 1


def test_equal_and_opposite_votes_cancel_to_no_direction():
    edges = {"a": 0.001, "b": 0.001}
    flat = {"a": {"b": 0.5}, "b": {"a": 0.5}}
    out = composite.combine({"a": 1, "b": -1}, edges, flat)
    assert out["score"] == pytest.approx(0.0)
    assert out["direction"] is None


def test_a_strategy_with_no_measured_edge_contributes_nothing():
    """A rule nobody has measured out of sample is a rule nobody knows anything
    about. It is named rather than silently ignored."""
    out = composite.combine({"ts_momentum": 1, "brand_new": 1}, EDGES, MATRIX)
    assert out["unmeasured"] == ["brand_new"]
    assert out["score"] == pytest.approx(1.0)


def test_nothing_measured_at_all_is_refused_not_scored():
    out = composite.combine({"brand_new": 1, "also_new": -1}, EDGES, MATRIX)
    assert out["score"] == 0.0 and out["direction"] is None
    assert "nothing to combine" in out["note"]


def test_an_unmeasured_correlation_is_assumed_HIGH_not_independent():
    """Assuming independence would hand a brand-new strategy the largest
    possible agreement bonus on no evidence at all."""
    assert composite.correlation({}, "a", "b") == composite.UNKNOWN_CORRELATION
    assert composite.UNKNOWN_CORRELATION >= 0.5
    assert composite.correlation({}, "a", "a") == 1.0


def test_the_matrix_is_read_in_either_order():
    matrix = {"a": {"b": 0.4}}
    assert composite.correlation(matrix, "b", "a") == 0.4


# --- ranking a whole scan ----------------------------------------------------

def test_a_consensus_instrument_outranks_a_lone_signal():
    """The information the old design threw away. Four strategies flagging the
    same instrument used to be reduced to one, with the agreement discarded."""
    ideas = [idea("CONSENSUS", "ts_momentum"), idea("CONSENSUS", "long_reversal"),
             idea("CONSENSUS", "band_reversion"), idea("LONER", "dual_momentum")]
    ordered, detail = composite.rank(ideas, edges=EDGES, matrix=MATRIX)
    assert [i.ticker for i in ordered] == ["CONSENSUS", "LONER"]
    assert ordered[0].meta["composite"]["agreeing"] == 3
    assert detail["ranked"] == 2


def test_reward_risk_no_longer_decides_anything():
    """Every strategy targets a fixed multiple of its own stop, so reward:risk
    is near-constant by construction and used to leave the ordering to whatever
    the screen happened to return first."""
    ideas = [idea("CONSENSUS", "ts_momentum", reward_risk=2.5),
             idea("CONSENSUS", "long_reversal", reward_risk=2.5),
             idea("LONER", "dual_momentum", reward_risk=3.0)]
    ordered, _ = composite.rank(ideas, edges=EDGES, matrix=MATRIX)
    assert ordered[0].ticker == "CONSENSUS"      # despite the lower reward:risk


def test_one_instrument_appears_once():
    ideas = [idea("X", "ts_momentum"), idea("X", "dual_momentum"),
             idea("X", "xs_momentum")]
    ordered, _ = composite.rank(ideas, edges=EDGES, matrix=MATRIX)
    assert len(ordered) == 1


def test_the_levels_come_from_the_strongest_AGREEING_rule():
    """A stop borrowed from a dissenting rule would be sized against a trade
    nobody is taking."""
    ideas = [idea("X", "band_reversion", direction="long"),
             idea("X", "dual_momentum", direction="long"),
             idea("X", "long_reversal", direction="short")]
    ordered, _ = composite.rank(ideas, edges=EDGES, matrix=MATRIX)
    assert ordered[0].strategy == "dual_momentum"     # the highest-edge long


def test_a_conflicted_instrument_gets_no_slot():
    ideas = [idea("FIGHT", "ts_momentum", direction="long"),
             idea("FIGHT", "dual_momentum", direction="short"),
             idea("CLEAN", "long_reversal", direction="long")]
    ordered, detail = composite.rank(ideas, edges=EDGES, matrix=MATRIX,
                                     config={"composite": {"min_score": 0.3}})
    assert [i.ticker for i in ordered] == ["CLEAN"]
    assert [r["ticker"] for r in detail["rejected"]] == ["FIGHT"]


def test_every_chosen_idea_records_why():
    """The book stores this, so a trade can later be read against the reasoning
    that produced it rather than only against its outcome."""
    ideas = [idea("X", "ts_momentum"), idea("X", "long_reversal")]
    ordered, _ = composite.rank(ideas, edges=EDGES, matrix=MATRIX)
    verdict = ordered[0].meta["composite"]
    assert verdict["score"] > 1.0
    assert {c["strategy"] for c in verdict["contributors"]} == {
        "ts_momentum", "long_reversal"}


def test_no_edges_at_all_says_so_rather_than_ranking_on_nothing():
    ideas = [idea("X", "ts_momentum")]
    ordered, detail = composite.rank(ideas, edges={}, matrix=MATRIX)
    assert ordered == []
    assert "fall back to the priority list" in composite.describe(detail)


# --- the real files on disk --------------------------------------------------

def test_the_projects_own_measured_evidence_loads():
    edges, age = composite.load_edges()
    matrix = composite.load_correlations()
    assert len(edges) >= 10, "expected the study's per-strategy means"
    assert all(v is not None for v in edges.values())
    assert len(matrix) >= 10
    # And the matrix is symmetric where it is populated.
    for a in matrix:
        for b, value in matrix[a].items():
            if b in matrix:
                assert matrix[b][a] == pytest.approx(value, abs=1e-6)


def test_a_stale_edge_file_is_flagged_in_the_description():
    detail = {"ranked": 5, "rejected": [], "edges_used": 12,
              "correlations_used": 12, "unknown_correlation": 0.7}
    assert "days old" in composite.describe(detail, age_days=120)
    assert "days old" not in composite.describe(detail, age_days=3)


# --- the live wiring ---------------------------------------------------------

def test_the_session_still_uses_the_priority_list_by_default():
    """Turning the composite on chooses a DIFFERENT book. That has to be a
    decision, not something discovered one morning."""
    from assistant.paper import session

    ideas = [idea("A", "band_reversion"), idea("B", "ts_momentum")]
    ordered = session._rank_candidates(ideas, {})
    assert {i.ticker for i in ordered} == {"A", "B"}
    assert all("composite" not in (i.meta or {}) for i in ordered)


def test_enabling_it_switches_the_session_to_the_measured_ranking(monkeypatch):
    from assistant.paper import session

    monkeypatch.setattr(composite, "load_edges", lambda *a, **k: (EDGES, 1.0))
    monkeypatch.setattr(composite, "load_correlations", lambda *a, **k: MATRIX)

    ideas = [idea("CONSENSUS", "ts_momentum"), idea("CONSENSUS", "long_reversal"),
             idea("LONER", "dual_momentum")]
    ordered = session._rank_candidates(ideas, {"composite": {"enabled": True}})
    assert ordered[0].ticker == "CONSENSUS"
    assert "composite" in ordered[0].meta


def test_a_missing_edge_file_falls_back_rather_than_stopping_the_book(monkeypatch):
    """A book that stops trading because a report file is absent is worse than
    one ordered by hand."""
    from assistant.paper import session

    monkeypatch.setattr(composite, "load_edges", lambda *a, **k: ({}, None))
    ideas = [idea("A", "ts_momentum"), idea("B", "long_reversal")]
    ordered = session._rank_candidates(ideas, {"composite": {"enabled": True}})
    assert {i.ticker for i in ordered} == {"A", "B"}
