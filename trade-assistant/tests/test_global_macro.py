"""Global macro provider — UK (BoE), euro area (ECB), worldwide (World Bank).

Context: the app was US-only via FRED. The owner trades UK/European markets,
so macro must cover their home region, and search must prefer their exchanges.
"""
import pytest

from assistant.providers.base import ProviderUnavailable
from assistant.providers.global_macro import COUNTRIES, GlobalMacroProvider


@pytest.fixture
def p():
    return GlobalMacroProvider()


def test_no_api_key_required(p):
    assert p.is_available() is True


def test_ecb_parser_handles_any_dimension_shape(p, monkeypatch):
    """Regression: hardcoding the observation key '0:0:0:0:0:0:0' broke HICP,
    whose dataflow has a different dimension count."""
    payload = {
        "dataSets": [{"series": {"0:0:0": {"observations": {"0": [1.9]}}}}],
        "structure": {"dimensions": {"observation": [{"values": [{"id": "2025-12"}]}]}},
    }

    class R:
        def raise_for_status(self): pass
        def json(self): return payload

    monkeypatch.setattr("assistant.providers.global_macro.requests.get", lambda *a, **k: R())
    value, when = p._ecb("ICP/M.U2.N.000000.4.ANR")
    assert value == 1.9 and when == "2025-12"


def test_ecb_bad_payload_degrades(p, monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"unexpected": True}

    monkeypatch.setattr("assistant.providers.global_macro.requests.get", lambda *a, **k: R())
    with pytest.raises(ProviderUnavailable):
        p._ecb("FM/D.U2.EUR.4F.KR.MRR_FR.LEV")


def test_boe_csv_parsing(p, monkeypatch):
    class R:
        text = "DATE,IUDBEDR\n02 Jan 2026,4.00\n03 Aug 2026,3.75\n"
        def raise_for_status(self): pass

    monkeypatch.setattr("assistant.providers.global_macro.requests.get", lambda *a, **k: R())
    value, when = p._boe("IUDBEDR")
    assert value == 3.75 and when == "03 Aug 2026"      # latest row wins


def test_boe_empty_series_raises(p, monkeypatch):
    class R:
        text = "DATE,IUDBEDR\n"
        def raise_for_status(self): pass

    monkeypatch.setattr("assistant.providers.global_macro.requests.get", lambda *a, **k: R())
    with pytest.raises(ProviderUnavailable):
        p._boe("IUDBEDR")


def test_unknown_country_rejected(p):
    with pytest.raises(ProviderUnavailable):
        p.get_country_macro("ZZ")


def test_uk_and_euro_are_first_class_countries():
    assert "GB" in COUNTRIES and "EU" in COUNTRIES


def test_get_macro_survives_one_dead_source(p, monkeypatch):
    """UK failing must not blank the euro-area block."""
    monkeypatch.setattr(p, "get_uk_macro", lambda: {"uk_bank_rate": None})
    monkeypatch.setattr(p, "get_euro_macro", lambda: {
        "ecb_mro_rate": {"value": 2.4, "source": "ECB", "timestamp": "2026-08-05"}})
    out = p.get_macro(regions=("GB", "EU"))
    assert "ecb_mro_rate" in out and "uk_bank_rate" not in out


def test_get_macro_all_dead_raises(p, monkeypatch):
    monkeypatch.setattr(p, "get_uk_macro", lambda: {})
    monkeypatch.setattr(p, "get_euro_macro", lambda: {})
    with pytest.raises(ProviderUnavailable):
        p.get_macro(regions=("GB", "EU"))


# ---------- exchange-preference ranking ----------

def test_search_prefers_home_exchange(monkeypatch):
    """A UK trader searching 'tesco' must get the London line, not Frankfurt."""
    from assistant.providers.yfinance_provider import YFinanceProvider

    class FakeSearch:
        def __init__(self, *a, **k): pass
        quotes = [
            {"symbol": "TCO2.F", "shortname": "Tesco PLC", "exchange": "FRA", "quoteType": "EQUITY"},
            {"symbol": "TSCO.L", "shortname": "TESCO PLC", "exchange": "LSE", "quoteType": "EQUITY"},
            {"symbol": "TCO0.HA", "shortname": "Tesco PLC", "exchange": "HAN", "quoteType": "EQUITY"},
        ]

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Search", FakeSearch)
    res = YFinanceProvider().search_symbol("tesco", 3, preferred_exchanges=["LSE", "AMS", "PAR"])
    assert res[0]["symbol"] == "TSCO.L"


def test_search_without_preference_keeps_source_order(monkeypatch):
    from assistant.providers.yfinance_provider import YFinanceProvider

    class FakeSearch:
        def __init__(self, *a, **k): pass
        quotes = [
            {"symbol": "TCO2.F", "shortname": "Tesco", "exchange": "FRA", "quoteType": "EQUITY"},
            {"symbol": "TSCO.L", "shortname": "Tesco", "exchange": "LSE", "quoteType": "EQUITY"},
        ]

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Search", FakeSearch)
    res = YFinanceProvider().search_symbol("tesco", 2)
    assert res[0]["symbol"] == "TCO2.F"


def test_equities_rank_above_funds(monkeypatch):
    from assistant.providers.yfinance_provider import YFinanceProvider

    class FakeSearch:
        def __init__(self, *a, **k): pass
        quotes = [
            {"symbol": "XSHL.L", "shortname": "Shell ETF", "exchange": "LSE", "quoteType": "ETF"},
            {"symbol": "SHEL.L", "shortname": "Shell PLC", "exchange": "LSE", "quoteType": "EQUITY"},
        ]

    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Search", FakeSearch)
    res = YFinanceProvider().search_symbol("shell", 2, preferred_exchanges=["LSE"])
    assert res[0]["symbol"] == "SHEL.L"


# ---------- cross-listing noise vs primary listing ----------

def _search(monkeypatch, quotes, prefs):
    from assistant.providers.yfinance_provider import YFinanceProvider

    class FakeSearch:
        def __init__(self, *a, **k): pass
    FakeSearch.quotes = quotes
    monkeypatch.setattr("assistant.providers.yfinance_provider.yf.Search", FakeSearch)
    return YFinanceProvider().search_symbol("q", 5, preferred_exchanges=prefs)


UK_PREFS = ["LSE", "IOB", "AMS", "PAR", "GER", "EBS", "MIL", "MCE",
            "ISE", "STO", "CPH", "OSL", "NMS", "NYQ"]


def test_german_cross_listing_never_beats_home_listing(monkeypatch):
    """Yahoo returns the Frankfurt line first for 'tesco'; it must not win."""
    res = _search(monkeypatch, [
        {"symbol": "TCO2.F", "shortname": "Tesco", "exchange": "FRA", "quoteType": "EQUITY"},
        {"symbol": "TSCO.L", "shortname": "Tesco", "exchange": "LSE", "quoteType": "EQUITY"},
    ], UK_PREFS)
    assert res[0]["symbol"] == "TSCO.L"


def test_us_company_keeps_its_us_listing(monkeypatch):
    """Regression: a hard preference sort resolved 'apple' to APC.DE. The user
    is UK-based, but Apple's primary listing is still Nasdaq."""
    res = _search(monkeypatch, [
        {"symbol": "AAPL", "shortname": "Apple Inc.", "exchange": "NMS", "quoteType": "EQUITY"},
        {"symbol": "APC.DE", "shortname": "Apple Inc.", "exchange": "GER", "quoteType": "EQUITY"},
    ], UK_PREFS)
    assert res[0]["symbol"] == "AAPL"


def test_uk_company_prefers_london_over_us_adr(monkeypatch):
    """Yahoo puts the NYSE ADR first for HSBC; a UK trader wants the LSE line."""
    res = _search(monkeypatch, [
        {"symbol": "HSBC", "shortname": "HSBC Holdings", "exchange": "NYQ", "quoteType": "EQUITY"},
        {"symbol": "0005.HK", "shortname": "HSBC HOLDINGS", "exchange": "HKG", "quoteType": "EQUITY"},
        {"symbol": "HSBA.L", "shortname": "HSBC HOLDINGS PLC", "exchange": "LSE", "quoteType": "EQUITY"},
    ], UK_PREFS)
    assert res[0]["symbol"] == "HSBA.L"


def test_etfs_rank_below_equities(monkeypatch):
    res = _search(monkeypatch, [
        {"symbol": "XSHL.L", "shortname": "Shell ETF", "exchange": "LSE", "quoteType": "ETF"},
        {"symbol": "SHEL.L", "shortname": "Shell PLC", "exchange": "LSE", "quoteType": "EQUITY"},
    ], UK_PREFS)
    assert res[0]["symbol"] == "SHEL.L"


@pytest.mark.parametrize("noisy", ["FRA", "STU", "DUS", "MEX", "PNK", "OID"])
def test_all_noise_venues_are_demoted(monkeypatch, noisy):
    res = _search(monkeypatch, [
        {"symbol": "NOISE", "shortname": "X", "exchange": noisy, "quoteType": "EQUITY"},
        {"symbol": "REAL.L", "shortname": "X", "exchange": "LSE", "quoteType": "EQUITY"},
    ], UK_PREFS)
    assert res[0]["symbol"] == "REAL.L"


# ---------- multi-country coverage ----------

def test_all_named_central_banks_are_mapped():
    """Every central bank in the owner's macro spec must resolve to a series."""
    from assistant.providers.global_macro import FRED_COUNTRY_SERIES
    for code in ("US", "JP", "CH", "CA", "AU", "NZ"):
        assert code in FRED_COUNTRY_SERIES, f"{code} central bank not mapped"
        assert FRED_COUNTRY_SERIES[code][0], f"{code} has no policy-rate series"
    # UK and euro area come from BoE/ECB directly, not FRED
    assert "GB" in COUNTRIES and "EU" in COUNTRIES


def test_country_coverage_spans_continents():
    regions = set(COUNTRIES)
    assert {"GB", "DE", "FR", "CH", "SE"} <= regions          # Europe
    assert {"US", "CA", "BR", "MX"} <= regions                # Americas
    assert {"JP", "CN", "IN", "KR", "AU", "SG", "HK"} <= regions  # Asia-Pacific
    assert {"ZA", "TR"} <= regions                            # EMEA
    assert len(regions) >= 25
