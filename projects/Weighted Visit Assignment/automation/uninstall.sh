#!/bin/bash
# Unload and remove the ESD scheduler launchd agents. Leaves data/, reports/
# and logs/ untouched: the audit log is append-only and outlives the automation.

set -euo pipefail

AGENT_DIR="$HOME/Library/LaunchAgents"
for label in \
  org.esdlab.scheduler.calsync \
  org.esdlab.scheduler.reconcile \
  org.esdlab.scheduler.shadow \
  org.esdlab.scheduler.debrief
do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "unloaded $label" || true
  rm -f "$AGENT_DIR/$label.plist" && echo "removed $label.plist" || true
done
echo "done. data/, reports/ and logs/ were left in place."
