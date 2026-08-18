"""Run the trading day from inside the dashboard process.

WHY IT LIVES HERE, WHICH IS NOT WHERE IT BELONGS

Scheduling is an operating system's job, and this was written twice as one —
first a crontab, then a launchd agent. Both fired exactly on time and both were
refused by macOS with the same line:

    /bin/bash: .../tools/trading-day.sh: Operation not permitted

The project sits in ~/Desktop, which macOS protects. A background scheduler is
not part of the login session, so it is denied that folder outright and
silently: the jobs ran, did nothing, wrote nothing, and the only trace was a
mail spool nobody reads. Measured from cron, with the launcher moved outside
the protected folder to rule that out: reading a project file DENIED, executing
a project script DENIED, running the virtualenv's Python DENIED.

The dashboard process has no such problem. It was started from a terminal that
already holds the permission, so it reads and writes the project all day. Doing
the scheduling here needs no privilege grant and no move.

THE PRICE, STATED PLAINLY

Nothing runs when the dashboard is not running. An OS scheduler would survive a
reboot; this does not. That is a genuine downgrade, and the honest fix is to
move the project out of ~/Desktop — at which point cron works with no
permissions at all and this module can go away.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

# Local wall-clock times, weekdays only. These mirror the trading day: an hour
# before each open so the book and the ideas are current before anything
# trades, and once after the US close to record the day.
DEFAULT_JOBS = [
    {"at": "07:00", "mode": "both", "why": "1h before the London open"},
    {"at": "08:05", "mode": "session", "why": "just after the London open"},
    {"at": "13:30", "mode": "both", "why": "1h before the US open"},
    {"at": "21:10", "mode": "session", "why": "after the US close"},
]

_state = {
    "running": None,        # mode currently executing, or None
    "last": [],             # completed runs, newest first
    "started_at": None,
    "jobs": [],
}
_lock = threading.Lock()


def _now_local():
    return datetime.now()


def _next_fire(job, after):
    """Next weekday occurrence of this job's time, strictly after `after`."""
    hour, minute = (int(x) for x in job["at"].split(":"))
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:         # Saturday, Sunday
        candidate += timedelta(days=1)
    return candidate


def status():
    with _lock:
        jobs = [dict(j) for j in _state["jobs"]]
        return {
            "enabled": bool(jobs),
            "running": _state["running"],
            "started_at": _state["started_at"],
            "jobs": jobs,
            "last": list(_state["last"][:10]),
        }


def _record(mode, ok, detail, started, finished):
    with _lock:
        _state["last"].insert(0, {
            "mode": mode, "ok": ok, "detail": detail,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "seconds": round((finished - started).total_seconds(), 1),
        })
        del _state["last"][20:]


def run_job(mode):
    """Run a session, a scan, or both. Returns (ok, detail).

    Called on the scheduler thread and by the manual endpoint, so it guards
    against overlapping runs: a session started while another is mid-rebalance
    would have two processes writing the same book.
    """
    from .. import pipeline
    from ..core.config import load_config
    from ..paper import session as paper_session

    with _lock:
        if _state["running"]:
            return False, f"already running ({_state['running']})"
        _state["running"] = mode

    started = datetime.now(timezone.utc)
    notes = []
    ok = True
    try:
        config = load_config()

        # Refuse rather than degrade. Without the licensed feed a session would
        # mark the book from the unlicensed fallback, which is the exact
        # failure this project spent days removing.
        from ..providers.ibkr_provider import IBKRDataProvider
        if not IBKRDataProvider(config).is_available():
            return False, "IB Gateway is not reachable — nothing was run"

        if mode in ("session", "both"):
            out = paper_session.run(config=config)
            notes.append(f"session: {len(out.get('opened') or [])} opened, "
                         f"{len(out.get('closed') or [])} closed, "
                         f"equity {out.get('equity')}")
        if mode in ("scan", "both"):
            result = pipeline.run_scan(config=config)
            notes.append(f"scan: {result.get('scanned')} instruments, "
                         f"{len(result.get('ideas') or [])} ideas")
    except Exception as exc:
        ok = False
        notes.append(f"{type(exc).__name__}: {exc}")
    finally:
        finished = datetime.now(timezone.utc)
        with _lock:
            _state["running"] = None
        _record(mode, ok, "; ".join(notes) or "no detail", started, finished)

    return ok, "; ".join(notes)


def _loop():
    while True:
        try:
            now = _now_local()
            with _lock:
                due = [j for j in _state["jobs"]
                       if datetime.fromisoformat(j["next_fire"]) <= now]
                for job in due:
                    job["next_fire"] = _next_fire(job, now).isoformat(timespec="seconds")
            for job in due:
                run_job(job["mode"])
        except Exception:
            # The scheduler thread must never die; a bad job is one bad job.
            pass
        time.sleep(20)


def start(jobs=None):
    """Begin scheduling. Idempotent — a second call is ignored."""
    with _lock:
        if _state["started_at"]:
            return status()
        now = _now_local()
        _state["jobs"] = [
            {**job, "next_fire": _next_fire(job, now).isoformat(timespec="seconds")}
            for job in (jobs or DEFAULT_JOBS)
        ]
        _state["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    thread = threading.Thread(target=_loop, name="trading-day-scheduler", daemon=True)
    thread.start()
    return status()
