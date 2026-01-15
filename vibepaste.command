#!/bin/bash
# VibePaste Menu Bar - Double-click to add 🎙️ icon to menu bar
# Checks if already running, prevents duplicates

cd "$(dirname "$0")"

# Check if menu bar app is already running
if pgrep -f "python.*menubar" > /dev/null 2>&1; then
    osascript -e 'display notification "VibePaste menu bar is already running!" with title "🎙️ VibePaste" sound name "Pop"'
    exit 0
fi

# Also check if main is running standalone
if pgrep -f "python.*src.main" > /dev/null 2>&1; then
    osascript -e 'display notification "VibePaste is already running in terminal mode!" with title "🎙️ VibePaste" sound name "Pop"'
    exit 0
fi

# Activate venv and run menu bar app
source venv/bin/activate
python3 -m src.menubar
