#!/usr/bin/env bash
# Tracker health: definitions valid, design fully covered, dashboard fresh, no regressions, tracker tests green.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
$PY -m tools.track validate
$PY -m tools.track check-coverage
$PY -m tools.track report --check
# verify-done re-runs every done task's checks (minutes); run it nightly or with --full
[ "${1:-}" = "--full" ] && $PY -m tools.track verify-done
$PY -m pytest tests/tracking -q || exit 1
[ "${1:-}" = "--full" ] && $PY -m pytest tests -q
exit 0
