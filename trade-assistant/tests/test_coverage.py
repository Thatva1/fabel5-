"""Coverage: what the LICENSED feed can price, and what it silently cannot."""
import json

from assistant.providers import coverage


def write_report(tmp_path, unavailable=None, tradable=None, checked_at=None):
    from datetime import datetime, timezone
    report = {
        "checked_at": checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "ibkr",
        "accounts": ["DUR673876"],
        "tradable": tradable or {"AAPL": {"last_bar": "2026-08-14", "last_close": 305.93, "bars": 251}},
        "unavailable": unavailable or {},
        "counts": {"tradable": 1, "unavailable": len(unavailable or {}), "total": 1},
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report))
    return str(path), report


# -- classification --------------------------------------------------------

def test_idealpro_error_maps_to_the_fx_subscription():
    out = coverage._classify(
        "GBPUSD=X",
        "No market data permissions for IDEALPRO CASH, contract: Forex('GBPUSD')")
    assert out["bundle"] == "IDEALPRO spot FX"
    assert "IDEALPRO" in out["action"]


def test_spot_fx_maps_to_fx_even_when_the_error_is_vague():
    """IBKR reports a missing FX entitlement as 'no bars', naming nothing."""
    out = coverage._classify("EURUSD=X", "ibkr: no bars for EURUSD=X")
    assert out["bundle"] == "IDEALPRO spot FX"


def test_missing_contract_is_reported_as_mapping_not_subscription():
    """Telling these apart is what turned four 'unavailable' FX futures into a
    one-line symbol fix rather than a subscription the user did not need."""
    out = coverage._classify("6E=F", "ibkr: no contract for 6E=F")
    assert out["bundle"] == "contract not found"
    assert "mapping" in out["action"].lower()


# -- filtering -------------------------------------------------------------

def test_unavailable_symbols_are_dropped_from_the_tradable_list(tmp_path):
    path, _ = write_report(tmp_path, unavailable={
        "GBPUSD=X": {"bundle": "IDEALPRO spot FX", "action": "x", "error": "no bars"}})
    kept, info = coverage.tradable_symbols(["AAPL", "GBPUSD=X"], {}, path=path)
    assert kept == ["AAPL"]
    assert info["filtered"] is True
    assert info["dropped"] == ["GBPUSD=X"]


def test_no_report_leaves_the_universe_untouched(tmp_path):
    """Failing open, not closed. An empty universe stops the book dead; an
    unfiltered one merely restores the previous behaviour."""
    kept, info = coverage.tradable_symbols(
        ["AAPL", "GBPUSD=X"], {}, path=str(tmp_path / "missing.json"))
    assert kept == ["AAPL", "GBPUSD=X"]
    assert info["filtered"] is False


def test_a_stale_report_is_not_trusted_to_filter(tmp_path):
    path, _ = write_report(
        tmp_path,
        unavailable={"GBPUSD=X": {"bundle": "FX", "action": "x", "error": "e"}},
        checked_at="2020-01-01T00:00:00+00:00")
    kept, info = coverage.tradable_symbols(["AAPL", "GBPUSD=X"], {},
                                           max_age_hours=24, path=path)
    assert kept == ["AAPL", "GBPUSD=X"]
    assert info["filtered"] is False
    assert "old" in info["reason"]


# -- subscription summary --------------------------------------------------

def test_missing_instruments_group_by_the_subscription_that_unlocks_them(tmp_path):
    """Thirteen 'no bars' errors read as a broken system; one group of thirteen
    reads as a decision with a price on it."""
    path, _ = write_report(tmp_path, unavailable={
        "GBPUSD=X": {"bundle": "IDEALPRO spot FX", "action": "buy FX", "error": "e"},
        "EURUSD=X": {"bundle": "IDEALPRO spot FX", "action": "buy FX", "error": "e"},
        "XYZ": {"bundle": "contract not found", "action": "fix mapping", "error": "e"},
    })
    out = coverage.subscription_summary(path=path)
    assert out["available"] is True
    groups = {g["bundle"]: g for g in out["groups"]}
    assert groups["IDEALPRO spot FX"]["count"] == 2
    assert groups["IDEALPRO spot FX"]["symbols"] == ["EURUSD=X", "GBPUSD=X"]
    # Largest group first, so the biggest single unlock is what the user reads.
    assert out["groups"][0]["bundle"] == "IDEALPRO spot FX"


def test_summary_without_a_report_says_so(tmp_path):
    out = coverage.subscription_summary(path=str(tmp_path / "nope.json"))
    assert out["available"] is False


def test_save_and_load_round_trip(tmp_path):
    path = str(tmp_path / "cov.json")
    report = {"checked_at": "2026-08-17T04:00:00+00:00", "tradable": {},
              "unavailable": {}, "counts": {}}
    coverage.save(report, path=path)
    assert coverage.load(path=path) == report


def test_load_returns_none_on_a_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert coverage.load(path=str(path)) is None
