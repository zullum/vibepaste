"""Clipboard and auto-paste.

The text always reaches the clipboard first, and that part *is* verified by
reading it back. The keystroke itself cannot be verified — no API tells us
whether the frontmost app accepted a Cmd+V — so instead we check the one
thing that reliably makes it fail silently: missing Accessibility
permission. Without it pyautogui raises nothing and simply does nothing.
"""

import logging
import subprocess
import time

import pyautogui
import pyperclip

logger = logging.getLogger(__name__)

CLIPBOARD_SETTLE_SECONDS = 0.12
PASTE_SETTLE_SECONDS = 0.1
BACKSPACE_SETTLE_SECONDS = 0.05
COPY_ATTEMPTS = 3

# The hotkey can leave one stray character. Anything above that is a bug in
# the caller, and deleting the user's text is worse than leaving a space.
MAX_STRAY_DELETIONS = 2

try:
    from ApplicationServices import AXIsProcessTrusted

    def accessibility_granted():
        """True if this process may synthesise keystrokes."""
        return bool(AXIsProcessTrusted())
except ImportError:  # not macOS, or PyObjC missing
    def accessibility_granted():
        return True


class PasteResult:
    """Outcome of a paste, so callers can report precisely what happened."""

    def __init__(self, copied, pasted, method=None, reason=None):
        self.copied = copied
        self.pasted = pasted
        self.method = method
        self.reason = reason

    def __repr__(self):
        return (f"PasteResult(copied={self.copied}, pasted={self.pasted}, "
                f"method={self.method!r}, reason={self.reason!r})")


class Paster:
    """Copies text to the clipboard and pastes it into the frontmost app."""

    def __init__(self, notify=True):
        self.notify = notify

    def paste_text(self, text, delete_first=0):
        """Copy `text` and paste it.

        Args:
            delete_first: how many characters the hotkey typed into the
                target field, to be backspaced away first. Only pass a
                count that something actually observed being typed — see
                HotkeySuppressor.was_typed. Backspace is not a harmless
                key: with nothing to delete it can navigate back or eat a
                selection instead.

        Returns:
            PasteResult. `copied` says whether the clipboard holds the text —
            if that's True the text is never lost, even when `pasted` is False.
        """
        if not text:
            return PasteResult(copied=False, pasted=False,
                               reason="empty text")

        copied = self.copy_only(text)
        if not copied:
            self._notify("Could not copy to clipboard", text)
            return PasteResult(copied=False, pasted=False,
                               reason="clipboard write failed")

        if not accessibility_granted():
            logger.error("Accessibility permission missing — cannot paste")
            self._notify(
                "Text copied. Grant Accessibility to auto-paste", text
            )
            return PasteResult(copied=True, pasted=False,
                               reason="accessibility not granted")

        time.sleep(CLIPBOARD_SETTLE_SECONDS)
        self._delete_stray_characters(delete_first)

        for method, send in (("pyautogui", self._paste_pyautogui),
                             ("osascript", self._paste_osascript)):
            try:
                send()
            except Exception as e:
                logger.warning(f"Paste via {method} failed: {e}")
                continue
            time.sleep(PASTE_SETTLE_SECONDS)
            logger.info(f"Pasted via {method}")
            return PasteResult(copied=True, pasted=True, method=method)

        logger.error("Every paste method failed")
        self._notify("Text copied — press Cmd+V to paste", text)
        return PasteResult(copied=True, pasted=False,
                           reason="all paste methods failed")

    def copy_only(self, text):
        """Copy to the clipboard and read it back to confirm it took."""
        for attempt in range(1, COPY_ATTEMPTS + 1):
            try:
                pyperclip.copy(text)
                time.sleep(0.02)
                if pyperclip.paste() == text:
                    logger.info(f"Copied {len(text)} chars to clipboard")
                    return True
                logger.warning(f"Clipboard readback mismatch (attempt {attempt})")
            except Exception as e:
                logger.warning(f"Clipboard copy failed (attempt {attempt}): {e}")
            time.sleep(0.1 * attempt)
        return False

    def _delete_stray_characters(self, count):
        """Backspace over characters the hotkey typed into the field.

        Sent here, after the Accessibility check and a breath before the
        paste, so it can only ever land in the same place the transcript
        does. Capped: however confused the caller gets, this never turns
        into a key repeat chewing through the user's text. A failure is
        logged and ignored — a leftover space is a blemish, but losing the
        transcript over it would not be.
        """
        count = min(max(int(count), 0), MAX_STRAY_DELETIONS)
        if not count:
            return
        try:
            for _ in range(count):
                pyautogui.press("backspace")
            time.sleep(BACKSPACE_SETTLE_SECONDS)
            logger.info(f"Deleted {count} stray character(s) before pasting")
        except Exception as e:
            logger.warning(f"Could not delete stray characters: {e}")

    # -- paste mechanisms ----------------------------------------------

    @staticmethod
    def _paste_pyautogui():
        pyautogui.hotkey("command", "v")

    @staticmethod
    def _paste_osascript():
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" '
             'using command down'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[:200])

    # -- notifications --------------------------------------------------

    def _notify(self, subtitle, text):
        if not self.notify:
            return
        preview = text.replace('"', "'").replace("\n", " ").replace("\r", "")
        preview = preview[:60] + ("…" if len(text) > 60 else "")
        subtitle = subtitle.replace('"', "'")
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{preview}" with title "VibePaste" '
                 f'subtitle "{subtitle}"'],
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Could not show notification: {e}")
