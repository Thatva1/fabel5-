"""Global macro provider — UK, euro area and worldwide series.

Sources are hit directly rather than through an aggregator, because the
aggregators' routes for this data are not reliably reachable:

  * Bank of England IADB  — UK Bank Rate, gilt yields, SONIA. CSV, no key.
  * ECB Data Portal       — euro-area policy rates, HICP, Euribor. JSON, no key.
  * World Bank            — CPI/unemployment/GDP for any country. JSON, no key.

None of these need an API key. FRED still covers the US (see fred_provider).
OECD is deliberately not used: its SDMX endpoint returns HTTP 403 from server
environments, so it cannot be depended on.
"""
import csv
import io
from datetime import date, timedelta

import requests

from ..core.models import FRESH_EOD, fact
from .base import DataProvider, ProviderUnavailable

UA = {"User-Agent": "Mozilla/5.0 (compatible; TradeAssistant/2.0)"}
TIMEOUT = 20

BOE_URL = ("https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp?csv.x=yes"
           "&Datefrom={frm}&Dateto=now&SeriesCodes={code}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
ECB_URL = "https://data-api.ecb.europa.eu/service/data/{key}"
WB_URL = "https://api.worldbank.org/v2/country/{iso}/indicator/{ind}"

# --- UK: Bank of England series ---
BOE_SERIES = {
    "uk_bank_rate":      ("IUDBEDR", "%",  "BoE Bank Rate"),
    "uk_10y_gilt":       ("IUDMNZC", "%",  "UK 10-year gilt yield"),
    "uk_sonia":          ("IUDSOIA", "%",  "SONIA overnight rate"),
}

# --- Euro area: ECB Data Portal series keys ---
ECB_SERIES = {
    "ecb_mro_rate":      ("FM/D.U2.EUR.4F.KR.MRR_FR.LEV", "%", "ECB main refinancing rate"),
    "ecb_deposit_rate":  ("FM/D.U2.EUR.4F.KR.DFR.LEV",    "%", "ECB deposit facility rate"),
    "ecb_hicp_yoy":      ("ICP/M.U2.N.000000.4.ANR",      "%", "Euro-area HICP inflation YoY"),
}

# --- World Bank indicator codes for the global fallback ---
WB_INDICATORS = {
    "cpi_inflation_yoy": ("FP.CPI.TOTL.ZG", "%",  "CPI inflation YoY"),
    "unemployment_rate": ("SL.UEM.TOTL.ZS", "%",  "Unemployment rate"),
    "gdp_growth":        ("NY.GDP.MKTP.KD.ZG", "%", "Real GDP growth"),
}

# Country -> (ISO3 for World Bank, human label). World Bank covers ~200
# countries; this table is the set the app offers as macro regions.
COUNTRIES = {
    "GB": ("GBR", "United Kingdom"), "EU": ("EMU", "Euro area"),
    "DE": ("DEU", "Germany"), "FR": ("FRA", "France"), "IT": ("ITA", "Italy"),
    "ES": ("ESP", "Spain"), "NL": ("NLD", "Netherlands"), "IE": ("IRL", "Ireland"),
    "CH": ("CHE", "Switzerland"), "SE": ("SWE", "Sweden"), "NO": ("NOR", "Norway"),
    "DK": ("DNK", "Denmark"), "PL": ("POL", "Poland"),
    "US": ("USA", "United States"), "CA": ("CAN", "Canada"), "MX": ("MEX", "Mexico"),
    "BR": ("BRA", "Brazil"),
    "JP": ("JPN", "Japan"), "CN": ("CHN", "China"), "IN": ("IND", "India"),
    "KR": ("KOR", "South Korea"), "HK": ("HKG", "Hong Kong"), "SG": ("SGP", "Singapore"),
    "AU": ("AUS", "Australia"), "NZ": ("NZL", "New Zealand"),
    "ZA": ("ZAF", "South Africa"), "TR": ("TUR", "Turkey"),
}

# Central-bank policy rates and 10-year government yields, per country.
# FRED carries these international series — it is not a US-only source, which
# is what previously made this app look US-centric.
#   key -> (policy_rate_series, ten_year_series, central_bank_label)
FRED_COUNTRY_SERIES = {
    "US": ("FEDFUNDS",        "DGS10",           "Federal Reserve"),
    "JP": ("IRSTCI01JPM156N", "IRLTLT01JPM156N", "Bank of Japan"),
    "CH": ("IRSTCI01CHM156N", "IRLTLT01CHM156N", "Swiss National Bank"),
    "CA": ("IR3TIB01CAM156N", "IRLTLT01CAM156N", "Bank of Canada"),
    "AU": ("IR3TIB01AUM156N", "IRLTLT01AUM156N", "Reserve Bank of Australia"),
    "NZ": ("IR3TIB01NZM156N", "IRLTLT01NZM156N", "Reserve Bank of New Zealand"),
    "DE": (None,              "IRLTLT01DEM156N", "Bundesbank (ECB policy)"),
    "FR": (None,              "IRLTLT01FRM156N", "Banque de France (ECB policy)"),
    "IT": (None,              "IRLTLT01ITM156N", "Banca d'Italia (ECB policy)"),
    "ES": (None,              "IRLTLT01ESM156N", "Banco de España (ECB policy)"),
    "SE": ("IR3TIB01SEM156N", "IRLTLT01SEM156N", "Riksbank"),
    "NO": ("IR3TIB01NOM156N", "IRLTLT01NOM156N", "Norges Bank"),
    "DK": ("IR3TIB01DKM156N", "IRLTLT01DKM156N", "Danmarks Nationalbank"),
    "KR": ("IR3TIB01KRM156N", "IRLTLT01KRM156N", "Bank of Korea"),
    "IN": (None,              "INDIRLTLT01STM", "Reserve Bank of India"),
    "PL": ("IR3TIB01PLM156N", "IRLTLT01PLM156N", "Narodowy Bank Polski"),
    "MX": ("IR3TIB01MXM156N", "IRLTLT01MXM156N", "Banco de México"),
}


class GlobalMacroProvider(DataProvider):
    name = "global-macro"

    def is_available(self):
        return True          # no API key required by any of these sources

    # ---------------- Bank of England ----------------

    def _boe(self, code):
        frm = (date.today() - timedelta(days=120)).strftime("%d/%b/%Y")
        try:
            resp = requests.get(BOE_URL.format(frm=frm, code=code), timeout=TIMEOUT, headers=UA)
            resp.raise_for_status()
            rows = list(csv.reader(io.StringIO(resp.text)))
        except Exception as exc:
            raise ProviderUnavailable(f"boe: {code} failed ({type(exc).__name__}: {exc})")
        data = [r for r in rows[1:] if len(r) > 1 and r[1].strip()]
        if not data:
            raise ProviderUnavailable(f"boe: no observations for {code}")
        when, value = data[-1][0], data[-1][1]
        try:
            return float(value), when
        except ValueError:
            raise ProviderUnavailable(f"boe: unparseable value for {code}")

    # ---------------- ECB ----------------

    def _ecb(self, series_key):
        """Parse SDMX-JSON without assuming the dimension count — the observation
        key differs per dataflow, which is why hardcoding '0:0:...' breaks."""
        try:
            resp = requests.get(ECB_URL.format(key=series_key),
                                params={"lastNObservations": 1, "format": "jsondata"},
                                timeout=TIMEOUT, headers=UA)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise ProviderUnavailable(f"ecb: {series_key} failed ({type(exc).__name__}: {exc})")

        try:
            series = payload["dataSets"][0]["series"]
            first = next(iter(series.values()))            # whatever the key shape is
            obs = first["observations"]
            idx = sorted(obs.keys(), key=lambda k: int(k))[-1]
            value = obs[idx][0]
            periods = payload["structure"]["dimensions"]["observation"][0]["values"]
            when = periods[int(idx)]["id"] if int(idx) < len(periods) else periods[-1]["id"]
            return float(value), when
        except Exception as exc:
            raise ProviderUnavailable(f"ecb: unexpected payload for {series_key} ({exc})")

    # ---------------- World Bank ----------------

    def _world_bank(self, iso3, indicator):
        try:
            resp = requests.get(WB_URL.format(iso=iso3, ind=indicator),
                                params={"format": "json", "date": f"{date.today().year - 3}:{date.today().year}"},
                                timeout=TIMEOUT, headers=UA)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            raise ProviderUnavailable(f"worldbank: {iso3}/{indicator} failed ({exc})")
        rows = [p for p in (body[1] if len(body) > 1 and body[1] else []) if p.get("value") is not None]
        if not rows:
            raise ProviderUnavailable(f"worldbank: no data for {iso3}/{indicator}")
        return float(rows[0]["value"]), str(rows[0]["date"])

    # ---------------- public surface ----------------

    def get_uk_macro(self):
        out = {}
        for key, (code, unit, label) in BOE_SERIES.items():
            try:
                value, when = self._boe(code)
                out[key] = fact(round(value, 3), "Bank of England", FRESH_EOD,
                                timestamp=when, unit=unit)
                out[key]["label"] = label
            except ProviderUnavailable:
                out[key] = None
        return out

    def get_euro_macro(self):
        out = {}
        for key, (series_key, unit, label) in ECB_SERIES.items():
            try:
                value, when = self._ecb(series_key)
                out[key] = fact(round(value, 3), "ECB Data Portal", FRESH_EOD,
                                timestamp=when, unit=unit)
                out[key]["label"] = label
            except ProviderUnavailable:
                out[key] = None
        return out

    def get_country_macro(self, country_code):
        """Broad indicators for any country, via the World Bank."""
        entry = COUNTRIES.get((country_code or "").upper())
        if not entry:
            raise ProviderUnavailable(f"worldbank: unknown country '{country_code}'")
        iso3, label = entry
        out = {"country": label}
        for key, (indicator, unit, desc) in WB_INDICATORS.items():
            try:
                value, when = self._world_bank(iso3, indicator)
                out[key] = fact(round(value, 2), "World Bank", FRESH_EOD,
                                timestamp=when, unit=unit)
                out[key]["label"] = f"{label} {desc}"
            except ProviderUnavailable:
                out[key] = None
        return out

    def get_fred_country(self, country_code):
        """Policy rate and 10-year yield for a country, from FRED's
        international series. Requires FRED_API_KEY."""
        from ..core.config import api_key
        key = api_key("FRED_API_KEY")
        if not key:
            raise ProviderUnavailable("fred: FRED_API_KEY not set")
        entry = FRED_COUNTRY_SERIES.get((country_code or "").upper())
        if not entry:
            raise ProviderUnavailable(f"fred: no series mapped for {country_code}")
        policy_series, ten_year_series, bank = entry
        code = country_code.lower()
        out = {}
        for label, series_id in (("policy_rate", policy_series), ("10y_yield", ten_year_series)):
            if not series_id:
                continue
            try:
                resp = requests.get("https://api.stlouisfed.org/fred/series/observations",
                                    params={"series_id": series_id, "api_key": key,
                                            "file_type": "json", "sort_order": "desc", "limit": 1},
                                    timeout=TIMEOUT)
                resp.raise_for_status()
                rows = [o for o in resp.json().get("observations", [])
                        if o.get("value") not in (None, ".")]
                if not rows:
                    continue
                out[f"{code}_{label}"] = fact(round(float(rows[0]["value"]), 3), "FRED",
                                              FRESH_EOD, timestamp=rows[0]["date"], unit="%")
                out[f"{code}_{label}"]["label"] = (
                    f"{bank} policy rate" if label == "policy_rate"
                    else f"{COUNTRIES.get(country_code.upper(), ('', country_code))[1]} 10-year yield")
            except Exception:
                continue
        if not out:
            raise ProviderUnavailable(f"fred: nothing returned for {country_code}")
        return out

    def get_macro(self, regions=("GB", "EU")):
        """Combined regional macro. Regions are ISO-2 codes; UK and euro area
        use their central banks directly, everything else the World Bank."""
        out = {}
        regions = [r.upper() for r in (regions or [])]
        if "GB" in regions:
            out.update({k: v for k, v in self.get_uk_macro().items() if v})
        if "EU" in regions:
            out.update({k: v for k, v in self.get_euro_macro().items() if v})
        for code in regions:
            if code in ("GB", "EU"):
                continue
            # Rates first (FRED international series — current, monthly), then
            # broad indicators from the World Bank as a backstop.
            try:
                out.update(self.get_fred_country(code))
            except ProviderUnavailable:
                pass
            try:
                block = self.get_country_macro(code)
            except ProviderUnavailable:
                continue
            for key, value in block.items():
                if key != "country" and value:
                    out[f"{code.lower()}_{key}"] = value
        if not out:
            raise ProviderUnavailable("global-macro: no series available")
        return out
