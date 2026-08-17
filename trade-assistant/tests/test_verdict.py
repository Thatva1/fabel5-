"""The single answer, and the rule that the model may not overrule the gate."""
from assistant.research import verdict


def idea(direction="long", status="actionable", verdict_name="permitted",
         hard=None, soft=None, bias="bullish", conviction=8, headline="Setup fired"):
    return {
        "ticker": "NVDA",
        "strategy_idea": {"direction": direction, "status": status,
                          "strategy": "ts_momentum", "headline": headline},
        "thesis": {"net_bias": bias, "conviction": conviction,
                   "bull_case": ["Revenue growing", "Trend intact"],
                   "bear_case": ["Rich valuation"],
                   "thesis_summary": "Summary.", "source": "ai"},
        "gate": {"verdict": verdict_name, "hard_failures": hard or [],
                 "soft_flags": soft or []},
        "plan": {"entry": 100.0, "stop": 95.0, "target": 115.0},
        "snapshot": {"regime_label": "Trending up"},
        "context": {"news": {"headlines": [
            {"headline": "Chipmaker raises guidance", "source": "Reuters",
             "datetime": "2026-08-14T10:00:00", "url": "http://x"}]}},
    }


# -- the decision ----------------------------------------------------------

def test_permitted_long_setup_is_a_buy():
    out = verdict.for_idea(idea())
    assert out["action"] == verdict.BUY
    assert out["confidence"] == "high"


def test_permitted_short_setup_is_a_sell():
    out = verdict.for_idea(idea(direction="short", bias="bearish"))
    assert out["action"] == verdict.SELL


def test_no_setup_is_avoid():
    out = verdict.for_idea(idea(status="watch", direction=None))
    assert out["action"] == verdict.AVOID
    assert "No strategy rule fired" in out["because"][0]


def test_hard_failure_is_avoid_and_names_the_blocker():
    out = verdict.for_idea(idea(hard=["Position exceeds the 15% concentration cap."]))
    assert out["action"] == verdict.AVOID
    assert out["blockers"] == ["Position exceeds the 15% concentration cap."]
    assert "concentration cap" in out["because"][0]


def test_a_gated_idea_with_no_strategy_reports_the_gate_reason_not_no_setup():
    """Every idea journalled before the strategy library exists in this shape —
    a gate verdict and no strategy_idea. Reporting those as 'no setup fired'
    discards the gate's actual reason, which is the part worth reading."""
    legacy = idea(status=None, direction=None,
                  hard=["Position would exceed the 60% total exposure cap."])
    legacy["strategy_idea"] = {}
    out = verdict.for_idea(legacy)
    assert out["action"] == verdict.AVOID
    assert "exposure cap" in out["because"][0]
    assert "No setup" not in out["headline"]


def test_an_ungated_idea_with_no_strategy_still_reports_no_setup():
    bare = idea(status=None, direction=None)
    bare["strategy_idea"] = {}
    assert "No setup" in verdict.for_idea(bare)["headline"]


def test_a_watch_item_explains_that_it_is_being_watched():
    """A squeeze under observation and an instrument nobody has looked at both
    reach AVOID; only the soft flags tell them apart."""
    watching = idea(status="watch", direction=None,
                    soft=["Watch item only — no direction yet, so no trade plan "
                          "and no position size were built."])
    watching["strategy_idea"] = {}
    out = verdict.for_idea(watching)
    assert out["action"] == verdict.AVOID
    assert "Watch item only" in out["because"][0]


def test_needs_more_research_is_wait_not_buy():
    out = verdict.for_idea(idea(verdict_name="needs_more_research",
                                soft=["Earnings in 2 days."]))
    assert out["action"] == verdict.WAIT


def test_a_bullish_thesis_cannot_rescue_a_gated_setup():
    """The rule this module exists to enforce. The narrative layer may lower
    confidence and add a caveat; it may never turn a refusal into a trade."""
    out = verdict.for_idea(idea(hard=["No stop: max loss is undefined."],
                                bias="bullish", conviction=10))
    assert out["action"] == verdict.AVOID


# -- conflict --------------------------------------------------------------

def test_long_setup_against_a_bearish_thesis_is_flagged_and_downgraded():
    out = verdict.for_idea(idea(direction="long", bias="bearish"))
    assert out["conflict"] is not None
    assert "disagree" in out["conflict"]
    assert out["confidence"] == "low"
    # The conflict leads the cons, where a reader will actually see it.
    assert out["cons"][0] == out["conflict"]


def test_agreement_is_not_flagged_as_conflict():
    assert verdict.for_idea(idea(direction="long", bias="bullish"))["conflict"] is None


def test_neutral_thesis_is_not_a_conflict():
    assert verdict.for_idea(idea(bias="neutral"))["conflict"] is None


def test_short_setup_presents_the_bear_case_as_the_argument_for_the_trade():
    """Showing the bear case under 'cons' on a short would invert the panel."""
    out = verdict.for_idea(idea(direction="short", bias="bearish"))
    assert "Rich valuation" in out["pros"]


# -- confidence ------------------------------------------------------------

def test_many_soft_flags_lower_confidence():
    out = verdict.for_idea(idea(soft=["a", "b", "c"]))
    assert out["confidence"] == "low"


def test_low_conviction_is_not_high_confidence():
    assert verdict.for_idea(idea(conviction=2))["confidence"] == "low"


# -- news ------------------------------------------------------------------

def test_news_is_carried_through_as_context():
    out = verdict.for_idea(idea())
    assert out["news"][0]["headline"] == "Chipmaker raises guidance"
    assert out["news"][0]["date"] == "2026-08-14"


def test_news_never_changes_the_action():
    """Nothing has measured that a headline predicts a return, so nothing here
    is allowed to trade on one."""
    bullish_news = idea()
    bullish_news["context"] = {"news": {"headlines": [
        {"headline": "Stock soars on blowout earnings", "datetime": "2026-08-14"}]}}
    gated = dict(bullish_news, gate={"verdict": "rejected",
                                     "hard_failures": ["No stop."], "soft_flags": []})
    assert verdict.for_idea(gated)["action"] == verdict.AVOID


def test_missing_news_is_not_an_error():
    bare = idea()
    bare["context"] = None
    assert verdict.for_idea(bare)["news"] == []


# -- open positions --------------------------------------------------------

def position(last=100.0, entry=100.0, stop=95.0, target=115.0, direction="long",
             bars_held=1, **extra):
    return {"ticker": "AAPL", "direction": direction, "last_price": last,
            "entry_price": entry, "stop": stop, "target": target,
            "bars_held": bars_held, "strategy": "ts_momentum", **extra}


def test_untriggered_position_is_a_hold():
    assert verdict.for_position(position())["action"] == verdict.HOLD


def test_price_through_the_stop_is_a_sell():
    out = verdict.for_position(position(last=94.0))
    assert out["action"] == verdict.SELL
    assert "stop" in out["headline"]


def test_price_at_target_is_a_sell():
    out = verdict.for_position(position(last=116.0))
    assert out["action"] == verdict.SELL
    assert "target" in out["headline"]


def short_position(last):
    """A short's stop sits ABOVE entry and its target BELOW — the mirror of a
    long. Getting that backwards inverts every exit test on the position."""
    return position(direction="short", last=last, entry=100.0,
                    stop=105.0, target=85.0)


def test_short_position_holds_between_its_levels():
    assert verdict.for_position(short_position(100.0))["action"] == verdict.HOLD


def test_short_position_stops_out_on_the_way_up():
    out = verdict.for_position(short_position(106.0))
    assert out["action"] == verdict.SELL
    assert "stop" in out["headline"]


def test_short_position_takes_profit_on_the_way_down():
    out = verdict.for_position(short_position(84.0))
    assert out["action"] == verdict.SELL
    assert "target" in out["headline"]


def test_a_falling_price_is_a_gain_for_a_short():
    """Sign convention: a short at 100 now trading at 90 is +10%, not -10%."""
    assert verdict.for_position(short_position(90.0))["move_pct"] == 10.0


def test_holding_limit_forces_a_time_exit():
    out = verdict.for_position(position(bars_held=10), config={"paper": {"max_holding_bars": 10}})
    assert out["action"] == verdict.SELL
    assert "Time exit" in out["headline"]


def test_a_position_that_could_not_be_priced_cannot_be_judged():
    out = verdict.for_position(position(last=None))
    assert out["action"] == verdict.WAIT
    assert "unknown" in out["because"][0]


def test_an_unlicensed_mark_is_disclosed_beside_the_answer():
    out = verdict.for_position(position(price_source="yfinance"))
    assert any("not the licensed IBKR feed" in c for c in out["cons"])


def test_a_failed_mark_warns_that_the_exit_test_used_a_stale_price():
    out = verdict.for_position(position(mark_failed=True))
    assert any("stale mark" in c for c in out["cons"])


# -- summary ---------------------------------------------------------------

def test_summarise_counts_by_action():
    out = verdict.summarise([verdict.for_idea(idea()),
                             verdict.for_idea(idea(status="watch", direction=None))])
    assert out["total"] == 2
    assert out["counts"][verdict.BUY] == 1
    assert out["counts"][verdict.AVOID] == 1
