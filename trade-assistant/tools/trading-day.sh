#!/bin/bash
# Everything a trading day needs, unattended.
#
#   session  — mark the book, take exits, rebalance if due
#   scan     — research the watchlist so the dashboard has CURRENT ideas
#
# Run with `session`, `scan`, or `both`. Cron passes one of those.
#
# Places no orders. `run.py paper` has no broker in it, and the execution path
# needs a per-order typed confirmation that an unattended job cannot supply and
# must never be given a way to.
#
# A wrapper rather than raw crontab lines because cron supplies almost no
# environment: no useful PATH, no working directory, no profile. Every path
# here is absolute, and the log is the only channel a scheduled run has.
set -uo pipefail

MODE="${1:-both}"
PROJECT="/Users/thatvagowda/Desktop/fabel 5/trade-assistant"
PYTHON="/Users/thatvagowda/Desktop/fabel 5/venv/bin/python"
LOG="$PROJECT/data/session-cron.log"

cd "$PROJECT" || { echo "cannot cd to $PROJECT" >> "$LOG"; exit 1; }

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" >> "$LOG"; }

{
  echo ""
  echo "==================================================================="
  echo "trading-day.sh ($MODE) starting $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"
  echo "==================================================================="
} >> "$LOG"

# IB Gateway must be up and logged in. Checked explicitly so a morning with it
# closed reports that in one plain line, rather than as a wall of per-instrument
# failures or — far worse — a book quietly marked from the unlicensed fallback.
if ! /usr/sbin/lsof -iTCP:7497 -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  log "IB Gateway is NOT listening on 7497. Nothing was run."
  log "Start Gateway and log in; this job runs again at its next scheduled time."
  exit 1
fi

status=0

if [ "$MODE" = "session" ] || [ "$MODE" = "both" ]; then
  log "--- paper session ---"
  "$PYTHON" run.py paper >> "$LOG" 2>&1 || status=$?
fi

if [ "$MODE" = "scan" ] || [ "$MODE" = "both" ]; then
  # The scan calls the language model once per instrument that produces a
  # setup, so it costs real money per run. That is why it is scheduled twice a
  # day rather than continuously, and why the count is logged.
  log "--- watchlist scan ---"
  "$PYTHON" run.py scan >> "$LOG" 2>&1 || status=$?
fi

log "finished exit=$status"
exit $status
