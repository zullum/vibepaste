"""Swallow the hotkey combination before it reaches the focused app.

Option+Space types a non-breaking space (U+00A0). Suppressing the event is
safer than deleting the character afterwards: a synthetic Backspace would
fire even when nothing was inserted, and in some apps Backspace navigates
back or deletes a selection.

macOS-specific — pynput exposes this through its `darwin_intercept` hook.
"""

import logging

logger = logging.getLogger(__name__)

try:
    import Quartz

    KEYCODE_FIELD = Quartz.kCGKeyboardEventKeycode
    ALT_FLAG_MASK = Quartz.kCGEventFlagMaskAlternate
    SUPPRESSION_AVAILABLE = True
except ImportError:  # not macOS, or PyObjC missing
    Quartz = None
    KEYCODE_FIELD = ALT_FLAG_MASK = None
    SUPPRESSION_AVAILABLE = False


def virtual_keycode(key):
    """macOS virtual keycode for a pynput key, or None if it has none."""
    value = getattr(key, "value", key)
    return getattr(value, "vk", None)


class HotkeySuppressor:
    """Decides which key events to swallow system-wide."""

    def __init__(self):
        self._keycodes = set()

    @property
    def available(self):
        return SUPPRESSION_AVAILABLE

    def suppress_key(self, key):
        """Suppress this key whenever Option is held alongside it."""
        keycode = virtual_keycode(key)
        if keycode is not None:
            self._keycodes.add(keycode)

    def intercept(self, event_type, event):
        """pynput darwin_intercept hook.

        Returning the event passes it through; returning None suppresses it
        system-wide. Only a registered key *with Option held* is suppressed,
        so ordinary typing is untouched. Any error passes the event through
        rather than risk eating the user's keystrokes.
        """
        try:
            keycode = Quartz.CGEventGetIntegerValueField(event, KEYCODE_FIELD)
            flags = Quartz.CGEventGetFlags(event)
            if keycode in self._keycodes and (flags & ALT_FLAG_MASK):
                return None
        except Exception as e:
            logger.error(f"Intercept error: {e}")
        return event
