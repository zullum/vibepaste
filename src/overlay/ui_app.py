"""The overlay UI process.

Runs as a long-lived child process because AppKit insists on owning the
main thread. The parent drives it with one-line commands on stdin:

    record <warn_seconds> <max_seconds>
    processing
    hide
    quit

Closing stdin also terminates it, so the overlay can never outlive its parent.
"""

import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from AppKit import (  # noqa: E402
    NSApplication, NSWindow, NSColor,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSFloatingWindowLevel, NSScreen, NSTimer,
)
from Foundation import NSMakeRect  # noqa: E402

from src.overlay.state import apply_command, emit, read_state, set_mode  # noqa: E402
from src.overlay.views import OverlayView, Ticker  # noqa: E402

WIDTH, HEIGHT = 150, 66
TOP_MARGIN = 35
FRAME_INTERVAL = 1.0 / 60.0


class WindowDriver:
    """Shows/hides the window as the mode changes; reports the hard time cap."""

    def __init__(self, window):
        self.window = window
        self.visible = False
        self.auto_stop_sent = False

    def tick(self):
        state = read_state()
        should_show = state["mode"] != "hidden"

        if should_show and not self.visible:
            self.window.orderFront_(None)
            self.visible = True
        elif not should_show and self.visible:
            self.window.orderOut_(None)
            self.visible = False

        if state["mode"] != "record":
            self.auto_stop_sent = False
            return

        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= state["max_seconds"] and not self.auto_stop_sent:
            # Tell the parent to stop — it owns the audio stream.
            self.auto_stop_sent = True
            emit("auto_stop")
            set_mode("processing")


def _stdin_loop():
    """Apply commands until the parent closes stdin or sends quit."""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            if not apply_command(line):
                break
        except (ValueError, IndexError):
            pass  # malformed command: ignore rather than die
    NSApplication.sharedApplication().performSelectorOnMainThread_withObject_waitUntilDone_(
        "terminate:", None, False
    )


def _build_window():
    screen = NSScreen.mainScreen()
    if screen is None:
        return None
    frame = screen.frame()
    x = (frame.size.width - WIDTH) / 2
    y = frame.size.height - HEIGHT - TOP_MARGIN

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, WIDTH, HEIGHT),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False,
    )
    window.setLevel_(NSFloatingWindowLevel + 100)
    window.setOpaque_(False)
    window.setBackgroundColor_(NSColor.clearColor())
    window.setIgnoresMouseEvents_(True)
    window.setCollectionBehavior_(1 << 0 | 1 << 3)  # all spaces, stationary
    return window


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # accessory: no dock icon

    window = _build_window()
    if window is None:
        return

    view = OverlayView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
    window.setContentView_(view)

    ticker = Ticker.alloc().initWithDriver_view_(WindowDriver(window), view)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        FRAME_INTERVAL, ticker, "tick:", None, True
    )

    threading.Thread(target=_stdin_loop, name="overlay-stdin",
                     daemon=True).start()
    app.run()


if __name__ == "__main__":
    main()
