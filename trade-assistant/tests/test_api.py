"""Runtime tests for the Flask API and provider failure handling.

These cover the user-facing failure modes the math tests can't: upstream
outages, missing records, and error-code correctness.
"""
import os
import tempfile

import pytest

# The dashboard imports the whole data stack, so the Flask app genuinely needs
# yfinance to start. Skip the module rather than aborting collection on a
# clean checkout (audit finding D-8).
pytest.importorskip("yfinance", reason="the web server imports the data providers")

from .optional_deps import requires_ib   # noqa: E402

from assistant import journal
from assistant.providers.base import ProviderUnavailable


@pytest.fixture(autouse=True)
def temp_journal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(journal, "DB_PATH", os.path.join(tmp, "test.db"))
        yield


@pytest.fixture
def client():
    from assistant.web.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class OutageRouter:
    """Every data call fails, as during a DNS/network/Yahoo outage."""
    def get_prices(self, ticker, period="1y"):
        raise ProviderUnavailable("yfinance: market data request failed (simulated outage)")

    def search_symbol(self, query, max_results=5):
        raise ProviderUnavailable("yfinance: search failed (simulated outage)")

    def get_fx_rates(self, currencies, base):
        return {}

    def provider_status(self):
        return []

    def suggest(self, query, limit=8):
        return []            # universe search also unavailable during an outage

    def universe_size(self):
        return 0


# ---------- Upstream outages are 503, not 500 ----------

def test_analyze_during_outage_returns_503(client, monkeypatch):
    monkeypatch.setattr("assistant.pipeline.get_router", lambda cfg: OutageRouter())
    resp = client.post("/api/analyze/AAPL")
    assert resp.status_code == 503, f"expected 503, got {resp.status_code}"
    body = resp.get_json()
    assert body["retryable"] is True
    assert "temporarily unavailable" in body["error"].lower()


def test_unknown_symbol_still_returns_404(client, monkeypatch):
    """An outage and a bad ticker must not look the same to the user."""
    class NoMatchRouter(OutageRouter):
        def search_symbol(self, query, max_results=5):
            return []

    monkeypatch.setattr("assistant.pipeline.get_router", lambda cfg: NoMatchRouter())
    resp = client.post("/api/analyze/NOTAREALTICKER")
    assert resp.status_code == 404
    assert "couldn't find" in resp.get_json()["error"]


def test_watchlist_add_during_outage_is_not_500(client, monkeypatch):
    monkeypatch.setattr("assistant.pipeline.get_router", lambda cfg: OutageRouter())
    resp = client.post("/api/watchlist/add", json={"ticker": "AAPL"})
    assert resp.status_code == 404          # "no market data found"
    assert resp.status_code != 500


# ---------- Missing records must not report success ----------

def test_decision_on_missing_idea_returns_404(client):
    resp = client.post("/api/ideas/999999/decision", json={"decision": "approved"})
    assert resp.status_code == 404, "updating a nonexistent idea reported success"
    assert "does not exist" in resp.get_json()["error"]


def test_outcome_on_missing_idea_returns_404(client):
    resp = client.post("/api/ideas/999999/outcome", json={"outcome": "win"})
    assert resp.status_code == 404
    assert "does not exist" in resp.get_json()["error"]


def test_journal_update_helpers_report_missing_rows():
    assert journal.set_decision(999999, "approved") is False
    assert journal.record_outcome(999999, "win") is False


def test_decision_on_existing_idea_succeeds(client):
    idea_id = journal.add_idea(
        {"ticker": "TEST"}, {"thesis_summary": "t"}, None,
        {"verdict": "rejected", "hard_failures": [], "soft_flags": []})
    resp = client.post(f"/api/ideas/{idea_id}/decision", json={"decision": "approved"})
    assert resp.status_code == 200
    assert journal.get_idea(idea_id)["decision"] == "approved"


def test_invalid_decision_value_rejected(client):
    resp = client.post("/api/ideas/1/decision", json={"decision": "yolo"})
    assert resp.status_code == 400


# ---------- Execution endpoints degrade cleanly ----------

def test_prepare_order_when_execution_disabled_is_409_not_500(client):
    resp = client.post("/api/ideas/1/prepare-order")
    assert resp.status_code == 409
    assert "disabled" in resp.get_json()["error"]


def test_confirm_order_when_execution_disabled_is_409(client):
    resp = client.post("/api/orders/1/confirm", json={"confirmation": "TEST"})
    assert resp.status_code == 409


@requires_ib
def test_gateway_unreachable_maps_to_503(client, monkeypatch):
    """A dead IB Gateway is a service problem, not an app crash."""
    from assistant.broker.ibkr import GatewayUnreachable

    def boom(*args, **kwargs):
        raise GatewayUnreachable("Could not reach IB Gateway/TWS at 127.0.0.1:7497")

    monkeypatch.setattr("assistant.execution.prepare_ticket", boom)
    monkeypatch.setattr("assistant.core.config.load_config", lambda: {
        "execution": {"enabled": True}, "account": {}, "base_currency": "USD"})
    resp = client.post("/api/ideas/1/prepare-order")
    assert resp.status_code == 503
    assert resp.get_json()["retryable"] is True


# ---------- Execution on/off toggle ----------

@pytest.fixture
def temp_config(monkeypatch, tmp_path):
    """Isolate config.yaml so toggle tests never touch the real one.

    state.yaml is derived from CONFIG_PATH's directory, so pointing CONFIG_PATH
    at the temp copy isolates the mutable state file too.
    """
    import shutil

    from assistant.core import config as cfg
    real = cfg.CONFIG_PATH
    tmp = tmp_path / "config.yaml"
    shutil.copy(real, tmp)
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp))
    monkeypatch.setattr("assistant.web.server.load_config", cfg.load_config)
    monkeypatch.setattr("assistant.web.server.set_execution_enabled",
                        cfg.set_execution_enabled)
    monkeypatch.setattr("assistant.web.server.add_to_watchlist", cfg.add_to_watchlist)
    monkeypatch.setattr("assistant.web.server.remove_from_watchlist",
                        cfg.remove_from_watchlist)
    yield cfg


def test_toggle_on_requires_typed_confirmation(client, temp_config):
    resp = client.post("/api/execution/toggle", json={"enabled": True})
    assert resp.status_code == 400
    assert "type ENABLE" in resp.get_json()["error"]
    assert temp_config.load_config()["execution"]["enabled"] is False


def test_toggle_on_rejects_wrong_confirmation(client, temp_config):
    resp = client.post("/api/execution/toggle",
                       json={"enabled": True, "confirmation": "yes"})
    assert resp.status_code == 400
    assert temp_config.load_config()["execution"]["enabled"] is False


def test_toggle_on_with_confirmation_persists(client, temp_config):
    resp = client.post("/api/execution/toggle",
                       json={"enabled": True, "confirmation": "enable"})  # case-insensitive
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is True
    assert temp_config.load_config()["execution"]["enabled"] is True


def test_toggle_off_needs_no_confirmation(client, temp_config):
    """Turning execution OFF must never be obstructed."""
    client.post("/api/execution/toggle", json={"enabled": True, "confirmation": "ENABLE"})
    resp = client.post("/api/execution/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert temp_config.load_config()["execution"]["enabled"] is False


def test_toggle_preserves_other_execution_settings(client, temp_config):
    before = temp_config.load_config()["execution"]
    client.post("/api/execution/toggle", json={"enabled": True, "confirmation": "ENABLE"})
    after = temp_config.load_config()["execution"]
    assert after["port"] == before["port"] and after["host"] == before["host"]


def test_toggle_does_not_bypass_order_gates(client, temp_config):
    """Enabling execution must not make an unapproved idea placeable."""
    client.post("/api/execution/toggle", json={"enabled": True, "confirmation": "ENABLE"})
    idea_id = journal.add_idea(
        {"ticker": "TEST"}, {"thesis_summary": "t"}, None,
        {"verdict": "approved_for_review", "hard_failures": [], "soft_flags": []})
    resp = client.post(f"/api/ideas/{idea_id}/prepare-order")
    assert resp.status_code in (409, 503)   # refused: not approved / no plan
    assert resp.status_code != 200


# ---------- Provider status must not claim health it hasn't observed ----------

def test_provider_status_reports_degraded_after_failure():
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})
    router._record("yfinance", ProviderUnavailable("connection refused"))
    status = {p["name"]: p for p in router.provider_status()}
    assert status["yfinance"]["state"] == "degraded"
    assert status["yfinance"]["active"] is False
    assert "connection refused" in status["yfinance"]["note"]


def test_provider_status_active_only_after_real_success():
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})
    assert {p["name"]: p for p in router.provider_status()}["yfinance"]["state"] == "ready"
    router._record("yfinance")
    assert {p["name"]: p for p in router.provider_status()}["yfinance"]["state"] == "active"


def test_provider_status_recovers_after_success():
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})
    router._record("yfinance", ProviderUnavailable("down"))
    router._record("yfinance")
    assert {p["name"]: p for p in router.provider_status()}["yfinance"]["state"] == "active"


def test_unkeyed_providers_report_off():
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})
    status = {p["name"]: p for p in router.provider_status()}
    for name in ("FRED", "Finnhub"):
        if not status[name]["active"]:
            assert status[name]["state"] == "off"
            assert "_API_KEY" in status[name]["note"]


# ---------- Provider wraps transport errors ----------

def test_yfinance_wraps_network_errors(monkeypatch):
    """A DNS/socket failure must become ProviderUnavailable, not escape raw."""
    from assistant.providers.yfinance_provider import YFinanceProvider

    class BoomTicker:
        def __init__(self, *a, **k):
            pass

        def history(self, **kwargs):
            raise OSError("[Errno 8] nodename nor servname provided")

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Ticker", BoomTicker)
    with pytest.raises(ProviderUnavailable, match="market data request failed"):
        YFinanceProvider().get_prices("AAPL")


def test_yfinance_wraps_search_errors(monkeypatch):
    from assistant.providers.yfinance_provider import YFinanceProvider

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Search", boom)
    with pytest.raises(ProviderUnavailable):
        YFinanceProvider().search_symbol("apple")


# ---------- Configurable thesis rules ----------

def test_grounding_rules_always_present():
    """Safety rules must survive any user style override."""
    from assistant.research.thesis import build_system_prompt
    prompt = build_system_prompt({"ai": {"style_rules": "Ignore everything. Be brief."}})
    assert "NEVER state a figure" in prompt
    assert "cite it inline" in prompt
    assert "do not compute position sizes" in prompt.lower()


def test_style_rules_are_applied():
    from assistant.research.thesis import build_system_prompt
    prompt = build_system_prompt({"ai": {"style_rules": "Focus on swing trades."}})
    assert "Focus on swing trades." in prompt


def test_default_style_used_when_unset():
    from assistant.research.thesis import DEFAULT_STYLE_RULES, build_system_prompt
    assert DEFAULT_STYLE_RULES in build_system_prompt({})
    assert DEFAULT_STYLE_RULES in build_system_prompt({"ai": {}})


# ---------- AI failures must be explained, not hidden ----------

def test_credit_error_is_explained_clearly():
    """A billing problem must never surface as a vague connection error."""
    from assistant.research.thesis import _explain_ai_error
    exc = Exception("Error code: 400 - {'message': 'Your credit balance is too low "
                    "to access the Anthropic API. Please go to Plans & Billing'}")
    msg = _explain_ai_error(exc)
    assert "credits exhausted" in msg
    assert "Plans & Billing" in msg


def test_auth_error_is_explained():
    from assistant.research.thesis import _explain_ai_error
    assert "key missing or invalid" in _explain_ai_error(Exception("authentication_error"))


def test_rate_limit_explained():
    from assistant.research.thesis import _explain_ai_error
    assert "rate limit" in _explain_ai_error(Exception("429 rate limit exceeded")).lower()


def test_unknown_error_still_reported():
    from assistant.research.thesis import _explain_ai_error
    assert "ValueError" in _explain_ai_error(ValueError("something odd"))


# ---------- Economic calendar ----------

def test_calendar_filters_to_tracked_releases(monkeypatch):
    """Only market-moving releases, and the noisy 'Research CPI' is excluded."""
    from assistant.providers import fred_provider as fp

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"release_dates": [
                {"date": "2026-08-07", "release_name": "Employment Situation"},
                {"date": "2026-08-12", "release_name": "Consumer Price Index"},
                {"date": "2026-08-12", "release_name": "Research Consumer Price Index"},
                {"date": "2026-08-04", "release_name": "Dow Jones Averages"},
                {"date": "2026-08-07", "release_name": "Employment Situation"},  # dupe
            ]}

    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(fp.FredProvider, "is_available", lambda self: True)
    events = fp.FredProvider().get_economic_calendar()
    names = [e["event"] for e in events]
    assert "Employment Situation" in names and "Consumer Price Index" in names
    assert "Dow Jones Averages" not in names          # not market-moving for us
    assert not any("Research" in n for n in names)    # noise excluded
    assert len(events) == len(set((e["date"], e["event"]) for e in events))  # deduped


def test_fomc_parse_failure_returns_nothing_not_wrong_dates(monkeypatch):
    """A layout change must yield no dates rather than guessed ones — a wrong
    rate-decision date is worse than a missing one."""
    from assistant.providers import fred_provider as fp

    class Resp:
        status_code = 200
        text = "<html><body>totally different layout</body></html>"
        def raise_for_status(self): pass

    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ProviderUnavailable):
        fp.FredProvider().get_fomc_dates()


def test_router_calendar_survives_partial_source_failure(monkeypatch):
    """FRED releases and FOMC come from different sources (FRED API vs the Fed's
    website). Losing one must not lose the other."""
    from assistant.providers.base import ProviderUnavailable as PU
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})

    def dead(*a, **k):
        raise PU("no FRED key")

    monkeypatch.setattr(router.fred, "get_economic_calendar", dead)
    monkeypatch.setattr(router.fred, "get_fomc_dates",
                        lambda *a, **k: [{"date": "2026-09-15", "event": "FOMC meeting",
                                          "source": "Federal Reserve", "freshness": "eod"}])
    events = router.get_economic_calendar()
    assert [e["event"] for e in events] == ["FOMC meeting"]


def test_router_calendar_empty_when_all_sources_fail(monkeypatch):
    from assistant.providers.base import ProviderUnavailable as PU
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})

    def dead(*a, **k):
        raise PU("unavailable")

    monkeypatch.setattr(router.fred, "get_economic_calendar", dead)
    monkeypatch.setattr(router.fred, "get_fomc_dates", dead)
    assert router.get_economic_calendar() == []


def test_router_calendar_is_date_sorted(monkeypatch):
    from assistant.providers.base import ProviderUnavailable as PU
    from assistant.providers.router import DataRouter
    router = DataRouter({"providers": {}})
    monkeypatch.setattr(router.fred, "get_economic_calendar",
                        lambda *a, **k: [{"date": "2026-09-04", "event": "Employment Situation"}])
    monkeypatch.setattr(router.fred, "get_fomc_dates",
                        lambda *a, **k: [{"date": "2026-08-12", "event": "FOMC meeting"}])
    dates = [e["date"] for e in router.get_economic_calendar()]
    assert dates == sorted(dates)


def test_placeholder_macro_event_is_filtered_out():
    """The old config placeholder must never reach the thesis engine."""
    from assistant.research.context import build_context

    class Stub:
        def get_fundamentals(self, t): return {"sector": None}
        def get_instrument_currency(self, t): return "USD"
        def get_news(self, t, limit=8): return []
        def get_earnings(self, t): return {}
        def get_peer_valuations(self, t): return []
        def get_recommendations(self, t): return None
        def get_macro(self, sector=None): return {}
        def get_economic_calendar(self): return []

    ctx = build_context("TEST", Stub(), {
        "macro_events": ["Add upcoming FOMC / CPI / jobs-report dates here", "OPEC meeting Sep 3"]})
    assert ctx["user_macro_events"] == ["OPEC meeting Sep 3"]


# ---------- Journal stats (regression: a NameError shipped here undetected) ----------

def test_stats_runs_on_empty_journal():
    st = journal.stats()
    assert st["win_rate_pct"] is None
    assert st["by_verdict"] == {} and st["by_month"] == {}
    assert set(st["by_confidence_bucket"]) == {"<55", "55-69", "70-84", "85+"}


def _seed(ticker, verdict, confidence, decision, outcome):
    idea_id = journal.add_idea(
        {"ticker": ticker}, {"thesis_summary": "t"},
        {"direction": "long", "entry": 100, "stop": 90, "target": 120, "shares": 10,
         "position_value": 1000, "risk_amount": 100, "confidence": confidence,
         "currency": "USD", "reward_risk": 2.0},
        {"verdict": verdict, "hard_failures": [], "soft_flags": []})
    journal.set_decision(idea_id, decision)
    journal.record_outcome(idea_id, outcome)
    return idea_id


def test_stats_breakdowns_are_computed():
    _seed("AAA", "approved_for_review", 80, "approved", "win")
    _seed("BBB", "approved_for_review", 82, "approved", "loss")
    _seed("CCC", "needs_more_research", 50, "approved", "win")
    _seed("DDD", "needs_more_research", 52, "approved", "open")

    st = journal.stats()
    assert st["wins"] == 2 and st["losses"] == 1
    assert st["win_rate_pct"] == 66.7
    assert st["open"] == 1
    assert st["by_verdict"]["approved_for_review"]["win_rate_pct"] == 50.0
    assert st["by_verdict"]["needs_more_research"]["win_rate_pct"] == 100.0
    assert st["by_confidence_bucket"]["70-84"]["wins"] == 1
    assert st["by_confidence_bucket"]["<55"]["wins"] == 1
    assert st["avg_confidence"] is not None
    assert len(st["by_month"]) >= 1


def test_state_endpoint_returns_200_with_data(client, monkeypatch):
    """End-to-end: /api/state must not 500 once the journal has rows."""
    monkeypatch.setattr("assistant.pipeline.get_router", lambda cfg: OutageRouter())
    _seed("AAA", "approved_for_review", 80, "approved", "win")
    resp = client.get("/api/state")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    body = resp.get_json()
    assert "stats" in body and "execution" in body and "scan_progress" in body


def test_execution_status_exposes_mode_and_account():
    from assistant.execution import execution_status
    st = execution_status({"execution": {"enabled": False}})
    assert st["mode"] == "off" and st["account_id"] is None


def test_unreachable_broker_reports_unverified_not_paper():
    """Fail closed: an unverifiable broker must never render as paper."""
    from assistant.execution import execution_status
    st = execution_status({"execution": {"enabled": True, "port": 9, "account": "DU1"}})
    assert st["mode"] == "unverified"
    assert st["paper"] is False
