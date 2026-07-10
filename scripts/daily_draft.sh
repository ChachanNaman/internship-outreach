#!/bin/bash
# Runs once a day via launchd: loads all sources, verifies + drafts up to
# DAILY_CAP new contacts (never sends -- that's a manual step), and pops a
# macOS notification with the result so nothing requires opening a chat/IDE.
set -euo pipefail

PROJECT_DIR="/Users/namanchachan/Projects/Outreach"
DAILY_CAP=40

cd "$PROJECT_DIR"

OUTPUT=$("$PROJECT_DIR/venv/bin/python" outreach.py --dry-run --cap "$DAILY_CAP" 2>&1) || true

echo "$OUTPUT" >> "$PROJECT_DIR/scripts/daily_draft.log"

DRAFTED=$(echo "$OUTPUT" | grep -oE "Drafted: [0-9]+" | grep -oE "[0-9]+" || echo "0")

if [ "$DRAFTED" -gt 0 ]; then
    osascript -e "display notification \"$DRAFTED new drafts ready in $PROJECT_DIR/drafts -- run 'python outreach.py --live' to send them.\" with title \"Outreach: daily drafts ready\""
else
    osascript -e "display notification \"No new drafts today (quota exhausted or nothing left to process). Check daily_draft.log.\" with title \"Outreach: nothing drafted\""
fi
