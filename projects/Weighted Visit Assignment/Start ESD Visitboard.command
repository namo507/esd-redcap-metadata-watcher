#!/bin/bash
# Double-click this file to open the Visitboard.
#
# It sweeps any calendar PDFs dropped in data/inbox, starts the board, and
# opens it in a browser. Nothing to type. Closing the Terminal window that
# opens stops the board again.

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

PYTHON="${ESD_PYTHON:-python3}"
PORT="${ESD_PORT:-8765}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 is not installed on this Mac."
  echo "Install it from https://www.python.org/downloads/ and double-click this again."
  echo
  read -r -p "Press return to close."
  exit 1
fi

printf '\n  ESD Visitboard\n  --------------\n\n'

# Anything waiting in the inbox goes in first, so the board opens with it
# already applied rather than showing yesterday's picture.
mkdir -p data/inbox
if ls data/inbox/*.pdf >/dev/null 2>&1; then
  echo "  Importing calendars from data/inbox ..."
  "$PYTHON" -m esd_scheduler import-inbox || echo "  (one file could not be read; it was left in the inbox)"
  echo
fi

# Free the port if a previous run is still holding it, so a second double-click
# reopens the board instead of failing with "address already in use".
if lsof -ti ":$PORT" >/dev/null 2>&1; then
  echo "  Stopping the board already running on port $PORT ..."
  lsof -ti ":$PORT" | xargs kill 2>/dev/null || true
  sleep 1
fi

echo "  Opening http://127.0.0.1:$PORT"
echo "  Leave this window open while you use the board. Close it to stop."
echo

( sleep 2; open "http://127.0.0.1:$PORT" ) &
"$PYTHON" -m backend.server --no-browser --port "$PORT"
