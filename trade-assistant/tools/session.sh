#!/bin/bash
# One paper session, safe to run unattended from cron or launchd.
#
# A wrapper rather than a raw crontab line because cron gives a job almost no
# environment: no PATH worth having, no working directory, and no shell profile.
# Every path here is absolute for that reason, and the log is the only place a
# scheduled run can report what happened.
#
# Places no orders. `run.py paper` has no broker in it at all — the execution
# layer is a separate path that requires a per-order typed confirmation, which
# an unattended job cannot supply and must never be given a way to.
set -uo pipefail

PROJECT="/Users/thatvagowda/Desktop/fabel 5/trade-assistant"
PYTHON="/Users/thatvagowda/Desktop/fabel 5/venv/bin/python"
LOG="$PROJECT/data/session-cron.log"

cd "$PROJECT" || { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') cannot cd to $PROJECT" >> "$LOG"; exit 1; }

{
  echo ""
  echo "==================================================================="
  echo "session starting $(date -u '+%Y-%m-%dT%H:%M:%SZ') (UTC)"
  echo "==================================================================="
} >> "$LOG"

# IB Gateway has to be up and logged in. Checked explicitly so a morning with
# Gateway closed reports that plainly in the log, rather than as a wall of
# per-instrument failures or, worse, a book silently marked from the unlicensed
# fallback feed.
if ! /usr/sbin/lsof -iTCP:7497 -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  echo "IB Gateway is NOT listening on 7497 — start it and log in." >> "$LOG"
  echo "Skipping this session rather than pricing the book off yfinance." >> "$LOG"
  exit 1
fi

"$PYTHON" run.py paper >> "$LOG" 2>&1
status=$?
echo "session finished $(date -u '+%Y-%m-%dT%H:%M:%SZ') exit=$status" >> "$LOG"
exit $status
