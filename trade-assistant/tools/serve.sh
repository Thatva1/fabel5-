#!/bin/bash
# Start the dashboard without activating the virtualenv.
#
# The preview runner's sandbox cannot read `venv/pyvenv.cfg`, and Python reads
# that file during start-up whenever it is launched through a venv interpreter —
# so the process died in init_import_site before any project code ran.
#
# The venv here is a plain one over the system CommandLineTools Python 3.9, with
# no interpreter of its own, so pointing the SYSTEM interpreter at the venv's
# site-packages gives an identical import environment and never opens
# pyvenv.cfg. Same packages, same Python, one less file to be allowed to read.
set -uo pipefail

PROJECT="/Users/thatvagowda/Desktop/fabel 5/trade-assistant"
SITE="/Users/thatvagowda/Desktop/fabel 5/venv/lib/python3.9/site-packages"
PYTHON="/usr/bin/python3"

cd "$PROJECT" || exit 1
export PYTHONPATH="$SITE${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1          # so the runner sees start-up output promptly

exec "$PYTHON" run.py serve "${1:-5002}"
