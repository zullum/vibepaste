#!/bin/bash
# Build VibePaste.app's executable and install the bundle.
#
# The executable must be a real binary rather than a #! script -- see the
# comment at the top of launcher.c for why. Re-run this after changing
# launcher.c or Contents/Resources/main.py.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$PROJECT_ROOT/VibePaste.app"
FRAMEWORKS=/opt/homebrew/opt/python@3.14/Frameworks
HEADERS="$FRAMEWORKS/Python.framework/Versions/3.14/include/python3.14"

echo "==> Compiling launcher"
mkdir -p "$APP/Contents/MacOS"
clang -O2 -Wall \
    -o "$APP/Contents/MacOS/VibePaste" \
    "$PROJECT_ROOT/tools/launcher.c" \
    -I"$HEADERS" \
    -F"$FRAMEWORKS" -framework Python \
    -rpath "$FRAMEWORKS"

# Ad-hoc signing gives the bundle a stable identity, so the Accessibility,
# Input Monitoring and Microphone grants survive a rebuild.
echo "==> Signing"
codesign --force --sign - --timestamp=none "$APP"

echo "==> Verifying"
codesign --verify --verbose "$APP" 2>&1 | sed 's/^/    /'
file "$APP/Contents/MacOS/VibePaste" | sed 's/^/    /'

if [ "${1:-}" = "--install" ]; then
    echo "==> Installing to /Applications"
    rm -rf /Applications/VibePaste.app
    cp -R "$APP" /Applications/VibePaste.app
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
        -f /Applications/VibePaste.app
fi

echo "==> Done"
