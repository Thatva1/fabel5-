"""FRED (Federal Reserve Economic Data) provider — authoritative US macro series.

Free API key: https://fred.stlouisfed.org (My Account -> API Keys), goes in
.env as FRED_API_KEY. Observations are end-of-day / monthly -> freshness 'eod',
timestamped with the observation date so the thesis engine cites real as-of dates.
"""
import re
from datetime import date, datetime, timedelta

import requests

from ..core.config import api_key
from ..core.models import FRESH_EOD, fact
from .base import DataProvider, ProviderUnavailable

# Scheduled releases worth knowing about before taking a position. FRED's
# releases/dates endpoint publishes these ahead of time, free.
TRACKED_RELEASES = (
    "Consumer Price Index",
    "Employment Situation",
    "Producer Price Index",
    "Personal Income and Outlays",
    "Gross Domestic Product",
)
_TRACKED_RE = re.compile("|".join(re.escape(n) for n in TRACKED_RELEASES), re.I)

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
SOURCE = "FRED"

SERIES = {
    "ten_year_yield": ("DGS10", "%"),
    "three_month_yield": ("DTB3", "%"),
    "fed_funds_rate": ("FEDFUNDS", "%"),
    "unemployment_rate": ("UNRATE", "%"),
    "vix": ("VIXCLS", None),
}
CPI_SERIES = "CPIAUCSL"  # index level; YoY computed deterministically below


class FredProvider(DataProvider):
    name = "fred"

    def is_available(self):
        return bool(api_key("FRED_API_KEY"))

    def _latest(self, series_id, limit=1):
        if not self.is_available():
            raise ProviderUnavailable("fred: FRED_API_KEY not set")
        try:
            resp = requests.get(BASE_URL, params={
                "series_id": series_id,
                "api_key": api_key("FRED_API_KEY"),
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            }, timeout=15)
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
        except Exception as exc:
            raise ProviderUnavailable(f"fred: request failed for {series_id}: {exc}")
        rows = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (None, ".")]
        if not rows:
            raise ProviderUnavailable(f"fred: no observations for {series_id}")
        return rows

    def get_economic_calendar(self, days_ahead=45):
        """Upcoming scheduled data releases (CPI, jobs report, PPI, GDP, PCE).

        FRED publishes future release dates for free — no paid plan involved.
        FOMC meeting dates are NOT in this feed (FRED's 'FOMC Press Release'
        entry fires daily and is meaningless as a meeting calendar), so those
        come from get_fomc_dates() instead.
        """
        if not self.is_available():
            raise ProviderUnavailable("fred: FRED_API_KEY not set")
        today = date.today()
        try:
            resp = requests.get(
                "https://api.stlouisfed.org/fred/releases/dates",
                params={"api_key": api_key("FRED_API_KEY"), "file_type": "json",
                        "realtime_start": str(today),
                        "realtime_end": str(today + timedelta(days=days_ahead)),
                        "include_release_dates_with_no_data": "true",
                        "limit": 1000, "sort_order": "asc"},
                timeout=20)
            resp.raise_for_status()
            rows = resp.json().get("release_dates", [])
        except Exception as exc:
            raise ProviderUnavailable(f"fred: release calendar failed: {exc}")

        events, seen = [], set()
        for row in rows:
            name = row.get("release_name", "")
            if not _TRACKED_RE.search(name) or name.lower().startswith("research"):
                continue
            key = (row["date"], name)
            if key in seen:
                continue
            seen.add(key)
            events.append({"date": row["date"], "event": name,
                           "source": SOURCE, "freshness": FRESH_EOD})
        return events

    def get_fomc_dates(self, days_ahead=120):
        """Upcoming FOMC meeting dates, parsed from the Fed's public calendar.

        Best-effort: this reads a public HTML page rather than an API, so it is
        wrapped defensively — if the page layout changes we return nothing
        rather than guessing wrong dates for a rate decision.
        """
        try:
            resp = requests.get(FOMC_CALENDAR_URL, timeout=20,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            raise ProviderUnavailable(f"fed: FOMC calendar fetch failed: {exc}")

        today = date.today()
        horizon = today + timedelta(days=days_ahead)
        out = []
        # The page groups meetings under "<year> FOMC Meetings" headings.
        blocks = re.split(r"(\d{4})\s+FOMC Meetings", html)
        for i in range(1, len(blocks) - 1, 2):
            try:
                year = int(blocks[i])
            except ValueError:
                continue
            body = blocks[i + 1]
            months = re.findall(r"fomc-meeting__month[^>]*>\s*(?:<strong>)?([A-Za-z]+)", body)
            days = re.findall(r"fomc-meeting__date[^>]*>\s*(\d{1,2})(?:-\d{1,2})?", body)
            for month_name, day in zip(months, days):
                month = _MONTHS.get(month_name.strip().lower()[:3] and
                                    next((m for m in _MONTHS if m.startswith(
                                        month_name.strip().lower()[:3])), ""))
                if not month:
                    continue
                try:
                    when = date(year, month, int(day))
                except ValueError:
                    continue
                if today <= when <= horizon:
                    out.append({"date": when.isoformat(), "event": "FOMC meeting",
                                "source": "Federal Reserve", "freshness": FRESH_EOD})
        if not out:
            raise ProviderUnavailable("fed: no upcoming FOMC dates parsed")
        return sorted(out, key=lambda e: e["date"])

    def get_macro(self):
        out = {}
        for key, (series_id, unit) in SERIES.items():
            try:
                date, value = self._latest(series_id)[0]
                out[key] = fact(round(value, 2), SOURCE, FRESH_EOD, timestamp=date, unit=unit)
            except ProviderUnavailable:
                out[key] = None
        try:
            rows = self._latest(CPI_SERIES, limit=14)  # monthly; 13 apart = YoY
            if len(rows) >= 13:
                (date_now, cpi_now), (_, cpi_prior) = rows[0], rows[12]
                yoy = (cpi_now / cpi_prior - 1) * 100
                out["cpi_inflation_yoy"] = fact(round(yoy, 2), SOURCE, FRESH_EOD,
                                                timestamp=date_now, unit="%")
        except ProviderUnavailable:
            out["cpi_inflation_yoy"] = None
        if not any(out.values()):
            raise ProviderUnavailable("fred: no macro series available")
        return out
