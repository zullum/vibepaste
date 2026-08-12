"""Manual check: does the app actually come back after restarting itself?

Not part of the suite. It genuinely quits and relaunches VibePaste, which is
the whole point — the unit tests prove the decision logic against fakes, and
this proves the part only macOS can answer: that a detached helper outlives
the process that armed it, that LaunchServices starts a fresh instance once
the old one is gone, and that the launcher's single-instance PID guard lets
it through rather than refusing it.

    source venv/bin/activate
    python tools/check_restart.py

Two things it does NOT cover, both deliberate:

  - The in-process trigger. `on_wedge()` -> `attempt()` runs on the app's own
    thread and quits via AppHelper.callAfter(rumps.quit_application); this
    script is a separate process, so it quits the app with an AppleScript
    quit instead. Same terminate path, different caller.
  - A real CoreAudio wedge, which cannot be provoked on demand. `is_wedged`
    is forced True here.

The ledger is pointed at a scratch file so a check does not spend one of the
three real restarts the cap allows.
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

from src.app_restart import AppRestarter  # noqa: E402
from src.restart_ledger import RestartLedger  # noqa: E402
from src.wedge_recovery import WedgeRecovery  # noqa: E402

BUNDLE = Path("/Applications/VibePaste.app")
EXECUTABLE = str(BUNDLE / "Contents/MacOS/VibePaste")
QUIT_TIMEOUT = 20.0
RELAUNCH_TIMEOUT = 45.0


def running_pid():
    """The live bundle process, by executable path rather than PID file.

    The launcher's `finally:` never runs under NSApp.terminate_, so the PID
    file outlives the process it names and cannot answer this.
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid=,command="],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if command.startswith(EXECUTABLE):
            return int(pid)
    return None


def wait_until(predicate, timeout, poll=0.3):
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while time.monotonic() < deadline:
        if predicate():
            return time.monotonic() - started
        time.sleep(poll)
    return None


def quit_the_app():
    """Quit the way the Quit menu item does, so before_quit still fires.

    `kill` does not stop it at all — the AppKit run loop parks the main
    thread — and `kill -9` would orphan the whisper-server.
    """
    subprocess.run(["osascript", "-e", 'quit app "VibePaste"'], timeout=20)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="    %(levelname)s %(name)s: %(message)s")

    if not BUNDLE.exists():
        print(f"❌ {BUNDLE} is not installed.")
        return 1

    before = running_pid()
    if before is None:
        print("❌ VibePaste is not running. Start it, then re-run this.")
        return 1
    print(f"\nRunning instance: pid {before}")

    scratch = Path(tempfile.mkdtemp()) / "restarts.json"
    recovery = WedgeRecovery(
        ledger=RestartLedger(path=scratch),
        # The bundle is passed explicitly: this script is not running from
        # it, so resolve_bundle_path() correctly returns None here. The app
        # itself logs what it resolved — see "DIAG relaunch target".
        restarter=AppRestarter(BUNDLE, pid=before),
        pending=lambda: 0,
        is_wedged=lambda: True,      # cannot wedge CoreAudio on demand
        notify=lambda title, message: print(f"  🔔 {message}"),
        quit_app=quit_the_app,
    )

    print("\n[1/3] Arming the relaunch and quitting")
    if not recovery.attempt():
        print("  ❌ attempt() declined to restart — nothing was quit.")
        return 1

    print("\n[2/3] Waiting for the old process to go")
    took = wait_until(lambda: running_pid() is None, QUIT_TIMEOUT)
    if took is None:
        print(f"  ❌ pid {before} is still alive — it never quit.")
        return 1
    print(f"  ✅ gone after {took:.1f}s")

    print("\n[3/3] Waiting for the helper to bring it back")
    took = wait_until(lambda: running_pid() is not None, RELAUNCH_TIMEOUT)
    if took is None:
        print("  ❌ Never came back. The app is DOWN — relaunch it by hand.")
        return 1

    after = running_pid()
    print(f"  ✅ back after {took:.1f}s as pid {after}")
    if after == before:
        print("  ❌ Same pid — that is the old process, not a restart.")
        return 1

    print("\n✅ The app quit itself and a fresh process came back.")
    print("   Check the log for 'DIAG relaunch target' in the new instance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
