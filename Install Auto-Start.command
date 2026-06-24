#!/bin/bash
# Installs Ibis Publisher as a background service that starts on login
# Double-click this file to install

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/ibis-publisher"

# Find python3
PYTHON=$(which python3 2>/dev/null || echo "/Library/Developer/CommandLineTools/usr/bin/python3")

PLIST="$HOME/Library/LaunchAgents/com.ibispublisher.app.plist"

cat > "$PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ibispublisher.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$APP_DIR/run.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$APP_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/IbisPublisher.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/IbisPublisher.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/opt/homebrew/bin:/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST

# Load it now
launchctl load "$PLIST" 2>/dev/null
launchctl start com.ibispublisher.app 2>/dev/null

osascript -e 'display dialog "✅ Ibis Publisher auto-start installed!\n\nIt will now:\n• Start automatically when you log in\n• Run silently in the background\n• Open at http://localhost:8765 in your browser\n\nOpening now..." buttons {"OK"} default button "OK"'

sleep 2
open "http://localhost:8765"
