"""Global hotkey listener.

The hotkey combination is swallowed before it reaches the focused app.
Option+Space would otherwise type a non-breaking space (U+00A0) into
whatever you were about to paste into. Suppressing the event is safer than
deleting the character afterwards: a synthetic Backspace would fire even
when nothing was inserted, and in some apps Backspace navigates back or
deletes a selection. Only Space *with Option held* is suppressed, so
ordinary typing is untouched.

Two more rules make this reliable on macOS:

1. The pynput callbacks run inside a CoreGraphics event tap. If a callback
   is slow, macOS disables the tap and key *release* events start going
   missing, which leaves the tracked key state permanently wrong. So the
   callbacks here only touch an in-memory dict and hand work to a queue —
   no disk I/O, no subprocesses, no audio devices.
2. Held keys are dropped after a while, so a release that never arrived
   cannot wedge the listener forever.
3. Nothing here writes to the log from inside the callback. Logging is file
   I/O, and file I/O in the callback is what makes macOS disable the tap in
   the first place — see src/event_tap.py. Diagnostics are handed to the
   dispatch worker and written there.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

from pynput import keyboard

from src.event_tap import (
    RecoverableListener, TapWatchdog, enable_tap, tap_is_enabled,
)
from src.hotkey_dispatch import HotkeyDispatcher
from src.hotkey_suppression import HotkeySuppressor

# A key held longer than this almost certainly had its release event dropped.
STUCK_KEY_SECONDS = 30.0

# Ceiling on how long a bare key may be swallowed. Comfortably above the
# recording hard limit (120s), so it never cuts a real recording short, but
# short enough that a stop event going missing costs the user the space bar
# for a couple of minutes rather than until they quit the app.
BARE_SUPPRESS_SECONDS = 150.0

MODIFIER_KEYS = frozenset({
    keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
    keyboard.Key.alt_gr, keyboard.Key.cmd, keyboard.Key.cmd_l,
    keyboard.Key.cmd_r, keyboard.Key.ctrl, keyboard.Key.ctrl_l,
    keyboard.Key.ctrl_r, keyboard.Key.shift, keyboard.Key.shift_l,
    keyboard.Key.shift_r,
})


class _StuckKey:
    """A key whose release was missed, to be logged off the tap thread.

    It exists only so the warning is not written from inside the callback:
    the log is a file, and file I/O there is what gets the tap killed.
    """

    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key


def _key_matches(pressed, expected):
    """True if a pynput key event matches a registered key.

    `expected` is either a keyboard.Key member or a single character.
    """
    if pressed == expected:
        return True
    char = getattr(pressed, "char", None)
    return char is not None and char == expected


class KeyboardListener:
    """Listens for modifier+key toggle hotkeys and dispatches them off-thread."""

    def __init__(self, stuck_key_seconds=STUCK_KEY_SECONDS,
                 suppress_hotkeys=True):
        self.stuck_key_seconds = stuck_key_seconds
        self.watchdog = TapWatchdog(self)
        # The suppressor sees macOS disable the tap before anything else
        # does; all it may do about it is wake the watchdog.
        self.suppressor = HotkeySuppressor(
            on_tap_disabled=self.watchdog.request_recovery
        )
        self.suppress_hotkeys = suppress_hotkeys and self.suppressor.available
        self._intercepting = False
        self._listener = None
        self._toggles = {}
        self._bare = {}            # name -> bare (unmodified) key config
        self._down = {}            # key -> monotonic time it went down
        self._armed = {}           # toggle name -> fired, awaiting release
        self._lock = threading.Lock()
        self.dispatcher = HotkeyDispatcher(resolve=self._resolve)
        self._stopped = False

    def register_toggle(self, name, modifier, key, callback):
        """Register a modifier+key combination.

        The callback fires once per physical press of `key` while `modifier`
        is held, and is invoked on a worker thread — never inside the tap.
        """
        self._toggles[name] = {
            "modifier": modifier,
            "key": key,
            "callback": callback,
        }
        self.suppressor.suppress_key(key)
        logger.info(f"Registered hotkey: {name} ({modifier}+{key})")

    def register_bare_key(self, name, key, callback):
        """Register a key that fires when pressed with no modifier held.

        Starts disabled; only listened for while it means something.

        While enabled the key is also swallowed, so the Space that stops a
        recording is not typed into the field the transcript is about to be
        pasted into. That suppression carries its own deadline rather than
        relying on being switched off — see suppress_bare_key.
        """
        self._bare[name] = {"key": key, "callback": callback, "enabled": False}
        logger.info(f"Registered bare key: {name} ({key})")

    def enable_bare_key(self, name, enabled):
        config = self._bare.get(name)
        if config is None:
            logger.warning(f"No bare key registered as '{name}'")
            return
        config["enabled"] = enabled
        self.suppressor.suppress_bare_key(
            config["key"], BARE_SUPPRESS_SECONDS if enabled else None
        )
        with self._lock:
            self._armed.pop(name, None)
        logger.info(f"Bare key '{name}' {'enabled' if enabled else 'disabled'}")

    def start(self):
        """Begin listening. Idempotent.

        Suppressing the hotkey needs an active (non listen-only) event tap,
        which requires Accessibility permission. If that tap can't be
        created we fall back to a plain listener: the hotkey still works,
        it just leaves a stray space in the focused field.
        """
        if self._listener is not None:
            logger.warning("Listener already started")
            return

        self._stopped = False
        self.dispatcher.start()
        self._start_tap()
        # Started even if the tap could not be created: a tap that failed at
        # launch is exactly the case a later restart can still rescue.
        self.watchdog.start()

    def _start_tap(self):
        """Create the event tap. True if something is listening afterwards."""
        if self.suppress_hotkeys and self._try_start(intercept=True):
            self._intercepting = True
            logger.info("Keyboard listener started (hotkey suppression on)")
            return True
        if self.suppress_hotkeys:
            logger.warning(
                "Could not create an intercepting event tap — the hotkey "
                "will type a space. Grant Accessibility permission to fix."
            )
        if self._try_start(intercept=False):
            logger.info("Keyboard listener started")
            return True
        logger.error("Keyboard listener failed to start")
        return False

    def _try_start(self, intercept):
        options = {}
        if intercept:
            options["darwin_intercept"] = self.suppressor.intercept
        try:
            listener = RecoverableListener(
                on_press=self._on_press, on_release=self._on_release,
                **options,
            )
            listener.start()
            listener.wait()
        except Exception as e:
            logger.warning(f"Listener start failed (intercept={intercept}): {e}")
            return False
        if not listener.is_alive():
            return False
        if getattr(listener, "event_tap", None) is None:
            # pynput's private hook stopped handing us the tap. Recovery
            # still works, it just has to build a new listener rather than
            # switch the existing tap back on.
            logger.warning(
                "Event tap handle unavailable — recovery will restart the "
                "listener instead of re-enabling its tap."
            )
        self._listener = listener
        return True

    def stop(self):
        """Stop listening and shut the dispatch worker down."""
        self._stopped = True
        self.watchdog.stop()
        self._stop_listener()
        self.dispatcher.stop()
        self.forget_key_state()
        logger.info("Keyboard listener stopped")

    def _stop_listener(self):
        if self._listener is None:
            return
        try:
            self._listener.stop()
        except Exception as e:
            logger.warning(f"Stopping the listener failed: {e}")
        self._listener = None
        self._intercepting = False

    def is_running(self):
        return self._listener is not None and self._listener.is_alive()

    # -- what the watchdog drives --------------------------------------

    def is_healthy(self):
        """True if the tap is not just alive but still delivering events.

        `is_running()` reports only that the thread lives, and it keeps
        saying True after macOS switches the tap off — which is precisely
        why the dead hotkey looked healthy right up to the restart.
        """
        if not self.is_running():
            return False
        enabled = tap_is_enabled(getattr(self._listener, "event_tap", None))
        return True if enabled is None else enabled

    def revive(self):
        """Switch the existing tap back on. False if there is none to switch."""
        return enable_tap(getattr(self._listener, "event_tap", None))

    def restart(self):
        """Throw the tap away and build a fresh one.

        Refuses once stopped: the watchdog can already be inside a check
        when shutdown begins, and a tap created after that would keep its
        thread running with nothing left to stop it.
        """
        if self._stopped:
            return False
        self._stop_listener()
        return self._start_tap()

    def forget_key_state(self):
        """Discard every key we think is held.

        Releases go missing while the tap is deaf, so after a recovery a
        stale Option can make a plain Space look like the hotkey, and it
        also blocks the bare stop key. Nothing observed while deaf is worth
        keeping — and waiting for stuck-key expiry means 30 seconds of that
        behaviour at the exact moment the user is retrying the hotkey.
        """
        with self._lock:
            self._down.clear()
            self._armed.clear()

    def hotkey_typed_a_character(self, name):
        """True if firing this hotkey left a stray character in the field.

        Two ways that happens. Without an intercepting tap nothing is ever
        swallowed, so the key always lands. With one, it still lands if the
        key arrived before its modifier — macOS delivers Space up to 38ms
        ahead of Option, and at that instant nothing says Option is coming.

        Consumes the signal, so the character is only ever undone once.
        """
        config = self._toggles.get(name)
        if config is None:
            return False
        if not self._intercepting:
            return True
        return self.suppressor.was_typed(config["key"])

    # -- tap callbacks: keep these cheap ------------------------------

    def _on_press(self, key):
        """Match against every held key, not just the one that arrived.

        macOS does not deliver ⌥+Space in the order it is typed: the tap
        reports Space first and the Option modifier about 25ms later. Firing
        only when the *just-pressed* key completes the combination therefore
        never matches, and the hotkey goes silently dead. Re-checking the
        whole held set on each press makes the order irrelevant.
        """
        try:
            with self._lock:
                now = time.monotonic()
                dropped = self._drop_stuck_keys(now)
                # setdefault, not assignment: a held key keeps its original
                # timestamp so stuck-key detection still measures the hold.
                self._down.setdefault(key, now)

                triggered = []
                for name, cfg in self._toggles.items():
                    if self._armed.get(name):
                        continue  # already fired; needs a release first
                    if cfg["modifier"] not in self._down:
                        continue
                    if not any(_key_matches(k, cfg["key"]) for k in self._down):
                        continue
                    self._armed[name] = True
                    triggered.append(name)

                if not (MODIFIER_KEYS & self._down.keys()):
                    for name, cfg in self._bare.items():
                        if not cfg["enabled"] or self._armed.get(name):
                            continue
                        if not _key_matches(key, cfg["key"]):
                            continue
                        self._armed[name] = True
                        triggered.append(name)

            for key in dropped:
                self.dispatcher.submit(_StuckKey(key))
            for name in triggered:
                self.dispatcher.submit(name)
        except Exception as e:  # never let an exception kill the tap
            logger.error(f"Error handling key press: {e}")

    def _on_release(self, key):
        try:
            with self._lock:
                self._down.pop(key, None)
                self._disarm(key)
        except Exception as e:
            logger.error(f"Error handling key release: {e}")

    def _disarm(self, key):
        """Let any hotkey this key belongs to fire again. Caller holds lock."""
        for name, cfg in self._toggles.items():
            if key == cfg["modifier"] or _key_matches(key, cfg["key"]):
                self._armed.pop(name, None)
        for name, cfg in self._bare.items():
            if _key_matches(key, cfg["key"]):
                self._armed.pop(name, None)

    def _drop_stuck_keys(self, now):
        """Forget keys held implausibly long — their release was missed.

        Returns them for the worker to log; writing that warning here would
        put file I/O in the tap callback.
        """
        stale = [
            key for key, pressed_at in self._down.items()
            if now - pressed_at > self.stuck_key_seconds
        ]
        for key in stale:
            del self._down[key]
            # The matching release never arrived, so re-arm here instead;
            # otherwise the toggle stays latched and can never fire again.
            self._disarm(key)
        return stale

    # -- worker --------------------------------------------------------

    def _resolve(self, item):
        """What the dispatcher should run for a queued item, if anything."""
        if isinstance(item, _StuckKey):
            # Logged here rather than in the tap callback: the log is a file,
            # and file I/O in the callback is what gets the tap disabled.
            return lambda _item: logger.warning(
                f"Dropped stuck key: {item.key}"
            )
        config = self._toggles.get(item) or self._bare.get(item)
        if config is None:
            return None
        return config["callback"]
