#!/bin/bash
# SelfTracker installer - macOS / Linux
set -e

REPO_BASE="https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/main"
DIR="$HOME/.spytracker"
SCRIPT="$DIR/tracker.py"
CFG="$DIR/config.json"

mkdir -p "$DIR"

echo "Downloading SelfTracker..."
curl -fsSL "$REPO_BASE/tracker.py" -o "$SCRIPT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required (macOS: run 'xcode-select --install' or install from python.org)." >&2
  exit 1
fi

if [ ! -f "$CFG" ]; then
  echo "Paste your Discord webhook URL (Discord server > channel settings > Integrations > Webhooks)."
  read -r -p "Webhook URL (press Enter to skip): " URL
  if [ -n "$URL" ]; then
    python3 -c "import json,sys; json.dump({'webhook_url': sys.argv[1]}, open('$CFG','w'))" "$URL"
  fi
fi

python3 "$SCRIPT" --install
