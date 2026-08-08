"""Audit finding S-1: API keys must never reach a user-facing string.

FRED and Finnhub both require the key as a URL query parameter, so `requests`
puts the whole URL — key included — into its transport exceptions. That text
used to flow: provider exception -> router._record() -> provider_status() ->
GET /api/state -> the browser DOM. These tests pin every link in that chain.
"""
import pytest

from assistant.providers.base import ProviderUnavailable, redact
from assistant.providers.router import DataRouter

SECRET = "SECRETKEY123"

# The exact shape of the leak demonstrated in AUDIT-2026-08-08.md.
FRED_LEAK = (
    "HTTPSConnectionPool(host='api.stlouisfed.org', port=443): Max retries exceeded "
    "with url: /fred/series/observations?series_id=DGS10"
    f"&api_key={SECRET}&file_type=json")


@pytest.mark.parametrize("text", [
    FRED_LEAK,
    f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={SECRET}",
    f"api_key={SECRET}",
    f"apikey={SECRET}",
    f"API_KEY={SECRET}&other=1",
    f'{{"token": "{SECRET}"}}',
    f"access_token={SECRET}",
    f"secret={SECRET}",
    f"password={SECRET}",
])
def test_named_credential_parameters_are_redacted(text):
    assert SECRET not in redact(text)
    assert "***" in redact(text)


@pytest.mark.parametrize("key", [
    "sk-ant-api03-AbCdEf1234567890",
    "sk-proj-AbCdEf1234567890",
    "ghp_AbCdEf1234567890",
])
def test_bare_key_material_is_redacted(key):
    """Some SDKs echo the key back without a name in front of it."""
    out = redact(f"Error: invalid credential {key} was rejected")
    assert key not in out
    assert "***" in out


def test_redact_preserves_the_useful_part_of_the_message():
    """Scrubbing must not make the error useless — the host and the failure
    mode are what the user needs to diagnose an outage."""
    out = redact(FRED_LEAK)
    assert "api.stlouisfed.org" in out
    assert "Max retries exceeded" in out
    assert "series_id=DGS10" in out


def test_redact_handles_none_and_non_strings():
    assert redact(None) is None
    assert redact(ValueError("api_key=abc123")) == "api_key=***"


def test_router_health_record_scrubs_provider_errors():
    """Belt and braces: even a provider that forgets to scrub its own message
    cannot get a key into the health record."""
    router = DataRouter({})
    router._record("fred", ProviderUnavailable(FRED_LEAK))
    assert SECRET not in router.health["fred"]["last_error"]


def test_provider_status_note_is_safe_to_serve_over_http():
    """provider_status() is the payload of GET /api/state."""
    router = DataRouter({})
    router._record("fred", ProviderUnavailable(FRED_LEAK))
    router._record("finnhub", ProviderUnavailable(
        f"finnhub: request failed: ConnectionError: ...&token={SECRET}"))
    blob = str(router.provider_status())
    assert SECRET not in blob
    assert "degraded" in blob      # the outage is still reported honestly


def test_fred_provider_scrubs_its_own_exception(monkeypatch):
    """The provider must not rely on the router's second pass."""
    from assistant.providers import fred_provider

    monkeypatch.setenv("FRED_API_KEY", SECRET)
    monkeypatch.setattr(fred_provider, "BASE_URL",
                        "https://this-host-does-not-exist.invalid/fred")

    with pytest.raises(ProviderUnavailable) as excinfo:
        fred_provider.FredProvider()._latest("DGS10")
    assert SECRET not in str(excinfo.value)
    # Still names the failure type, so the message stays actionable.
    assert "DGS10" in str(excinfo.value)


def test_finnhub_provider_scrubs_its_own_exception(monkeypatch):
    from assistant.providers import finnhub_provider

    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    monkeypatch.setattr(finnhub_provider, "BASE_URL",
                        "https://this-host-does-not-exist.invalid/api")

    with pytest.raises(ProviderUnavailable) as excinfo:
        finnhub_provider.FinnhubProvider()._get("quote", {"symbol": "AAPL"})
    assert SECRET not in str(excinfo.value)


def test_ai_error_explanation_is_scrubbed():
    """thesis ai_error lands in gate soft_flags, which /api/state serves."""
    from assistant.research.thesis import _explain_ai_error

    out = _explain_ai_error(
        RuntimeError("connection failed for x-api-key sk-ant-api03-AbCdEf1234567890"))
    assert "AbCdEf1234567890" not in out
