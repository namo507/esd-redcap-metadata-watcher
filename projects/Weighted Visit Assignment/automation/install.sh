#!/bin/bash
# Install the four scheduled jobs as user launchd agents (macOS).
#
#   ./automation/install.sh          install and load
#   ./automation/install.sh --dry    print the plists without installing
#   ./automation/uninstall.sh        unload and remove
#
# These are *user* agents under ~/Library/LaunchAgents. They need no admin
# rights, they run only while the user is logged in, and they touch nothing
# outside this project directory.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$HOME/Library/LaunchAgents"
DRY_RUN=0
[ "${1:-}" = "--dry" ] && DRY_RUN=1

emit_plist() {
  local label="$1" job="$2" schedule="$3"
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${PROJECT_DIR}/automation/run.sh</string>
        <string>${job}</string>
    </array>
    <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
${schedule}
    <key>StandardOutPath</key><string>${PROJECT_DIR}/logs/launchd-${job}.out</string>
    <key>StandardErrorPath</key><string>${PROJECT_DIR}/logs/launchd-${job}.err</string>
    <key>RunAtLoad</key><false/>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST
}

# Delta sync every 5 minutes. This is what keeps the <=72h freshness class
# inside its 15 minute hard threshold with room to spare.
SCHED_CALSYNC="    <key>StartInterval</key><integer>300</integer>"

# Full reconcile nightly at 02:00.
SCHED_RECONCILE="    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>"

# Shadow optimiser Monday 06:45, so its regret numbers are in the log before
# the debrief renders at 07:00.
SCHED_SHADOW="    <key>StartCalendarInterval</key>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>45</integer></dict>"

# Weekly debrief Monday 07:00, ahead of the lab meeting.
SCHED_DEBRIEF="    <key>StartCalendarInterval</key>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>"

# Inbox sweep every 10 minutes. Slower than the calendar delta sync on purpose:
# a dropped file is a human action, and a ten minute wait costs nothing while
# halving the chance of grabbing a PDF mid-copy.
SCHED_CALENDARS="    <key>StartInterval</key><integer>600</integer>"

# Audit summary Monday 07:15, just after the debrief renders.
SCHED_AUDIT="    <key>StartCalendarInterval</key>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>15</integer></dict>"

JOBS=(
  "org.esdlab.scheduler.calsync|calsync|SCHED_CALSYNC"
  "org.esdlab.scheduler.calendars|calendars|SCHED_CALENDARS"
  "org.esdlab.scheduler.audit|audit|SCHED_AUDIT"
  "org.esdlab.scheduler.reconcile|reconcile|SCHED_RECONCILE"
  "org.esdlab.scheduler.shadow|shadow|SCHED_SHADOW"
  "org.esdlab.scheduler.debrief|debrief|SCHED_DEBRIEF"
)

mkdir -p "$PROJECT_DIR/logs"
chmod +x "$PROJECT_DIR/automation/run.sh"

for entry in "${JOBS[@]}"; do
  IFS='|' read -r label job schedvar <<<"$entry"
  plist="$(emit_plist "$label" "$job" "${!schedvar}")"
  if [ "$DRY_RUN" = "1" ]; then
    echo "--- $AGENT_DIR/$label.plist ---"
    echo "$plist"
    continue
  fi
  mkdir -p "$AGENT_DIR"
  echo "$plist" >"$AGENT_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENT_DIR/$label.plist"
  echo "installed $label ($job)"
done

if [ "$DRY_RUN" = "0" ]; then
  echo
  echo "loaded agents:"
  launchctl list | grep -E 'org\.esdlab\.scheduler' || echo "  (none yet; give launchd a moment)"
  echo
  echo "Logs:      $PROJECT_DIR/logs/"
  echo "Reports:   $PROJECT_DIR/reports/"
  echo "Uninstall: ./automation/uninstall.sh"
fi
