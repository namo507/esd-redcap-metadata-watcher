#!/bin/bash
# Rebuild the frozen public copy of the board and check it over.
#
# WHAT PUBLISHING MEANS NOW
#
# Nothing here uploads anything. The board is published by GitHub Actions: a
# push to main runs .github/workflows/visitboard-frontend.yml, which builds the
# same snapshot this script builds and commits it to docs/visitboard/, where
# GitHub Pages serves it. Publishing is therefore `git push`, and there is no
# second, hidden way to put a different build live.
#
# This script used to run `netlify deploy --prod`. That host is gone, and a
# script that still tried it would either fail or, worse, succeed against a
# stale site nobody reads. It builds and inspects instead.
#
# WHAT IT IS STILL FOR
#
#   ./automation/publish.sh          build the snapshot and report on it
#   ./automation/publish.sh --check  the same, and fail if the build looks wrong
#
# The inbox job calls it after importing a calendar, so a print dropped in
# data/inbox is proved to still produce a valid snapshot without waiting for
# CI to say so.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="${ESD_PYTHON:-python3}"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

echo "  building the public copy ..."
"$PYTHON" -m backend.build_static

OUT="dist-static"
BOARD="$OUT/board.json"

if [ ! -s "$BOARD" ]; then
  echo "  BUILD FAILED: $BOARD is missing or empty."
  exit 1
fi

# The snapshot is what a visitor gets when the API cannot be reached, so an
# empty one is a blank board rather than an obvious error. Worth one look.
VISITS=$("$PYTHON" - <<'PY'
import json
with open("dist-static/board.json", encoding="utf-8") as fh:
    print(len(json.load(fh).get("visits", [])))
PY
)
echo "  built into $OUT/ with $VISITS visit(s) in the snapshot."

if [ "$CHECK" = "1" ] && [ "$VISITS" = "0" ]; then
  echo "  CHECK FAILED: the snapshot has no visits, so the offline board is blank."
  exit 1
fi

echo "  to publish: commit and push to main. The frontend workflow rebuilds"
echo "  this snapshot and serves it from docs/visitboard/ on GitHub Pages."
