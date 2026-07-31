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
"""

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

from pynput import keyboard

from src.hotkey_suppression import HotkeySuppressor

# A key held longer than this almost certainly had its release event dropped.
STUCK_KEY_SECONDS = 30.0
_SHUTDOWN = object()

MODIFIER_KEYS = frozenset({
    keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
    keyboard.Key.alt_gr, keyboard.Key.cmd, keyboard.Key.cmd_l,
    keyboard.Key.cmd_r, keyboard.Key.ctrl, keyboard.Key.ctrl_l,
    keyboard.Key.ctrl_r, keyboard.Key.shift, keyboard.Key.shift_l,
    keyboard.Key.shift_r,
})


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
        self.suppressor = HotkeySuppressor()
        self.suppress_hotkeys = suppress_hotkeys and self.suppressor.available
        self._listener = None
        self._toggles = {}
        self._bare = {}            # name -> bare (unmodified) key config
        self._down = {}            # key -> monotonic time it went down
        self._armed = {}           # toggle name -> fired, awaiting release
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = None

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

        This deliberately does *not* suppress the key. Swallowing the space
        bar system-wide means any failure to switch it back off leaves the
        user unable to type a space anywhere — which is exactly what
        happened when a stop event went missing. A stray space before the
        transcript is pasted is the far cheaper failure.
        """
        self._bare[name] = {"key": key, "callback": callback, "enabled": False}
        logger.info(f"Registered bare key: {name} ({key})")

    def enable_bare_key(self, name, enabled):
        config = self._bare.get(name)
        if config is None:
            logger.warning(f"No bare key registered as '{name}'")
            return
        config["enabled"] = enabled
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

        self._worker = threading.Thread(
            target=self._dispatch_loop, name="hotkey-dispatch", daemon=True
        )
        self._worker.start()

        if self.suppress_hotkeys and self._try_start(intercept=True):
            logger.info("Keyboard listener started (hotkey suppression on)")
            return
        if self.suppress_hotkeys:
            logger.warning(
                "Could not create an intercepting event tap — the hotkey "
                "will type a space. Grant Accessibility permission to fix."
            )
        if self._try_start(intercept=False):
            logger.info("Keyboard listener started")
        else:
            logger.error("Keyboard listener failed to start")

    def _try_start(self, intercept):
        options = {}
        if intercept:
            options["darwin_intercept"] = self.suppressor.intercept
        try:
            listener = keyboard.Listener(
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
        self._listener = listener
        return True

    def stop(self):
        """Stop listening and shut the dispatch worker down."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._worker is not None:
            self._queue.put(_SHUTDOWN)
            self._worker = None
        with self._lock:
            self._down.clear()
            self._armed.clear()
        logger.info("Keyboard listener stopped")

    def is_running(self):
        return self._listener is not None and self._listener.is_alive()

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
                self._drop_stuck_keys(now)
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

            for name in triggered:
                self._queue.put(name)
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
        """Forget keys held implausibly long — their release was missed."""
        stale = [
            key for key, pressed_at in self._down.items()
            if now - pressed_at > self.stuck_key_seconds
        ]
        for key in stale:
            del self._down[key]
            # The matching release never arrived, so re-arm here instead;
            # otherwise the toggle stays latched and can never fire again.
            self._disarm(key)
            logger.warning(f"Dropped stuck key: {key}")

    # -- worker --------------------------------------------------------

    def _dispatch_loop(self):
        while True:
            name = self._queue.get()
            if name is _SHUTDOWN:
                return
            config = self._toggles.get(name) or self._bare.get(name)
            if config is None or config["callback"] is None:
                continue
            try:
                config["callback"](name)
            except Exception as e:
                logger.error(f"Error in hotkey callback '{name}': {e}",
                             exc_info=True)
