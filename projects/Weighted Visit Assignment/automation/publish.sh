#!/bin/bash
# Rebuild the public copy and push it live.
#
# Called on its own, or by the inbox job after it imports something, so a
# calendar dropped in data/inbox reaches the published site without anyone
# running a command.
#
# Deploying is deliberately opt-in. It publishes to a public URL, so it only
# runs when the operator has said it should: set ESD_AUTO_PUBLISH=1, or pass
# --force. Without that this builds and stops, which is still useful.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="${ESD_PYTHON:-python3}"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

echo "  building the public copy ..."
"$PYTHON" -m backend.build_static

if [ "$FORCE" != "1" ] && [ "${ESD_AUTO_PUBLISH:-0}" != "1" ]; then
  echo "  built into dist-static/."
  echo "  not deploying: set ESD_AUTO_PUBLISH=1 or pass --force."
  echo "  the site is public, so publishing is never automatic by default."
  exit 0
fi

if ! command -v netlify >/dev/null 2>&1; then
  echo "  netlify CLI is not installed, so there is nothing to deploy with."
  echo "  npm install -g netlify-cli"
  exit 1
fi

echo "  deploying ..."
netlify deploy --prod --dir dist-static
