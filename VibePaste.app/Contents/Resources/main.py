#!/usr/bin/env python3
"""VibePaste Launcher - Direct Python execution for Input Monitoring

The shebang deliberately names the framework's Python.app binary rather than
venv/bin/python3.

venv/bin/python3 is a stub that re-execs into exactly that binary. Doing the
re-exec *after* LaunchServices has launched the bundle replaces the process
image it registered, so the app never completes its check-in: NSStatusBar then
hands out a status item with zero height that is never drawn, and the menu bar
icon silently disappears. Nothing raises, and the item still reports
isVisible=True, which is what makes this failure so hard to see.

Running from a terminal does not go through LaunchServices, so the icon
appears there either way -- terminal testing cannot reproduce this.
"""

import os
import site
import sys
from pathlib import Path

# Hardcoded project root - the app may be installed in /Applications
# but the source code lives in this directory
PROJECT_ROOT = Path("/Users/sanelzulic/myprojects/vibepaste")
VENV = PROJECT_ROOT / "venv"

# Bypassing venv/bin/python3 also bypasses the venv, so put it back by hand.
site.addsitedir(str(VENV / f"lib/python3.{sys.version_info.minor}/site-packages"))
sys.prefix = sys.exec_prefix = str(VENV)
# Child processes (the overlay UI) are spawned with sys.executable; they are
# not LaunchServices-registered apps, so the venv stub is right for them.
sys.executable = str(VENV / "bin" / "python3")

os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# Redirect stdout/stderr to a log file first, so an early exit is still
# visible. The previous version checked for a running instance *before*
# this, which made a refused launch look like nothing happened at all.
log_file = PROJECT_ROOT / "vibepaste_debug.log"
sys.stdout = open(log_file, "a", buffering=1)
sys.stderr = sys.stdout

# Single-instance guard via a PID file.
#
# Matching on the command line ("pgrep -f .../MacOS/VibePaste") is unusable:
# any shell, grep or editor whose command line merely mentions that path is
# matched too, and the launch is refused for no reason. A PID file only ever
# names the process we actually started.
import subprocess

PID_FILE = Path.home() / ".vibepaste" / "vibepaste.pid"


def another_instance_running():
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)  # signal 0: existence check, doesn't touch the process
    except OSError:
        return False  # stale PID file, process is gone
    # Guard against PID reuse: the live process must actually be VibePaste.
    try:
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "VibePaste" in command


if another_instance_running():
    print("--- Launch refused: VibePaste is already running ---")
    subprocess.run([
        "osascript", "-e",
        'display notification "VibePaste is already running!" with title "🎙️ VibePaste" sound name "Pop"'
    ])
    sys.exit(0)

try:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
except OSError as e:
    print(f"Warning: could not write PID file: {e}")

print(f"--- VibePaste Launcher Started ---")
print(f"PROJECT_ROOT: {PROJECT_ROOT}")
print(f"sys.executable: {sys.executable}")
print(f"os.getcwd: {os.getcwd()}")

# Run the menubar app
from src.menubar import main

try:
    main()
except Exception as e:
    print(f"CRITICAL ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    # Don't leave a PID file behind that would block the next launch.
    try:
        if PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except (OSError, ValueError):
        pass
