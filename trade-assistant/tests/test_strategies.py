"""Strategy library, registry, cross-section and regime router.

The important assertions here are the NEGATIVE ones. Any rule set will fire on
something; what makes these strategies worth running is what they refuse — a
rank against a universe that isn't there, a "12-month" signal contaminated by
last month's reversal, a low-beta name that is quietly collapsing, and above all
a ranking that can see the future.
"""
import numpy as np
import pandas as pd
import pytest

from assistant.research import regime, scanner
from assistant.strategies import factors, registry, router
from assistant.strategies.base import STATUS_ACTIONABLE, STATUS_WATCH, Strategy, StrategyContext
from assistant.strategies.cross_section import CrossSection
from assistant.strategies.low_beta import LowBetaStrategy
from assistant.strategies.ts_momentum import TimeSeriesMomentumStrategy
from assistant.strategies.xs_momentum import CrossSectionalMomentumStrategy

# Every strategy here rebalances on a calendar, so the fixtures carry real dates.
# Business days, ending on a month boundary so the default `monthly` rebalance
# gate is open on the last bar unless a test deliberately closes it.
START = "2019-01-01"


def _dates(n, end=None):
    """`n` business days ending on `end` (default: a first-of-month bar)."""
    if end is None:
        # 2024-07-01 is a Monday and the first business day of its month.
        end = "2024-07-01"
    return pd.bdate_range(end=end, periods=n)


def _frame(closes, volumes=None, spread=0.01, end=None):
    closes = pd.Series([float(c) for c in closes])
    volumes = volumes or [1_000_000] * len(closes)
    frame = pd.DataFrame({
        "Open": closes.shift(1).fillna(closes.iloc[0]),
        "High": closes * (1 + spread),
        "Low": closes * (1 - spread),
        "Close": closes,
        "Volume": [float(v) for v in volumes],
    })
    frame.index = _dates(len(frame), end)
    return frame


def _ctx(strategy, closes, volumes=None, snapshot_overrides=None, config=None,
         cross_section=None, benchmark_closes=None, end=None):
    df = _frame(closes, volumes, end=end)
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    snap.update(snapshot_overrides or {})
    config = config or {}
    return StrategyContext(ticker="TEST", df=df, snapshot=snap,
                           regime=regime.classify(snap, config),
                           params=strategy.params_for(config), config=config,
                           cross_section=cross_section,
                           benchmark_closes=benchmark_closes)


def _walk(n, start, drift, noise=0.012, seed=24):
    """A seeded random walk with drift — deterministic, but with the pullbacks
    real trends have. A smooth formula series has no down days at all, which
    pins RSI at 100 and makes realised volatility meaningless."""
    rng = np.random.default_rng(seed)
    price, out = start, []
    for step in rng.normal(drift, noise, n):
        price *= 1 + step
        out.append(price)
    return out


def _uptrend(n=400, start=100.0, daily=0.0015, seed=24):
    return _walk(n, start, daily, seed=seed)


def _downtrend(n=400, start=300.0, daily=0.0015, seed=11):
    return _walk(n, start, -daily, seed=seed)


def _flat(n=400, level=100.0, amplitude=5.0, period=25):
    return [level + amplitude * np.sin(2 * np.pi * i / period) for i in range(n)]


def _rising_benchmark(n=400, seed=3):
    """A market comfortably above its own 200-day average — the overlay's PASS."""
    return pd.Series(_walk(n, 1000.0, 0.0012, noise=0.006, seed=seed), index=_dates(n))


def _falling_benchmark(n=400, seed=5):
    return pd.Series(_walk(n, 3000.0, -0.0012, noise=0.006, seed=seed), index=_dates(n))


def _universe(leader_closes, followers=14, end=None):
    """A CrossSection where TEST is comfortably the strongest name.

    The followers are flat, so TEST's rank is unambiguous and a test that fails
    is telling you about the strategy rather than about the fixture.
    """
    index = _dates(len(leader_closes), end)
    closes = {"TEST": pd.Series(leader_closes, index=index)}
    for i in range(followers):
        closes[f"P{i}"] = pd.Series(_flat(len(leader_closes), level=50.0 + i),
                                    index=index)
    return CrossSection(closes)


# --- cross-section: the look-ahead guarantee ---------------------------------

def test_a_rank_never_sees_a_bar_after_the_date_it_was_asked_for():
    """The one property the whole backtest rests on. If a rank as of a date can
    be moved by appending FUTURE bars, every cross-sectional result this project
    ever produces is inflated and nothing downstream can detect it."""
    index = _dates(400)
    rising = pd.Series(_uptrend(400), index=index)
    flat = pd.Series(_flat(400), index=index)
    as_of = index[300]

    early = CrossSection({"A": rising.iloc[:301], "B": flat.iloc[:301]})
    full = CrossSection({"A": rising, "B": flat})

    assert (early.momentum("A", as_of)["value"]
            == pytest.approx(full.momentum("A", as_of)["value"]))
    assert early.momentum("A", as_of)["rank"] == full.momentum("A", as_of)["rank"]


def test_a_rank_against_nothing_is_refused_rather_than_reported_as_first():
    """Rank 1 of 1 is not a cross-sectional signal, it is an absolute one
    wearing the same clothes."""
    index = _dates(400)
    lonely = CrossSection({"A": pd.Series(_uptrend(400), index=index)})
    assert lonely.momentum("A", index[-1]) is None


def test_momentum_ranking_skips_the_most_recent_month():
    """The '1' in 12-1. A name that spent the last three weeks collapsing must
    still rank on what it did over the year BEFORE that."""
    index = _dates(400)
    closes = list(_uptrend(400))
    crashed = closes[:-15] + [closes[-15] * 0.6] * 15
    universe = CrossSection({"A": pd.Series(crashed, index=index),
                             "B": pd.Series(_flat(400), index=index)})
    skipped = universe.momentum("A", index[-1], lookback_bars=252, skip_bars=21)
    included = universe.momentum("A", index[-1], lookback_bars=252, skip_bars=0)
    assert skipped["value"] > included["value"]


def test_volatility_ranks_the_calmest_name_first():
    index = _dates(400)
    universe = CrossSection({
        "CALM": pd.Series(_walk(400, 100.0, 0.0, noise=0.003, seed=1), index=index),
        "WILD": pd.Series(_walk(400, 100.0, 0.0, noise=0.05, seed=2), index=index),
    })
    assert universe.volatility("CALM", index[-1])["rank"] == 1
    assert universe.volatility("WILD", index[-1])["rank"] == 2


# --- cross-sectional momentum ------------------------------------------------

def test_xs_momentum_buys_a_leader_on_a_rebalance_bar():
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    ctx = _ctx(strategy, closes, cross_section=_universe(closes),
               benchmark_closes=_rising_benchmark())
    ideas = strategy.detect(ctx)
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.direction == "long"
    assert idea.status == STATUS_ACTIONABLE
    assert idea.stop < idea.entry < idea.target
    assert idea.strategy == "xs_momentum"
    assert idea.meta["momentum_rank"] == 1
    assert idea.meta["universe_size"] >= 10


def test_xs_momentum_produces_nothing_without_a_universe_to_rank_against():
    """It must not silently degrade into "this went up a lot", which is a
    different strategy with different evidence behind it."""
    strategy = CrossSectionalMomentumStrategy()
    ctx = _ctx(strategy, _uptrend(400), cross_section=None,
               benchmark_closes=_rising_benchmark())
    assert strategy.detect(ctx) == []


def test_xs_momentum_refuses_a_universe_too_small_to_be_one():
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    ctx = _ctx(strategy, closes, cross_section=_universe(closes, followers=3),
               benchmark_closes=_rising_benchmark())
    assert strategy.detect(ctx) == []


def test_xs_momentum_ignores_a_name_outside_the_top_n():
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    config = {"strategies": {"xs_momentum": {"top_n": 1}}}
    index = _dates(400)
    # Two names beat TEST, so its rank is 3 and top_n is 1.
    universe = CrossSection({
        "TEST": pd.Series(closes, index=index),
        "FAST1": pd.Series(_walk(400, 100.0, 0.004, seed=7), index=index),
        "FAST2": pd.Series(_walk(400, 100.0, 0.005, seed=8), index=index),
    })
    ctx = _ctx(strategy, closes, config=config, cross_section=universe,
               benchmark_closes=_rising_benchmark())
    assert universe.momentum("TEST", index[-1])["rank"] == 3
    assert strategy.detect(ctx) == []


def test_xs_momentum_stands_aside_when_the_market_is_below_its_own_trend():
    """Momentum's characteristic loss is a crash off a market bottom, not a slow
    bleed. This overlay is the whole defence against it."""
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    ctx = _ctx(strategy, closes, cross_section=_universe(closes),
               benchmark_closes=_falling_benchmark())
    assert strategy.detect(ctx) == []


def test_xs_momentum_fails_closed_when_the_market_cannot_be_measured():
    """"I could not tell whether the market was falling" and "the market was not
    falling" must not be the same answer."""
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    ctx = _ctx(strategy, closes, cross_section=_universe(closes),
               benchmark_closes=None)
    assert strategy.detect(ctx) == []
    # With the overlay switched off, the same setup is allowed through.
    config = {"strategies": {"xs_momentum": {"require_market_trend": False}}}
    relaxed = _ctx(strategy, closes, config=config, cross_section=_universe(closes),
                   benchmark_closes=None)
    assert len(strategy.detect(relaxed)) == 1


def test_xs_momentum_only_acts_on_a_rebalance_bar():
    """A monthly strategy that re-evaluates daily is a daily strategy with a
    slow signal, and it will trade twenty times more than the paper it cites."""
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    # Mid-month: the previous bar is in the same month, so the gate is shut.
    mid_month = _ctx(strategy, closes, cross_section=_universe(closes, end="2024-07-17"),
                     benchmark_closes=_rising_benchmark(), end="2024-07-17")
    assert strategy.detect(mid_month) == []

    config = {"strategies": {"xs_momentum": {"rebalance": "any"}}}
    daily = _ctx(strategy, closes, config=config,
                 cross_section=_universe(closes, end="2024-07-17"),
                 benchmark_closes=_rising_benchmark(), end="2024-07-17")
    assert len(strategy.detect(daily)) == 1


def test_xs_momentum_skips_a_leader_whose_own_volatility_has_exploded():
    strategy = CrossSectionalMomentumStrategy()
    closes = _uptrend(400)
    config = {"strategies": {"xs_momentum": {"max_vol_pct": 1.0}}}
    ctx = _ctx(strategy, closes, config=config, cross_section=_universe(closes),
               benchmark_closes=_rising_benchmark())
    assert strategy.detect(ctx) == []


# --- time-series momentum ----------------------------------------------------

def test_ts_momentum_goes_long_a_positive_trailing_year():
    strategy = TimeSeriesMomentumStrategy()
    ideas = strategy.detect(_ctx(strategy, _uptrend(400)))
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.direction == "long"
    assert idea.stop < idea.entry < idea.target
    assert idea.meta["trailing_return_pct"] > 0
    assert idea.meta["inverse_vol_weight"] is not None


def test_ts_momentum_goes_short_a_negative_trailing_year():
    strategy = TimeSeriesMomentumStrategy()
    ideas = strategy.detect(_ctx(strategy, _downtrend(400)))
    assert len(ideas) == 1
    assert ideas[0].direction == "short"
    assert ideas[0].target < ideas[0].entry < ideas[0].stop


def test_ts_momentum_needs_no_universe_at_all():
    """This is the point of running it alongside the rotation: its signal is
    absolute, so it still works on a single instrument — and it goes flat when
    everything is falling instead of rotating into whatever falls least."""
    strategy = TimeSeriesMomentumStrategy()
    ctx = _ctx(strategy, _uptrend(400), cross_section=None, benchmark_closes=None)
    assert len(strategy.detect(ctx)) == 1


def test_ts_momentum_refuses_a_positive_year_whose_trend_has_since_broken():
    """Up 30% over the year and collapsing for the last four months is a
    positive signal on a broken trend. The added moving-average agreement is
    what stops the rule buying it."""
    strategy = TimeSeriesMomentumStrategy()
    # A year of strong gains, then a long slide that leaves price under its
    # 200-day average but still above where it stood twelve months ago.
    rally = _uptrend(300, start=100.0, daily=0.006)
    slide = [rally[-1] * (1 - 0.0035) ** (i + 1) for i in range(100)]
    closes = rally + slide
    ctx = _ctx(strategy, closes)
    assert ctx.df["Close"].iloc[-1] > ctx.df["Close"].iloc[-353]   # year is positive
    assert strategy.detect(ctx) == []

    config = {"strategies": {"ts_momentum": {"require_ma_agreement": False}}}
    unmodified = _ctx(strategy, closes, config=config)
    assert len(strategy.detect(unmodified)) == 1     # the published rule takes it


def test_ts_momentum_needs_a_full_year_of_history():
    strategy = TimeSeriesMomentumStrategy()
    assert strategy.detect(_ctx(strategy, _uptrend(120))) == []


def test_ts_momentum_only_acts_on_a_rebalance_bar():
    strategy = TimeSeriesMomentumStrategy()
    assert strategy.detect(_ctx(strategy, _uptrend(400), end="2024-07-17")) == []


# --- low beta ----------------------------------------------------------------

def _calm_and_index(n=400):
    """A calm name and a market that moves twice as much — beta near 0.5."""
    index = _dates(n)
    market = _walk(n, 1000.0, 0.0012, noise=0.012, seed=41)
    market_returns = pd.Series(market, index=index).pct_change().fillna(0.0)
    calm = [100.0]
    for r in market_returns.iloc[1:]:
        calm.append(calm[-1] * (1 + r * 0.45))
    return calm, pd.Series(market, index=index)


def test_low_beta_buys_a_calm_name_that_is_still_trending():
    strategy = LowBetaStrategy()
    calm, market = _calm_and_index()
    ctx = _ctx(strategy, calm, benchmark_closes=market)
    ideas = strategy.detect(ctx)
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.direction == "long"
    assert idea.meta["beta"] < 0.85
    assert idea.stop < idea.entry < idea.target


def test_low_beta_refuses_a_name_that_moves_with_its_index():
    strategy = LowBetaStrategy()
    _, market = _calm_and_index()
    # The name IS the index: beta 1.0, well over the ceiling.
    ctx = _ctx(strategy, list(market.values), benchmark_closes=market)
    assert strategy.detect(ctx) == []


def test_low_beta_refuses_a_calm_decline():
    """Beta says how much a share moves WITH the index, not which way it is
    going. A stock that has quietly halved scores beautifully on beta alone."""
    strategy = LowBetaStrategy()
    _, market = _calm_and_index()
    sinking = _downtrend(400, start=300.0, daily=0.002)
    ctx = _ctx(strategy, sinking, benchmark_closes=market)
    assert strategy.detect(ctx) == []


def test_low_beta_needs_a_benchmark_to_be_low_beta_against():
    strategy = LowBetaStrategy()
    calm, _ = _calm_and_index()
    assert strategy.detect(_ctx(strategy, calm, benchmark_closes=None)) == []


def test_low_beta_uses_the_cross_section_when_one_is_available():
    """With a universe it ranks volatility against peers; the absolute ceiling
    is only the fallback. A name that fails the peer rank must be refused even
    though it passes the absolute test."""
    strategy = LowBetaStrategy()
    calm, market = _calm_and_index()
    index = _dates(400)
    closes = {"TEST": pd.Series(calm, index=index)}
    for i in range(9):
        # Nine peers that are all calmer than TEST, so it lands in the top decile
        # of volatility rather than the quietest 40%.
        closes[f"P{i}"] = pd.Series(_walk(400, 100.0, 0.0, noise=0.0005, seed=i),
                                    index=index)
    # Without a universe the same name passes on the absolute ceiling, so the
    # refusal below is the peer rank doing the work and nothing else.
    assert len(strategy.detect(_ctx(strategy, calm, benchmark_closes=market))) == 1

    ranked = _ctx(strategy, calm, benchmark_closes=market,
                  cross_section=CrossSection(closes))
    assert strategy.detect(ranked) == []


# --- shared factor machinery -------------------------------------------------

def test_the_rebalance_gate_fails_closed_on_an_undated_index():
    """A frame with bare row numbers cannot answer "is this the first bar of the
    month". Failing open would silently multiply turnover twentyfold."""
    undated = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
    assert factors.is_rebalance_bar(undated, "monthly") is False
    assert factors.is_rebalance_bar(undated, "any") is True


def test_a_volatility_filter_does_not_pass_what_it_could_not_measure():
    short = pd.Series([100.0, 101.0])
    vol, passed = factors.volatility_check(short, 60, 30.0)
    assert vol is None and passed is False


# --- interface guarantees ----------------------------------------------------

def test_a_strategy_cannot_emit_a_long_with_an_upside_down_stop():
    """base.build() is the last line of defence against arithmetic mistakes in a
    strategy — a negative risk-per-share would produce a nonsense position size."""
    strategy = TimeSeriesMomentumStrategy()
    ctx = _ctx(strategy, _uptrend(400))
    assert strategy.build(ctx, "long", entry=100, stop=110, target=120, reasons=[]) is None
    assert strategy.build(ctx, "long", entry=100, stop=90, target=95, reasons=[]) is None
    assert strategy.build(ctx, "short", entry=100, stop=90, target=80, reasons=[]) is None
    good = strategy.build(ctx, "long", entry=100, stop=95, target=115, reasons=[])
    assert good.risk_per_share == 5.0 and good.reward_risk == 3.0


def test_every_idea_is_tagged_with_its_strategy_and_regime():
    strategy = TimeSeriesMomentumStrategy()
    idea = strategy.detect(_ctx(strategy, _uptrend(400)))[0].to_dict()
    assert idea["strategy"] and idea["strategy_label"] and idea["regime"]
    assert idea["reasons"], "an idea must explain itself"


def test_every_idea_explains_the_research_it_implements():
    """These are published rules, not house rules. The reasons the user reads
    must say what the signal actually was, not just that something fired."""
    strategy = TimeSeriesMomentumStrategy()
    reasons = " ".join(strategy.detect(_ctx(strategy, _uptrend(400)))[0].reasons)
    assert "12-month return" in reasons


def test_config_can_disable_a_strategy():
    config = {"strategies": {"xs_momentum": {"enabled": False}}}
    names = [s.name for s in registry.enabled_strategies(config)]
    assert "xs_momentum" not in names
    assert "ts_momentum" in names


def test_config_can_restrict_a_strategy_to_one_direction():
    """Shorting carries costs longs do not — borrow fees, stamp duty on the
    closing purchase, unlimited theoretical loss — and every backtest this
    project has run has lost money short. This is how you switch one side off."""
    df = _frame(_downtrend(400))
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})

    both = router.route("TEST", df, snap, {})
    assert any(i.strategy == "ts_momentum" and i.direction == "short"
               for i in both["ideas"])

    config = {"strategies": {"ts_momentum": {"directions": ["long"]}}}
    shorts_off = router.route("TEST", df, snap, config)
    assert not any(i.direction == "short" for i in shorts_off["ideas"])


def test_direction_filter_never_drops_a_watch_item():
    """A watch item has no direction; filtering it out would hide the warning
    entirely. No strategy currently emits one, but the interface still promises
    it, and the promise is what a later strategy will be written against."""
    class Watcher(Strategy):
        name, label, description = "watcher", "Watcher", "always watches"
        regimes = regime.ALL_REGIMES

        def detect(self, ctx):
            return [self.watch(ctx, headline="heads up", reasons=["because"])]

    original = registry.BUILTIN
    registry.BUILTIN = original + (Watcher,)
    try:
        df = _frame(_flat())
        snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
        config = {"strategies": {"watcher": {"directions": ["long"]}}}
        ideas = router.route("TEST", df, snap, config)["ideas"]
        assert any(i.status == STATUS_WATCH for i in ideas)
    finally:
        registry.BUILTIN = original


def test_config_can_move_a_strategy_to_a_different_regime():
    config = {"strategies": {"low_beta": {"regimes": ["TRENDING_DOWN"]}}}
    names = [s.name for s in registry.strategies_for_regime("TRENDING_DOWN", config)]
    assert "low_beta" in names


def test_config_overrides_a_single_setting_without_losing_the_rest():
    config = {"strategies": {"xs_momentum": {"top_n": 5}}}
    params = CrossSectionalMomentumStrategy().params_for(config)
    assert params["top_n"] == 5
    assert params["skip_bars"] == CrossSectionalMomentumStrategy.defaults["skip_bars"]


def test_every_registered_strategy_declares_its_regimes_and_a_description():
    for strategy in registry.all_strategies():
        assert strategy.name and strategy.label and strategy.description
        assert strategy.regimes, f"{strategy.name} must declare which regimes it runs in"
        for r in strategy.regimes:
            assert r in regime.ALL_REGIMES


def test_every_regime_has_at_least_one_strategy_that_runs_in_it():
    """A regime no strategy is registered for is a silent hole: the router
    reports "no enabled strategy runs in this market" and the user cannot tell
    that from "nothing qualified today"."""
    for market in regime.ALL_REGIMES:
        assert registry.strategies_for_regime(market, {}), f"{market} has no strategy"


# --- router ------------------------------------------------------------------

def test_router_passes_the_cross_section_through_to_the_strategies():
    closes = _uptrend(400)
    df = _frame(closes)
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    result = router.route("TEST", df, snap, {}, cross_section=_universe(closes),
                          benchmark_closes=_rising_benchmark())
    assert any(i.strategy == "xs_momentum" for i in result["ideas"])


def test_router_still_works_with_no_cross_section_at_all():
    """Analysing one ticker from the dashboard has no universe. The strategies
    that need one go quiet; the ones that don't still run."""
    df = _frame(_uptrend(400))
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    result = router.route("TEST", df, snap, {})
    assert not any(i.strategy == "xs_momentum" for i in result["ideas"])
    assert any(i.strategy == "ts_momentum" for i in result["ideas"])


def test_router_produces_nothing_without_enough_history():
    snap = {"ticker": "TEST", "price": 10, "adx": 30}
    result = router.route("TEST", None, snap, {})
    assert result["ideas"] == []
    assert result["regime"]["regime"] == regime.UNKNOWN


def test_router_survives_a_strategy_that_raises():
    """One broken strategy must not take down the whole scan."""
    class Exploding(Strategy):
        name, label, description = "boom", "Boom", "always fails"
        regimes = (regime.SIDEWAYS,)

        def detect(self, ctx):
            raise RuntimeError("kaboom")

    original = registry.BUILTIN
    registry.BUILTIN = original + (Exploding,)
    try:
        df = _frame(_flat())
        snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
        result = router.route("TEST", df, snap, {})
        assert any("kaboom" in note for note in result["notes"])
        assert "ts_momentum" in result["strategies_run"]
    finally:
        registry.BUILTIN = original


def test_router_explains_itself_when_nothing_fires():
    """Silence must be explained. "The rotation ran and found nothing" and
    "nothing ran at all" look identical on a dashboard unless the router says
    which — and with monthly strategies most days are silent by design."""
    df = _frame(_flat())
    snap = scanner.scan_ticker("TEST", df, {"min_history_days": 60})
    result = router.route("TEST", df, snap, {})
    assert result["ideas"] == []
    assert any("no setup met its full rule set" in n for n in result["notes"])
