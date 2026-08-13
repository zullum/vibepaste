"""Manual check: can the recovery really quit the app from its own thread?

This covers the one hop nothing else does. `WedgeRecovery` runs on a
background thread and ends by calling `menubar._quit_on_main_thread()`, but
that line executes nowhere else: unit tests cannot run it (it is a bare
AppKit call), and tools/check_restart.py deliberately quits with AppleScript
because it is a separate process. A real CoreAudio wedge would exercise it,
and a wedge cannot be provoked on demand.

What makes it worth checking rather than assuming: the relaunch helper is
armed *before* the quit. If `AppHelper.callAfter` did not marshal onto the
main thread — or if `NSApp.terminate_` from a background thread simply did
nothing — the app would arm a relaunch, record a restart, and then stay
exactly where it was with a dead microphone.

So this runs a throwaway rumps app, quits it from a background thread the
same way the recovery does, and checks two things: that the process really
terminated, and that `before_quit` still fired on the way out — the hook
that releases the whisper-server.

    source venv/bin/activate
    python tools/check_quit_from_thread.py

A second menu bar icon (⏳) appears for about two seconds. That is this
check, not VibePaste, and it takes itself down.
"""

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUIT_AFTER_SECONDS = 2.0
PATIENCE_SECONDS = 25.0


def run_child(marker):
    """The throwaway app: quit from a thread, exactly as the recovery does."""
    import rumps

    from src.menubar import _quit_on_main_thread

    app = rumps.App("QuitProbe", title="⏳")
    rumps.events.before_quit.register(
        lambda: marker.write_text("before_quit fired")
    )

    def trigger():
        time.sleep(QUIT_AFTER_SECONDS)
        print("  → quitting from thread "
              f"{threading.current_thread().name!r}", flush=True)
        _quit_on_main_thread()

    threading.Thread(target=trigger, name="wedge-recovery",
                     daemon=True).start()
    app.run()          # returns only if the quit never happened
    return 0


def main():
    marker = Path(tempfile.mkdtemp()) / "before_quit"
    print(f"\nStarting a throwaway menu bar app; it should quit itself "
          f"after {QUIT_AFTER_SECONDS:.0f}s.")

    started = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, __file__, "--child", str(marker)]
    )
    try:
        code = child.wait(timeout=PATIENCE_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        print("\n❌ It never quit. A wedge would arm a relaunch and then "
              "sit there — the app would not restart.")
        return 1

    took = time.monotonic() - started
    print(f"\n[1/2] Process exited after {took:.1f}s (code {code})")
    if code != 0:
        print("  ❌ Exited, but not cleanly.")
        return 1

    print("[2/2] before_quit hook")
    if not marker.exists():
        print("  ❌ Never fired — an auto-restart would orphan the "
              "whisper-server.")
        return 1
    print(f"  ✅ {marker.read_text()}")

    print("\n✅ A background thread can quit the app, and the hook that "
          "releases the whisper-server still runs.")
    return 0


if __name__ == "__main__":
    if "--child" in sys.argv:
        sys.exit(run_child(Path(sys.argv[-1])))
    sys.exit(main())
