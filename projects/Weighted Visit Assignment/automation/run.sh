#!/bin/bash
# ESD visit scheduling: single entry point for every scheduled job.
#
#   run.sh calsync      every 5 minutes   delta pull of Outlook / Google
#   run.sh reconcile    nightly 02:00     full calendar reconcile + integrity check
#   run.sh debrief      Monday 07:00      weekly drift + debrief report
#   run.sh shadow       Monday 06:45      shadow optimiser, records regret
#
# Every job is idempotent and safe to re-run. Logs go to logs/<job>.log so
# launchd's own stdout capture stays clean.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="${ESD_PYTHON:-python3}"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR" data data/inbox data/uploads reports

JOB="${1:-}"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LOG="$LOG_DIR/${JOB:-unknown}.log"

log() { printf '%s %s\n' "$STAMP" "$*" | tee -a "$LOG"; }

# Load credentials if the operator has configured them. Keep secrets out of the
# repo: this file is gitignored and holds ESD_CAL_PROVIDER / ESD_CAL_TOKEN /
# ESD_CAL_MAP. Without it the engine falls back to the mock provider.
if [ -f "$PROJECT_DIR/config/calendar.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "$PROJECT_DIR/config/calendar.env"; set +a
fi

case "$JOB" in
  calsync)
    log "calendar delta sync starting"
    if ! "$PYTHON" -m esd_scheduler sync >>"$LOG" 2>&1; then
      log "SYNC DEGRADED: one or more coordinators failed. Layer 1 will treat"
      log "  them as calendar_unavailable rather than assuming they are free."
      exit 1
    fi
    log "calendar delta sync ok"
    ;;

  calendars)
    # Unattended half of the calendar upload feature. A coordinator drops an
    # Outlook print into data/inbox and this files it: parsed, recorded in the
    # audit store, original moved to data/uploads so it cannot be imported
    # twice. A running board notices the new rows on its next read.
    if ! "$PYTHON" -m esd_scheduler import-inbox >>"$LOG" 2>&1; then
      log "INBOX IMPORT FAILED: a file in data/inbox could not be read. It has"
      log "  been left in place for inspection rather than filed away."
      exit 1
    fi
    log "inbox processed"

    # A calendar that has been imported should reach the published copy without
    # anybody running a command. Building always; deploying only when the
    # operator has opted in, because the destination is a public URL.
    if [ -x "$PROJECT_DIR/automation/publish.sh" ]; then
      "$PROJECT_DIR/automation/publish.sh" >>"$LOG" 2>&1 \
        && log "public copy rebuilt" \
        || log "public copy rebuild failed; the board itself is unaffected"
    fi
    ;;

  publish)
    log "rebuilding the public copy"
    "$PROJECT_DIR/automation/publish.sh" >>"$LOG" 2>&1
    log "done"
    ;;

  audit)
    log "writing audit summary"
    mkdir -p reports
    "$PYTHON" -m esd_scheduler audit >"reports/audit-$(date -u +%Y-%m-%d).txt" 2>>"$LOG"
    log "audit written to reports/audit-$(date -u +%Y-%m-%d).txt"
    ;;

  reconcile)
    log "nightly full reconcile starting"
    "$PYTHON" -m esd_scheduler sync >>"$LOG" 2>&1 || log "reconcile sync degraded"
    # Integrity check: the audit log is append-only, so a shrinking row count
    # means something outside this tool has touched the database.
    "$PYTHON" - <<'PY' >>"$LOG" 2>&1
import sqlite3, os, json
db = os.path.join("data", "esd_scheduler.db")
con = sqlite3.connect(db)
counts = {
    t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    for t in ("scoring_run", "candidate_score", "assignment_outcome",
              "calendar_sync_log", "optimizer_shadow")
}
state_path = os.path.join("data", "rowcounts.json")
previous = {}
if os.path.exists(state_path):
    with open(state_path) as fh:
        previous = json.load(fh)
for table, count in counts.items():
    if count < previous.get(table, 0):
        print(f"INTEGRITY ALERT: {table} shrank {previous[table]} -> {count}")
with open(state_path, "w") as fh:
    json.dump(counts, fh, indent=2)
print("row counts:", counts)
con.close()
PY
    log "nightly reconcile done"
    ;;

  shadow)
    log "shadow optimiser starting"
    "$PYTHON" -m esd_scheduler plan-week --verbose >>"$LOG" 2>&1
    log "shadow optimiser done"
    ;;

  debrief)
    WEEK_START="$(date -v-mon -v-7d +%Y-%m-%d 2>/dev/null || date -d 'last monday -7 days' +%Y-%m-%d)"
    LABEL="$(date -v-7d +%Y-W%V 2>/dev/null || date -d '7 days ago' +%Y-W%V)"
    log "weekly debrief for $LABEL (week starting $WEEK_START)"
    "$PYTHON" -m esd_scheduler drift --week-start "${WEEK_START}T00:00:00" >>"$LOG" 2>&1
    "$PYTHON" -m esd_scheduler debrief \
        --week-start "${WEEK_START}T00:00:00" --label "$LABEL" >>"$LOG" 2>&1
    # Re-calibrate the review band from the accumulated log, but never write it
    # automatically: weights and bands change only at a version boundary, with
    # a human approving. This just reports what the data now supports.
    "$PYTHON" -m esd_scheduler calibrate >>"$LOG" 2>&1 || true
    log "debrief written to reports/debrief-$LABEL.md"
    if command -v open >/dev/null 2>&1 && [ "${ESD_OPEN_REPORT:-0}" = "1" ]; then
      open "reports/debrief-$LABEL.html"
    fi
    ;;

  *)
    echo "usage: run.sh {calsync|reconcile|shadow|debrief}" >&2
    exit 2
    ;;
esac
