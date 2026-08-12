"""Keep the CoreGraphics event tap alive.

macOS switches an event tap off when its callback overruns the system's
budget (`kCGEventTapDisabledByTimeout`), and the tap stays off until someone
calls `CGEventTapEnable` again. pynput calls that exactly once, at startup,
and has no handling for the notification macOS delivers — so from that
moment on not one key event is seen again.

Nothing about the failure looks like a failure. The run loop keeps spinning,
the thread stays alive, `is_alive()` and therefore `is_running()` keep
reporting True, and the diagnostic line keeps printing `running=True` about a
tap that is stone deaf. The only cure used to be restarting the app.

Two mechanisms bring it back, deliberately different from each other so one
wedged thing cannot produce the same failure twice:

1. macOS tells us. The notification reaches our `darwin_intercept` hook, and
   `request_recovery()` wakes the watchdog — microseconds on the tap thread.
2. The watchdog also polls, because mechanism 1 is blind on the fallback
   (non-intercepting) tap, and cannot notice the listener thread dying.

The recovery itself never runs on the tap thread. Restarting a listener from
inside its own callback would tear down the run loop that is calling us.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

from pynput import keyboard

try:
    import Quartz

    # macOS delivers these to the tap callback regardless of the event mask.
    TAP_DISABLED_EVENTS = frozenset({
        Quartz.kCGEventTapDisabledByTimeout,
        Quartz.kCGEventTapDisabledByUserInput,
    })
except ImportError:  # not macOS, or PyObjC missing
    Quartz = None
    TAP_DISABLED_EVENTS = frozenset()

# Only the backstop: the notification path recovers in microseconds. Short
# enough that a dead hotkey heals before you would reach for a restart.
WATCHDOG_INTERVAL_SECONDS = 5.0

# How long to leave a tap alone after recovery failed to bring it back.
# Without it, a tap that cannot be revived at all — permission revoked
# mid-session — is rebuilt every few seconds for as long as the app runs.
FAILED_RECOVERY_BACKOFF_SECONDS = 60.0


class RecoverableListener(keyboard.Listener):
    """A pynput listener that keeps hold of the tap pynput throws away.

    `_create_event_tap` is the only place the handle exists — pynput leaves
    it in a local variable, so there is otherwise nothing to re-enable.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_tap = None

    def _create_event_tap(self):
        tap = super()._create_event_tap()
        self.event_tap = tap
        return tap


def tap_is_enabled(tap):
    """True/False if the tap is delivering events, None if we cannot tell."""
    if tap is None or Quartz is None:
        return None
    try:
        return bool(Quartz.CGEventTapIsEnabled(tap))
    except Exception as e:
        logger.error(f"Could not read event tap state: {e}")
        return None


def enable_tap(tap):
    """Switch a disabled tap back on. True if the call went through."""
    if tap is None or Quartz is None:
        return False
    try:
        Quartz.CGEventTapEnable(tap, True)
        return True
    except Exception as e:
        logger.error(f"Could not re-enable the event tap: {e}")
        return False


class TapWatchdog:
    """Notices a deaf event tap and escalates until it hears again.

    The controller supplies four things: `is_healthy()`, `revive()`,
    `restart()` and `forget_key_state()`. Keeping the interface that small
    is what lets this be tested without a real tap, which would need Input
    Monitoring and real keystrokes.
    """

    def __init__(self, controller, interval=WATCHDOG_INTERVAL_SECONDS,
                 backoff=FAILED_RECOVERY_BACKOFF_SECONDS):
        self.controller = controller
        self.interval = interval
        self.backoff = backoff
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        # Recovery restores a tap that once worked; it cannot conjure a
        # permission that was never granted. Rebuilding the listener every
        # few seconds because Input Monitoring is off achieves nothing but
        # noise, so wait until we have seen the tap deliver at least once.
        self._seen_healthy = False
        self._retry_after = 0.0

    def start(self):
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._loop, name="tap-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stopping.set()
        self._wake.set()
        self._thread = None

    def is_running(self):
        """Whether anything is still watching.

        Worth reporting: a watchdog that quietly died leaves exactly the
        silent, healthy-looking failure it was added to end.
        """
        return self._thread is not None and self._thread.is_alive()

    def request_recovery(self):
        """Called from the tap callback — must stay this cheap.

        No Quartz calls, no logging, no lock: just two flag flips that wake
        the thread allowed to do the slow work. macOS only sends this
        notification about a tap that *was* delivering, which is proof enough
        that there is something here to recover.
        """
        self._seen_healthy = True
        self._wake.set()

    def check_now(self):
        """Run one health check and recover if needed. Returns True if healthy."""
        if self.controller.is_healthy():
            self._seen_healthy = True
            self._retry_after = 0.0
            return True
        if not self._seen_healthy or time.monotonic() < self._retry_after:
            return False
        logger.warning(
            "Event tap stopped delivering events — macOS disabled it; "
            "recovering (the hotkey used to stay dead until a restart)"
        )
        if self.controller.revive() and self.controller.is_healthy():
            self._recovered("re-enabled")
            return True
        # Re-enabling did not take, or there was no handle to re-enable.
        # A fresh tap is a genuinely different mechanism, not a retry. Its
        # health is checked too: a listener that started is not the same
        # thing as a tap that delivers, and claiming otherwise put "hotkeys
        # are live again" in the log while they were still dead.
        if self.controller.restart() and self.controller.is_healthy():
            self._recovered("restarted")
            return True
        self._retry_after = time.monotonic() + self.backoff
        logger.error(
            "Could not recover the event tap — hotkeys stay dead. Check "
            "Input Monitoring and Accessibility for VibePaste."
        )
        return False

    def _recovered(self, how):
        self.controller.forget_key_state()
        self._retry_after = 0.0
        logger.warning(f"Event tap {how} — hotkeys are live again")

    def _loop(self):
        while not self._stopping.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stopping.is_set():
                return
            try:
                self.check_now()
            except Exception as e:  # a dead watchdog is a dead hotkey
                logger.error(f"Tap watchdog error: {e}", exc_info=True)
