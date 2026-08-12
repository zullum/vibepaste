"""Swallow the hotkey combination before it reaches the focused app.

Option+Space types a non-breaking space (U+00A0). Suppressing the event is
safer than deleting the character afterwards: a *blind* Backspace would fire
even when nothing was inserted, and in some apps Backspace navigates back or
deletes a selection.

Suppression cannot catch every case, though, so this module also records
which of its keys got through — see `was_typed`. That turns the deletion
from a guess into an observation, and the paste path uses it to clean up the
one space suppression could not stop.

macOS-specific — pynput exposes this through its `darwin_intercept` hook.
"""

import logging
import time

logger = logging.getLogger(__name__)

from src.event_tap import TAP_DISABLED_EVENTS

try:
    import Quartz

    KEYCODE_FIELD = Quartz.kCGKeyboardEventKeycode
    KEY_DOWN = Quartz.kCGEventKeyDown
    ALT_FLAG_MASK = Quartz.kCGEventFlagMaskAlternate
    ANY_MODIFIER_MASK = (
        Quartz.kCGEventFlagMaskAlternate
        | Quartz.kCGEventFlagMaskCommand
        | Quartz.kCGEventFlagMaskControl
        | Quartz.kCGEventFlagMaskShift
    )
    SUPPRESSION_AVAILABLE = True
except ImportError:  # not macOS, or PyObjC missing
    Quartz = None
    KEYCODE_FIELD = KEY_DOWN = ALT_FLAG_MASK = ANY_MODIFIER_MASK = None
    SUPPRESSION_AVAILABLE = False

# How recently a key must have slipped through to count as this hotkey's
# stray character. The observed gap between the Space keydown and the Option
# modifier is ~38ms; a second is generous enough to survive a busy dispatch
# thread while staying far too short to pick up ordinary earlier typing.
TYPED_WINDOW_SECONDS = 1.0


def virtual_keycode(key):
    """macOS virtual keycode for a pynput key, or None if it has none."""
    value = getattr(key, "value", key)
    return getattr(value, "vk", None)


class HotkeySuppressor:
    """Decides which key events to swallow system-wide."""

    def __init__(self, on_tap_disabled=None):
        """
        Args:
            on_tap_disabled: called when macOS reports it has switched the
                tap off. Must be cheap — it runs inside the tap callback.
        """
        self._keycodes = set()
        self._bare_until = {}   # keycode -> monotonic time to stop swallowing
        self._typed_at = {}     # keycode -> when it last reached the app
        self._on_tap_disabled = on_tap_disabled

    @property
    def available(self):
        return SUPPRESSION_AVAILABLE

    def suppress_key(self, key):
        """Suppress this key whenever Option is held alongside it."""
        keycode = virtual_keycode(key)
        if keycode is not None:
            self._keycodes.add(keycode)

    def suppress_bare_key(self, key, for_seconds):
        """Swallow this key pressed alone, for at most `for_seconds`.

        Used so the Space that stops a recording is not also typed into the
        field the transcript is about to be pasted into.

        The deadline is the point of this design. An earlier version used a
        flag that something had to switch back off, and when a stop event
        went missing the space bar stayed dead everywhere until the app was
        killed. Expiry makes that failure temporary and bounded: worst case
        the key is swallowed until the recording's own hard limit passes.

        Pass for_seconds=None to stop suppressing immediately.
        """
        keycode = virtual_keycode(key)
        if keycode is None:
            return
        if for_seconds is None:
            self._bare_until.pop(keycode, None)
        else:
            self._bare_until[keycode] = time.monotonic() + for_seconds

    def _swallow_bare(self, keycode, flags):
        """True if this key, pressed with no modifier, is being swallowed."""
        deadline = self._bare_until.get(keycode)
        if deadline is None or (flags & ANY_MODIFIER_MASK):
            return False
        if time.monotonic() >= deadline:
            del self._bare_until[keycode]   # lapsed; never swallow it again
            return False
        return True

    def intercept(self, event_type, event):
        """pynput darwin_intercept hook.

        Known limitation: starting a recording types a space if Space is
        pressed fractionally *before* Option. Measured on a real tap, the
        Space keydown can arrive 38ms before the Option modifier, and at
        that moment neither the event's flags nor the live HID state show
        Option at all -- so there is nothing to decide on, and the key must
        pass through. Holding Option first avoids it entirely. The stop key
        is unaffected, because suppression is already armed by then.

        Returning the event passes it through; returning None suppresses it
        system-wide. Only a registered key *with Option held*, or a bare key
        inside its suppression window, is swallowed — so ordinary typing is
        untouched. Any error passes the event through rather than risk
        eating the user's keystrokes.
        """
        if event_type in TAP_DISABLED_EVENTS:
            # macOS has switched the tap off and will deliver nothing more
            # until it is re-enabled. Its keycode and flags mean nothing, so
            # read neither — just wake the thread that can fix this.
            if self._on_tap_disabled is not None:
                self._on_tap_disabled()
            return event
        try:
            keycode = Quartz.CGEventGetIntegerValueField(event, KEYCODE_FIELD)
            flags = Quartz.CGEventGetFlags(event)
            if keycode in self._keycodes and (flags & ALT_FLAG_MASK):
                return None
            if self._swallow_bare(keycode, flags):
                return None
            if event_type == KEY_DOWN and keycode in self._keycodes:
                # Slipped past us — whatever has focus is about to receive
                # this character, and something has to undo it later.
                self._typed_at[keycode] = time.monotonic()
        except Exception as e:
            logger.error(f"Intercept error: {e}")
        return event

    def was_typed(self, key, within=TYPED_WINDOW_SECONDS):
        """True if this key recently reached the focused app un-swallowed.

        The one honest signal that a stray character was inserted. Anything
        else — assuming suppression failed, or always deleting — sends a
        Backspace when nothing was typed, and Backspace is not harmless: in
        some apps it navigates back or deletes the selection.

        Reading this consumes it, so a single stray space is never deleted
        twice.
        """
        keycode = virtual_keycode(key)
        stamp = self._typed_at.pop(keycode, None)
        return stamp is not None and time.monotonic() - stamp <= within
