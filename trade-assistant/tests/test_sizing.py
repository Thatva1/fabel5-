import pytest

from assistant.risk.sizing import (
    build_plan, confidence_score, long_levels, position_size, short_levels,
)


def test_position_size_never_exceeds_budget():
    shares = position_size(risk_budget=1000, risk_per_share=3.7)
    assert shares == 270
    assert shares * 3.7 <= 1000


def test_position_size_zero_cases():
    assert position_size(1000, 0) == 0
    assert position_size(1000, -1) == 0
    assert position_size(0, 5) == 0


def test_long_levels_ordering():
    levels = long_levels(price=100, atr=4, sma20=97, prior_low=95)
    assert levels["stop"] < levels["entry"] < levels["target"]
    assert levels["entry_zone"][0] <= levels["entry"] <= levels["entry_zone"][1]
    assert levels["risk_per_share"] > 0
    # target = entry + 2.5 * risk
    assert levels["target"] == pytest.approx(
        levels["entry"] + 2.5 * levels["risk_per_share"], abs=0.01)


def test_long_stop_sits_under_structure():
    levels = long_levels(price=100, atr=4, sma20=97, prior_low=95)
    # structure stop = 95 - 0.25*4 = 94; ATR stop = entry - 6; tightest wins
    assert levels["stop"] == pytest.approx(94.0)


def test_long_entry_zone_ordered_when_price_below_sma20():
    # Regression: price crashed below its 20-day MA; the MA must NOT become the
    # zone's lower bound (it sits above price)
    levels = long_levels(price=322, atr=25, sma20=359, prior_low=310)
    assert levels["entry_zone"][0] <= levels["entry_zone"][1]
    assert levels["entry_zone"][1] == 322
    assert levels["stop"] < levels["entry"]


def test_short_entry_zone_ordered_when_price_above_sma20():
    levels = short_levels(price=359, atr=25, sma20=322, prior_high=370)
    assert levels["entry_zone"][0] <= levels["entry_zone"][1]
    assert levels["entry_zone"][0] == 359
    assert levels["stop"] > levels["entry"]


def test_short_levels_ordering():
    levels = short_levels(price=100, atr=4, sma20=103, prior_high=105)
    assert levels["target"] < levels["entry"] < levels["stop"]
    assert levels["target"] == pytest.approx(
        levels["entry"] - 2.5 * levels["risk_per_share"], abs=0.01)


def _snapshot(direction="bullish"):
    return {
        "ticker": "TEST", "price": 100.0, "atr": 4.0, "sma20": 97.0,
        "prior_low_20d": 95.0, "prior_high_20d": 105.0,
        "signals": ["Breakout above 20-day high (99.00)"],
        "direction_hint": direction,
    }


def _thesis(bias="bullish", supports=True, source="claude:test"):
    return {"net_bias": bias, "supports_setup": supports,
            "conviction": 70, "source": source}


def test_build_plan_long_respects_risk_budget():
    plan = build_plan(_snapshot(), _thesis(), risk_budget_instrument_ccy=1000)
    assert plan is not None
    assert plan["direction"] == "long"
    assert plan["risk_amount"] <= 1000 * 1.001
    assert plan["shares"] >= 1
    assert plan["reward_risk"] == pytest.approx(2.5, abs=0.05)


def test_build_plan_short():
    plan = build_plan(_snapshot("bearish"), _thesis("bearish"), 1000)
    assert plan["direction"] == "short"
    assert plan["stop"] > plan["entry"] > plan["target"]


def test_no_plan_without_setup():
    assert build_plan(_snapshot(), _thesis(supports=False), 1000) is None
    assert build_plan(_snapshot(), _thesis(bias="neutral"), 1000) is None


def test_no_plan_when_budget_too_small_for_one_share():
    assert build_plan(_snapshot(), _thesis(), risk_budget_instrument_ccy=1) is None


def test_confidence_rubric_caps_rule_based():
    score, reasons = confidence_score(
        conviction=90, signal_count=4, direction_aligned=True,
        reward_risk=3.0, thesis_source="rule-based")
    assert score <= 60
    assert any("Capped at 60" in r for r in reasons)


def test_confidence_rubric_components():
    score, reasons = confidence_score(80, 2, True, 2.5, "claude:test")
    # 0.5*80 + 0.3*min(100, 2*25+25) + 0.2*(2.5/3*100) = 40 + 22.5 + 16.67 ≈ 79
    assert score == pytest.approx(79, abs=1)
    assert len(reasons) == 3


# ---------- Cap-aware sizing: satisfy BOTH constraints, don't reject ----------

def test_position_size_respects_concentration_cap():
    """Regression: a tight stop used to produce a position far over the cap."""
    # $1000 budget / $1.60 risk = 625 shares, but the cap allows only 240
    assert position_size(1000, 1.60) == 625
    assert position_size(1000, 1.60, max_position_value=240) == 240


def test_position_size_uses_risk_when_it_is_the_tighter_limit():
    assert position_size(1000, 20.0, max_position_value=500) == 50   # risk binds


def test_cap_sizing_never_exceeds_either_constraint():
    entry, risk_per_share, budget, cap_value = 62.36, 1.60, 1000, 15000
    shares = position_size(budget, risk_per_share, max_position_value=cap_value / entry)
    assert shares * risk_per_share <= budget          # max loss respected
    assert shares * entry <= cap_value + entry        # cap respected (whole shares)


def test_build_plan_sizes_down_and_flags_it():
    snapshot = {"ticker": "BAC", "price": 62.36, "atr": 1.2, "sma20": 61.0,
                "prior_low_20d": 60.9, "prior_high_20d": 63.0,
                "signals": ["breakout"], "direction_hint": "bullish"}
    thesis = {"net_bias": "bullish", "supports_setup": True,
              "conviction": 70, "source": "claude:test"}
    plan = build_plan(snapshot, thesis, 1000, "USD", max_position_value=15000)
    assert plan is not None, "a tight-stop setup must still produce a tradable plan"
    assert plan["position_value"] <= 15000 + plan["entry"]
    assert plan["risk_amount"] <= 1000
    assert plan["sized_down_to_cap"] is True
    assert plan["shares"] < plan["shares_by_risk_alone"]
    assert 0 < plan["risk_budget_used_pct"] < 100


def test_build_plan_not_flagged_when_risk_binds():
    snapshot = {"ticker": "X", "price": 100.0, "atr": 8.0, "sma20": 97.0,
                "prior_low_20d": 90.0, "prior_high_20d": 105.0,
                "signals": ["breakout"], "direction_hint": "bullish"}
    thesis = {"net_bias": "bullish", "supports_setup": True,
              "conviction": 70, "source": "claude:test"}
    plan = build_plan(snapshot, thesis, 1000, "USD", max_position_value=100000)
    assert plan["sized_down_to_cap"] is False
    assert plan["risk_budget_used_pct"] > 90


def test_cap_too_small_for_one_share_returns_no_plan():
    snapshot = {"ticker": "X", "price": 5000.0, "atr": 50.0, "sma20": 4900.0,
                "prior_low_20d": 4800.0, "prior_high_20d": 5100.0,
                "signals": ["breakout"], "direction_hint": "bullish"}
    thesis = {"net_bias": "bullish", "supports_setup": True,
              "conviction": 70, "source": "claude:test"}
    assert build_plan(snapshot, thesis, 1000, "USD", max_position_value=100) is None
