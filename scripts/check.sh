#!/usr/bin/env bash
# Tracker health: definitions valid, design fully covered, dashboard fresh, no regressions, tracker tests green.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
$PY -m tools.track validate
$PY -m tools.track check-coverage
$PY -m tools.track report --check
$PY -m tools.track verify-done
$PY -m pytest tests/tracking -q
