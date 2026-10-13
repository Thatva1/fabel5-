"""The main agent, its country sub-versions, their tuning, and the learning agent.

No network and no language model: the model and the replay test are injected.
"""
import copy

import pytest

from assistant.agents import learner, tuning, versions
from assistant.paper import screen

ROWS = [
    {"symbol": "HSBA.L", "country": "United Kingdom", "currency": "GBp"},
    {"symbol": "NESN.SW", "country": "Switzerland", "currency": "CHF"},
    {"symbol": "GLEN.L", "country": "Switzerland", "currency": "GBp"},
    {"symbol": "ENI.MI", "country": "Italy", "currency": "EUR"},
]

CONFIG = {
    "base_currency": "GBP",
    "account": {"portfolio_value": 1_000_000},
    "strategies": {"ts_momentum": {"enabled": True, "stop_atr": 3.0, "lookback_bars": 252},
                   "pead_drift": {"enabled": False}},
    "strategy_priority": ["ts_momentum"],
    "paper": {"trading_currency": "USD", "screen": {"universe": "us_all", "min_price": 5.0}},
    "scanner": {"benchmark": "^GSPC"},
    "kelly": {"enabled": True},
    "providers": {"ibkr": {"enabled": True, "port": 7497}},
    "intraday": {"trading_currency": "USD"},
    "agents": {
        "enabled": True, "total_capital": 1_000_000,
        "versions": {
            "us": {"label": "United States", "currency": "USD", "book": "main",
                   "weight": 60, "prices": "ibkr", "intraday": True, "intraday_share": 0.5},
            "uk": {"label": "United Kingdom", "currency": "GBP", "countries": ["United Kingdom"],
                   "weight": 30, "prices": "ibkr", "benchmark": "^FTSE"},
            "switzerland": {"label": "Switzerland", "currency": "CHF",
                            "countries": ["Switzerland"], "weight": 10,
                            "prices": "yfinance", "benchmark": "^SSMI"},
            "italy": {"label": "Italy", "currency": "EUR", "countries": ["Italy"],
                      "weight": 5, "enabled": False},
        },
    },
}


# --- versions -------------------------------------------------------------------

def test_money_is_split_by_weight_over_enabled_versions():
    money = versions.allocation(CONFIG)
    assert set(money) == {"us", "uk", "switzerland"}
    assert money["us"]["total"] == pytest.approx(600_000)
    assert money["us"]["intraday"] == pytest.approx(300_000)
    assert money["us"]["daily"] == pytest.approx(300_000)
    assert money["uk"]["total"] == pytest.approx(300_000)
    assert money["uk"]["intraday"] == 0


def test_every_version_starts_with_the_main_versions_strategies(tmp_path):
    out = versions.config_for("switzerland", CONFIG, rows=ROWS, agents_dir=str(tmp_path))
    assert out["strategies"] == CONFIG["strategies"]          # no pre-picked advantage
    assert out["strategy_priority"] == CONFIG["strategy_priority"]


def test_a_version_carries_only_its_market_facts(tmp_path):
    out = versions.config_for("switzerland", CONFIG, rows=ROWS, agents_dir=str(tmp_path))
    assert out["paper"]["trading_currency"] == "CHF"
    assert out["paper"]["screen"]["universe"] == ["NESN.SW"]   # not the London-listed Swiss
    assert out["paper"]["starting_equity"] == pytest.approx(100_000)
    assert out["paper"]["carry_forward_equity"] is False
    assert out["paper"]["universe_history_cache_hours"] is None
    assert out["scanner"]["benchmark"] == "^SSMI"
    assert out["kelly"]["enabled"] is False
    assert out["providers"]["ibkr"]["enabled"] is False


def test_the_us_version_keeps_the_main_book_and_screen(tmp_path):
    out = versions.config_for("us", CONFIG, agents_dir=str(tmp_path))
    assert out["paper"]["screen"]["universe"] == "us_all"
    assert out["kelly"]["enabled"] is True
    assert out["providers"]["ibkr"]["enabled"] is True
    assert out["intraday"]["starting_equity_base"] == pytest.approx(300_000)
    assert versions.book_path("us", CONFIG) is None
    assert versions.book_path("uk", CONFIG, str(tmp_path)).endswith("uk/book.json")


def test_building_a_version_never_changes_the_main_config(tmp_path):
    before = copy.deepcopy(CONFIG)
    versions.config_for("uk", CONFIG, rows=ROWS, agents_dir=str(tmp_path))
    assert CONFIG == before


def test_country_universe_never_touches_the_shared_cache(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(screen, "load_cached", lambda: calls.append("load"))
    monkeypatch.setattr(screen, "save_cached", lambda *a: calls.append("save"))
    monkeypatch.setattr(screen, "apply", lambda symbols, config, progress_cb=None:
                        (list(symbols), {"considered": len(symbols)}))
    config = versions.config_for("uk", CONFIG, rows=ROWS, agents_dir=str(tmp_path))
    symbols, _, cached = screen.tradable_universe(config, router=None)
    assert symbols == ["HSBA.L"] and cached is False and calls == []


# --- tuning ---------------------------------------------------------------------

def test_tuning_one_country_leaves_the_others_on_the_main_version(tmp_path):
    d = str(tmp_path)
    tuning.apply("uk", CONFIG, "ts_momentum", "stop_atr", 4.0, by="test", reason="r", agents_dir=d)
    uk = versions.config_for("uk", CONFIG, rows=ROWS, agents_dir=d)
    swiss = versions.config_for("switzerland", CONFIG, rows=ROWS, agents_dir=d)
    assert uk["strategies"]["ts_momentum"]["stop_atr"] == 4.0
    assert swiss["strategies"]["ts_momentum"]["stop_atr"] == 3.0
    assert tuning.differences("uk", CONFIG, d) == [
        {"strategy": "ts_momentum", "param": "stop_atr", "main": 3.0, "value": 4.0}]


def test_tuned_values_stay_within_half_and_double_the_main_value():
    with pytest.raises(ValueError):
        tuning.check(CONFIG, "ts_momentum", "stop_atr", 7.0)
    with pytest.raises(ValueError):
        tuning.check(CONFIG, "ts_momentum", "stop_atr", 1.0)
    assert tuning.check(CONFIG, "ts_momentum", "lookback_bars", 300.4) == 300   # int stays int


def test_only_whitelisted_settings_can_be_tuned():
    with pytest.raises(ValueError):
        tuning.check(CONFIG, "ts_momentum", "regimes", ["X"])
    with pytest.raises(ValueError):
        tuning.check(CONFIG, "no_such_strategy", "stop_atr", 3.0)
    with pytest.raises(ValueError):
        tuning.check(CONFIG, "ts_momentum", "enabled", "yes")


def test_undo_puts_the_old_value_back_and_cannot_repeat(tmp_path):
    d = str(tmp_path)
    row = tuning.apply("uk", CONFIG, "ts_momentum", "stop_atr", 4.0, by="test", reason="r",
                       agents_dir=d)
    tuning.undo("uk", CONFIG, row["id"], agents_dir=d)
    assert tuning.differences("uk", CONFIG, d) == []
    assert tuning.current("uk", CONFIG, "ts_momentum", "stop_atr", d) == 3.0
    actions = [r["action"] for r in tuning.history("uk", d)]
    assert actions == ["applied", "undone"]
    with pytest.raises(ValueError):
        tuning.undo("uk", CONFIG, row["id"], agents_dir=d)


# --- the learning agent -----------------------------------------------------------

class _NoNetwork:
    def get_prices(self, *a, **k):
        raise RuntimeError("no network in tests")


NONET = _NoNetwork()

def _score(holdout_mean, holdout_n=40, early_mean=0.1, early_n=80):
    return {"holdout": {"trades": holdout_n, "mean_r": holdout_mean,
                        "total_r": round(holdout_mean * holdout_n, 2)},
            "earlier": {"trades": early_n, "mean_r": early_mean,
                        "total_r": round(early_mean * early_n, 2)}}


def test_a_change_must_improve_recent_results_without_hurting_earlier_ones():
    proposal = {"strategy": "ts_momentum", "param": "stop_atr", "value": 4.0}
    assert learner.verdict(proposal, 3.0, _score(0.05), _score(0.15))[0] is True
    assert learner.verdict(proposal, 3.0, _score(0.05), _score(0.06))[0] is False   # too small
    assert learner.verdict(proposal, 3.0, _score(0.05), _score(0.15, early_mean=0.0))[0] is False
    assert learner.verdict(proposal, 3.0, _score(0.05), _score(0.5, holdout_n=10))[0] is False


def test_switching_a_strategy_off_needs_it_to_lose_money_here():
    off = {"strategy": "ts_momentum", "param": "enabled", "value": False}
    assert learner.verdict(off, True, _score(-0.05, early_mean=0.0))[0] is True
    assert learner.verdict(off, True, _score(0.02))[0] is False
    assert learner.verdict(off, True, _score(-0.05, holdout_n=5))[0] is False


def _fake_book(monkeypatch, closed):
    class Book:
        started = True
        positions, pending, sessions, closed_list = [], [], [], closed
        ledger_path = None
        as_of = None

        def summary(self):
            return {"closed_trades": len(closed)}
    monkeypatch.setattr(versions, "load_book", lambda *a, **k: Book())
    monkeypatch.setattr(learner, "_closed_trades", lambda book: closed)


def test_no_change_is_tested_for_a_strategy_without_enough_trades(monkeypatch, tmp_path):
    _fake_book(monkeypatch, [{"strategy": "ts_momentum", "r_multiple": 1.0, "pnl": 10}] * 5)
    tested = []
    row = learner.learn(
        "uk", CONFIG, router=NONET, agents_dir=str(tmp_path),
        model_fn=lambda bundle, config: {"journal": "quiet day", "proposals": [
            {"strategy": "ts_momentum", "param": "stop_atr", "value": 4.0, "reason": "r"}]},
        test_fn=lambda *a: tested.append(a))
    assert tested == []
    assert row["proposals"][0]["result"] == "not tested"
    assert tuning.differences("uk", CONFIG, str(tmp_path)) == []


def test_a_passing_change_is_applied_once_and_logged(monkeypatch, tmp_path):
    _fake_book(monkeypatch, [{"strategy": "ts_momentum", "r_multiple": -0.5, "pnl": -5}] * 25)
    proposals = [{"strategy": "ts_momentum", "param": "stop_atr", "value": 4.0, "reason": "a"},
                 {"strategy": "ts_momentum", "param": "lookback_bars", "value": 200, "reason": "b"}]
    row = learner.learn(
        "uk", CONFIG, router=NONET, agents_dir=str(tmp_path),
        model_fn=lambda bundle, config: {"journal": "stops too tight", "proposals": proposals},
        test_fn=lambda *a: {"passed": True, "why": "held up"})
    assert [p["result"] for p in row["proposals"]] == ["applied", "not tested"]   # one a day
    assert tuning.current("uk", CONFIG, "ts_momentum", "stop_atr", str(tmp_path)) == 4.0
    assert tuning.current("switzerland", CONFIG, "ts_momentum", "stop_atr", str(tmp_path)) == 3.0
    assert versions.journal("uk", str(tmp_path))[0]["note"] == "stops too tight"


def test_a_failing_change_is_logged_and_not_applied(monkeypatch, tmp_path):
    _fake_book(monkeypatch, [{"strategy": "ts_momentum", "r_multiple": -0.5, "pnl": -5}] * 25)
    learner.learn(
        "uk", CONFIG, router=NONET, agents_dir=str(tmp_path),
        model_fn=lambda bundle, config: {"journal": "n", "proposals": [
            {"strategy": "ts_momentum", "param": "stop_atr", "value": 4.0, "reason": "a"}]},
        test_fn=lambda *a: {"passed": False, "why": "did not hold up"})
    assert tuning.differences("uk", CONFIG, str(tmp_path)) == []
    assert tuning.history("uk", str(tmp_path))[-1]["action"] == "rejected"


def test_a_recently_changed_setting_is_left_alone(monkeypatch, tmp_path):
    _fake_book(monkeypatch, [{"strategy": "ts_momentum", "r_multiple": -0.5, "pnl": -5}] * 25)
    tuning.apply("uk", CONFIG, "ts_momentum", "stop_atr", 4.0, by="test", reason="r",
                 agents_dir=str(tmp_path))
    row = learner.learn(
        "uk", CONFIG, router=NONET, agents_dir=str(tmp_path),
        model_fn=lambda bundle, config: {"journal": "n", "proposals": [
            {"strategy": "ts_momentum", "param": "stop_atr", "value": 5.0, "reason": "a"}]},
        test_fn=lambda *a: {"passed": True, "why": "x"})
    assert row["proposals"][0]["result"] == "not tested"
    assert "20 days" in row["proposals"][0]["why"]


def test_a_model_failure_writes_a_note_and_changes_nothing(monkeypatch, tmp_path):
    _fake_book(monkeypatch, [])

    def broken(bundle, config):
        raise RuntimeError("no network")
    row = learner.learn("uk", CONFIG, router=NONET, agents_dir=str(tmp_path), model_fn=broken)
    assert "skipped" in row["note"]
    assert tuning.history("uk", str(tmp_path)) == []


def test_unstarted_engines_count_as_cash_not_as_a_loss(monkeypatch, tmp_path):
    class Empty:
        started, positions, pending, cash, as_of = False, [], [], 0.0, None

        def summary(self):
            return {}
    from assistant.paper import intraday_book
    monkeypatch.setattr(versions, "load_book", lambda *a, **k: Empty())
    monkeypatch.setattr(intraday_book, "load_book", lambda *a, **k: Empty())
    monkeypatch.setattr(versions, "fx_rate", lambda router, a, b: 1.0)
    firm = versions.firm(CONFIG, router=object(), agents_dir=str(tmp_path))
    assert firm["total"]["equity"] == pytest.approx(1_000_000)
    assert firm["total"]["cash"] == pytest.approx(1_000_000)
    assert firm["return_pct"] == pytest.approx(0.0)
    names = {row["name"] for row in firm["by_country"]}
    assert names == {"United States", "United Kingdom", "Switzerland"}   # intraday folds into US
