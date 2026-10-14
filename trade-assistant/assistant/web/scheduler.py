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
    {"at": "07:30", "mode": "intraday-prepare:uk",
     "why": "choose the UK version's intraday shares, before the London open"},
    {"at": "13:00", "mode": "intraday-prepare:us",
     "why": "choose the US version's intraday shares, before the US open"},
    {"at": "13:30", "mode": "both", "why": "1h before the US open"},
    {"at": "21:10", "mode": "session", "why": "after the US close"},
    {"at": "22:15", "mode": "learn",
     "why": "each country's learning agent reviews the day and tests changes"},
]

_state = {
    "running": None,        # mode currently executing, or None
    "last": [],             # completed runs, newest first
    "started_at": None,
    "jobs": [],
    # The intraday loop is tracked separately from the daily jobs on purpose.
    # It runs on a completely different clock — every bar while a market is
    # open, rather than four times a day — and sharing the `running` guard would
    # mean a five-minute tick could block the evening session, or worse, that a
    # forty-minute universe rebalance could stop the intraday book liquidating
    # into the close.
    "intraday": {"running": False, "last": [], "ticks": 0, "started_at": None,
                 "next_tick": None},
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
        # Learning replays history from Yahoo and needs no broker connection.
        if mode != "learn" and not IBKRDataProvider(config).is_available():
            return False, "IB Gateway is not reachable — nothing was run"

        if mode in ("session", "both"):
            # Every version of the agent: the US first (the original book), then
            # each country, each with its own market facts and tuning.
            from ..agents import versions
            for name, report in versions.run_all(config).items():
                if report.get("error"):
                    notes.append(f"{name}: {report['error']}")
                else:
                    notes.append(f"{name}: {len(report.get('opened') or [])} opened, "
                                 f"{len(report.get('closed') or [])} closed")
        if mode == "learn":
            from ..agents import learner
            for name, row in learner.learn_all(config).items():
                if row.get("error"):
                    notes.append(f"{name} learning: {row['error']}")
                else:
                    done = [p for p in row.get("proposals") or [] if p.get("result") == "applied"]
                    notes.append(f"{name} learning: {len(row.get('proposals') or [])} proposed, "
                                 f"{len(done)} applied")
        if mode in ("scan", "both"):
            result = pipeline.run_scan(config=config)
            notes.append(f"scan: {result.get('scanned')} instruments, "
                         f"{len(result.get('ideas') or [])} ideas")
        if mode.startswith("intraday-prepare"):
            # "intraday-prepare:uk" prepares one version; the bare mode, all.
            from ..agents import versions
            wanted = mode.split(":", 1)[1:] or versions.intraday_versions(config)
            for name in wanted:
                if name not in versions.intraday_versions(config):
                    notes.append(f"{name}: no intraday engine in this version")
                    continue
                out = versions.prepare_intraday(name, config, pipeline.get_router(config))
                if out.get("error"):
                    ok = False
                    notes.append(f"{name} intraday prepare: {out['error']}")
                else:
                    notes.append(f"{name} intraday prepare: {out.get('summary') or 'done'} "
                                 f"({out.get('fetch_seconds')}s)")

        # Rebuild the public site from whatever the run just produced. Doing it
        # here rather than on a separate timer means the published page cannot
        # be older than the book behind it — a public page showing last week's
        # equity is the same failure as a dashboard doing it, with an audience.
        publish_cfg = (config.get("publish") or {})
        if publish_cfg.get("enabled"):
            try:
                import os

                from ..core.config import PROJECT_ROOT
                from ..publish import export

                out_dir = publish_cfg.get("out_dir") or os.path.join(PROJECT_ROOT, "public")
                export.write(out_dir, config,
                             include_positions=bool(publish_cfg.get("include_positions")),
                             title=publish_cfg.get("title") or "Trade Assistant")
                notes.append(f"published to {out_dir}")
            except Exception as exc:
                notes.append(f"publish failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        ok = False
        notes.append(f"{type(exc).__name__}: {exc}")
    finally:
        finished = datetime.now(timezone.utc)
        with _lock:
            _state["running"] = None
        _record(mode, ok, "; ".join(notes) or "no detail", started, finished)

    return ok, "; ".join(notes)


def intraday_status():
    with _lock:
        state = _state["intraday"]
        return {"enabled": bool(state["started_at"]), "running": state["running"],
                "ticks": state["ticks"], "next_tick": state["next_tick"],
                "started_at": state["started_at"],
                "last": list(state["last"][:10])}


def run_intraday_tick(force=False):
    """One pass of the intraday loop. Returns (ok, detail).

    Guarded by its own flag rather than the daily one. A tick that overlaps
    itself would have two passes marking and closing the same book, and the
    second would save over the first's exits — a position closed at 20:55 could
    reappear, open, after the bell.
    """
    from ..core.config import load_config
    from ..paper import intraday_runner

    with _lock:
        if _state["intraday"]["running"]:
            return False, "a tick is already running"
        _state["intraday"]["running"] = True

    started = datetime.now(timezone.utc)
    ok, detail = True, ""
    try:
        from .. import pipeline
        from ..agents import versions
        config = load_config()
        router = pipeline.get_router(config)
        # Each version's intraday engine, only while its own market is open.
        parts = []
        for name in versions.intraday_versions(config):
            out = versions.tick_intraday(name, config, router, force=force)
            if out.get("skipped"):
                continue
            if out.get("error"):
                ok = False
                parts.append(f"{name}: {out['error']}")
                continue
            part = (f"{name}: {len(out.get('opened') or [])} opened, "
                    f"{len(out.get('closed') or [])} closed, "
                    f"equity {out.get('equity')}, "
                    f"bars {out.get('staleness_minutes')}m old")
            for note in (out.get("notes") or []):
                if note.startswith(("HELD BUT UNPRICED", "OVERNIGHT RISK")):
                    part += f" | {note}"
            parts.append(part)
        detail = "; ".join(parts) or "no market open"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    finally:
        finished = datetime.now(timezone.utc)
        with _lock:
            state = _state["intraday"]
            state["running"] = False
            state["ticks"] += 1
            state["last"].insert(0, {
                "ok": ok, "detail": detail,
                "at": finished.isoformat(timespec="seconds"),
                "seconds": round((finished - started).total_seconds(), 1)})
            del state["last"][20:]
    return ok, detail


def _intraday_loop(every_minutes):
    """Tick on the bar, and only while something is open.

    Aligned to the bar boundary rather than to whenever the process started: a
    loop that fires at :02 and :07 is always reading a bar that is two minutes
    from complete, and the strategies would see a different, partial final bar
    every single pass.
    """
    from ..core import market_clock

    period = max(1, int(every_minutes)) * 60
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Sleep to the next boundary, plus a few seconds so the bar IB is
            # about to hand us has actually closed.
            elapsed = (now.minute * 60 + now.second) % period
            wait = period - elapsed + 10
            with _lock:
                _state["intraday"]["next_tick"] = (
                    now + timedelta(seconds=wait)).isoformat(timespec="seconds")
            time.sleep(wait)

            if any(market_clock.is_open(v) for v in ("US", "LSE")):
                run_intraday_tick()
        except Exception:
            # The loop must never die. A tick that throws is one bad tick, and
            # a dead intraday loop with open positions is the overnight risk
            # this whole engine is built to prevent.
            time.sleep(30)


def start_intraday(every_minutes=5):
    """Begin the intraday loop. Idempotent."""
    with _lock:
        if _state["intraday"]["started_at"]:
            return intraday_status()
        _state["intraday"]["started_at"] = datetime.now(
            timezone.utc).isoformat(timespec="seconds")

    thread = threading.Thread(target=_intraday_loop, args=(every_minutes,),
                              name="intraday-loop", daemon=True)
    thread.start()
    return intraday_status()


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
