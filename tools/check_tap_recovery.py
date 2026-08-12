"""Manual check: does a disabled event tap really come back?

Not part of the suite. A real tap needs Input Monitoring and real
keystrokes, which is the same reason test_keyboard.py sits outside it.

The unit tests prove the escalation logic against a fake. This proves the
part only macOS can answer: that CGEventTapEnable actually revives a tap the
system has switched off, and that key events flow again afterwards.

    source venv/bin/activate
    python tools/check_tap_recovery.py

What it does NOT cover: the kCGEventTapDisabledByTimeout notification, which
only macOS can send and cannot be provoked on demand. Disabling the tap by
hand leaves it in the same state, so the watchdog's poll is what recovers it
here — the slower of the two paths, and the one worth measuring.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

from pynput import keyboard

from src.event_tap import tap_is_enabled
from src.keyboard_listener import KeyboardListener

PROMPT_TIMEOUT = 30.0


def wait_for_hotkey(fired, what):
    print(f"\n  → Press ⌥(left)+Space now to {what} "
          f"({PROMPT_TIMEOUT:.0f}s)...", flush=True)
    if fired.wait(PROMPT_TIMEOUT):
        print("  ✅ hotkey fired")
        return True
    print("  ❌ nothing arrived — the tap is not delivering events")
    return False


def force_disable(listener):
    """Switch the tap off exactly as macOS does when a callback overruns."""
    import Quartz

    tap = getattr(listener._listener, "event_tap", None)
    if tap is None:
        print("❌ No tap handle — cannot simulate the failure.")
        return False
    Quartz.CGEventTapEnable(tap, False)
    return True


def wait_for_recovery(listener, timeout=20.0):
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while time.monotonic() < deadline:
        if listener.is_healthy():
            return time.monotonic() - started
        time.sleep(0.1)
    return None


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="    %(levelname)s %(name)s: %(message)s",
    )
    fired = threading.Event()

    listener = KeyboardListener()
    listener.register_toggle(
        "check", keyboard.Key.alt_l, keyboard.Key.space,
        callback=lambda name: fired.set(),
    )
    listener.start()

    if not listener.is_running():
        print("❌ Listener did not start. Grant Input Monitoring and retry.")
        return 1

    tap = getattr(listener._listener, "event_tap", None)
    print(f"\nTap created: intercepting={listener._intercepting} "
          f"handle={'yes' if tap is not None else 'no'} "
          f"enabled={tap_is_enabled(tap)}")
    if not listener._intercepting:
        print("  (fallback tap — recovery still applies; only the instant "
              "notification path is unavailable)")

    try:
        print("\n[1/3] Before the failure")
        if not wait_for_hotkey(fired, "confirm the hotkey works"):
            return 1
        fired.clear()

        print("\n[2/3] Disabling the tap the way macOS does")
        if not force_disable(listener):
            return 1
        time.sleep(0.2)
        print(f"  is_running() = {listener.is_running()}   "
              f"← stays True; this is the lie that hid the bug")
        print(f"  is_healthy() = {listener.is_healthy()}   ← the honest one")
        if listener.is_healthy():
            print("  ❌ The tap did not report as disabled — check failed.")
            return 1

        print("\n[3/3] Waiting for the watchdog")
        took = wait_for_recovery(listener)
        if took is None:
            print("  ❌ Never recovered.")
            return 1
        print(f"  ✅ Recovered after {took:.1f}s without a restart")

        if not wait_for_hotkey(fired, "confirm the hotkey works again"):
            return 1

        print("\n✅ A tap macOS disables comes back on its own.")
        return 0
    finally:
        listener.stop()


if __name__ == "__main__":
    sys.exit(main())
