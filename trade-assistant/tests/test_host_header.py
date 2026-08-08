"""Audit finding W-1: DNS rebinding must not reach the local API.

Binding to 127.0.0.1 keeps other machines out, but a page on evil.example whose
DNS record flips to 127.0.0.1 becomes same-origin with the dashboard and can
POST JSON to it. No endpoint is authenticated, so that page could read
/api/state for a ticker and drive the order flow with it.

A rebound request still carries the attacker's hostname in the Host header, so
checking it defeats the attack outright.
"""
import pytest

# The dashboard imports the whole data stack, so the web app genuinely needs
# yfinance to start. Skip the module rather than aborting collection.
pytest.importorskip("yfinance", reason="the web server imports the data providers")

from assistant.web.server import _hostname_of, app   # noqa: E402


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("host", [
    "127.0.0.1:5002",
    "localhost:5002",
    "127.0.0.1",
    "localhost",
    "LOCALHOST:5002",       # the header is case-insensitive
    "[::1]:5002",
])
def test_local_hosts_are_allowed(client, host):
    resp = client.get("/api/state", headers={"Host": host})
    assert resp.status_code != 403


@pytest.mark.parametrize("host", [
    "evil.example",
    "evil.example:5002",
    "attacker.test:5002",
    "127.0.0.1.evil.example:5002",     # suffix trick
    "localhost.evil.example",
    "evil.example:5002.localhost",
])
def test_foreign_hosts_are_refused(client, host):
    resp = client.get("/api/state", headers={"Host": host})
    assert resp.status_code == 403
    assert "localhost" in resp.get_json()["error"]


def test_the_check_covers_state_reads_not_just_writes(client):
    """/api/state is the reconnaissance step: it carries the tickers, the
    portfolio and the execution mode."""
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403


def test_the_check_covers_the_execution_toggle(client):
    resp = client.post("/api/execution/toggle",
                       json={"enabled": True, "confirmation": "ENABLE"},
                       headers={"Host": "evil.example"})
    assert resp.status_code == 403


def test_the_check_covers_order_confirmation(client):
    resp = client.post("/api/orders/1/confirm", json={"confirmation": "AAPL"},
                       headers={"Host": "evil.example"})
    assert resp.status_code == 403


def test_the_check_covers_the_dashboard_page_itself(client):
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("header,expected", [
    ("127.0.0.1:5002", "127.0.0.1"),
    ("localhost:5002", "localhost"),
    ("localhost", "localhost"),
    ("[::1]:5002", "[::1]"),
    ("[::1]", "[::1]"),
    ("EVIL.example:80", "evil.example"),
    ("", ""),
    (None, ""),
])
def test_hostname_parsing(header, expected):
    assert _hostname_of(header) == expected
