"""The scan snapshot — so a scan run by cron is visible to the dashboard."""
import json
from datetime import datetime, timedelta, timezone

from assistant import pipeline


def result(scanned=3, finished=None):
    return {
        "watchlist": [{"ticker": "AAPL", "setup_count": 1, "price": 300.0}],
        "ideas": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        "cancelled": False, "scanned": scanned, "total": scanned,
        "finished_at": finished or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def test_a_saved_scan_can_be_read_back(tmp_path):
    path = str(tmp_path / "last_scan.json")
    pipeline.save_scan_snapshot(result(), path=path)
    loaded = pipeline.load_scan_snapshot(path=path)
    assert loaded["scanned"] == 3
    assert loaded["watchlist"][0]["ticker"] == "AAPL"


def test_ideas_are_not_duplicated_into_the_snapshot(tmp_path):
    """They are already in the journal. A second copy invites the two to
    disagree about the same idea."""
    path = str(tmp_path / "last_scan.json")
    pipeline.save_scan_snapshot(result(), path=path)
    payload = json.loads(open(path).read())
    assert "ideas" not in payload
    assert payload["idea_count"] == 2


def test_a_stale_snapshot_is_not_served(tmp_path):
    """A watchlist page showing last week's prices as this morning's is the
    exact failure this project keeps having."""
    path = str(tmp_path / "last_scan.json")
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(timespec="seconds")
    pipeline.save_scan_snapshot(result(finished=old), path=path)
    assert pipeline.load_scan_snapshot(path=path, max_age_hours=36) is None
    # Still readable when the caller accepts the age.
    assert pipeline.load_scan_snapshot(path=path, max_age_hours=0) is not None


def test_a_recent_snapshot_reports_its_age(tmp_path):
    path = str(tmp_path / "last_scan.json")
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    pipeline.save_scan_snapshot(result(finished=recent), path=path)
    assert pipeline.load_scan_snapshot(path=path)["age_hours"] == 2.0


def test_a_missing_snapshot_is_not_an_error(tmp_path):
    assert pipeline.load_scan_snapshot(path=str(tmp_path / "nope.json")) is None


def test_a_corrupt_snapshot_is_not_an_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert pipeline.load_scan_snapshot(path=str(path)) is None


def test_an_unwritable_path_does_not_fail_the_scan(tmp_path):
    """Losing the snapshot must never lose the scan that produced it."""
    pipeline.save_scan_snapshot(result(), path="/nonexistent-dir/x/last_scan.json")
