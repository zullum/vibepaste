"""Tests that only the hotkey combination is swallowed.

Option+Space types a non-breaking space; suppressing it keeps that character
out of whatever field the transcript is about to be pasted into. Plain Space
must still reach the app, or normal typing breaks.
"""

import pytest
from pynput import keyboard

from src.hotkey_suppression import (
    ALT_FLAG_MASK, SUPPRESSION_AVAILABLE, HotkeySuppressor, virtual_keycode,
)
from src.keyboard_listener import KeyboardListener

pytestmark = pytest.mark.skipif(
    not SUPPRESSION_AVAILABLE, reason="Quartz not available"
)

SPACE_VK = 49
C_KEY_VK = 8
SHIFT_FLAG_MASK = 0x00020000
SENTINEL = object()


@pytest.fixture
def suppressor():
    suppressor = HotkeySuppressor()
    suppressor.suppress_key(keyboard.Key.space)
    return suppressor


def intercept(suppressor, monkeypatch, keycode, flags):
    """Run the intercept against a fake CGEvent."""
    import src.hotkey_suppression as module

    monkeypatch.setattr(
        module.Quartz, "CGEventGetIntegerValueField",
        lambda event, field: keycode,
    )
    monkeypatch.setattr(module.Quartz, "CGEventGetFlags", lambda event: flags)
    return suppressor.intercept(10, SENTINEL)


def test_space_has_the_expected_virtual_keycode():
    assert virtual_keycode(keyboard.Key.space) == SPACE_VK


def test_option_space_is_suppressed(suppressor, monkeypatch):
    assert intercept(suppressor, monkeypatch, SPACE_VK, ALT_FLAG_MASK) is None


def test_plain_space_passes_through(suppressor, monkeypatch):
    """Typing a normal space must never be swallowed."""
    assert intercept(suppressor, monkeypatch, SPACE_VK, 0) is SENTINEL


def test_option_with_another_key_passes_through(suppressor, monkeypatch):
    assert intercept(
        suppressor, monkeypatch, C_KEY_VK, ALT_FLAG_MASK
    ) is SENTINEL


def test_shift_space_passes_through(suppressor, monkeypatch):
    assert intercept(
        suppressor, monkeypatch, SPACE_VK, SHIFT_FLAG_MASK
    ) is SENTINEL


def test_bare_space_is_suppressed_while_a_recording_runs(suppressor, monkeypatch):
    """The Space that stops a recording must not also type a space."""
    suppressor.suppress_bare_key(keyboard.Key.space, for_seconds=30)

    assert intercept(suppressor, monkeypatch, SPACE_VK, 0) is None


def test_bare_suppression_stops_when_the_recording_does(suppressor, monkeypatch):
    suppressor.suppress_bare_key(keyboard.Key.space, for_seconds=30)
    suppressor.suppress_bare_key(keyboard.Key.space, for_seconds=None)

    assert intercept(suppressor, monkeypatch, SPACE_VK, 0) is SENTINEL


def test_bare_suppression_expires_on_its_own(suppressor, monkeypatch):
    """The safety net: a stop event that never arrives must not cost the
    user the space bar. Suppression lapses even if nothing switches it off."""
    suppressor.suppress_bare_key(keyboard.Key.space, for_seconds=0.05)
    import time
    time.sleep(0.1)

    assert intercept(suppressor, monkeypatch, SPACE_VK, 0) is SENTINEL


def test_an_expired_bare_suppression_is_forgotten(suppressor, monkeypatch):
    suppressor.suppress_bare_key(keyboard.Key.space, for_seconds=0.05)
    import time
    time.sleep(0.1)
    intercept(suppressor, monkeypatch, SPACE_VK, 0)

    assert SPACE_VK not in suppressor._bare_until


def test_bare_suppression_ignores_space_with_a_modifier(suppressor, monkeypatch):
    """⌥+Space is the start combo, handled by the other rule."""
    suppressor.suppress_bare_key(keyboard.Key.space, for_seconds=30)

    assert intercept(
        suppressor, monkeypatch, SPACE_VK, SHIFT_FLAG_MASK
    ) is SENTINEL


def test_unregistered_key_is_never_suppressed(monkeypatch):
    empty = HotkeySuppressor()
    assert intercept(empty, monkeypatch, SPACE_VK, ALT_FLAG_MASK) is SENTINEL


def test_intercept_failure_lets_the_event_through(suppressor, monkeypatch):
    """A broken intercept must never eat the user's keystrokes."""
    import src.hotkey_suppression as module

    def explode(*args, **kwargs):
        raise RuntimeError("Quartz blew up")

    monkeypatch.setattr(module.Quartz, "CGEventGetIntegerValueField", explode)
    assert suppressor.intercept(10, SENTINEL) is SENTINEL


def test_registering_a_hotkey_registers_it_for_suppression():
    listener = KeyboardListener()
    listener.register_toggle(
        "english", keyboard.Key.alt_l, keyboard.Key.space, callback=None
    )

    assert SPACE_VK in listener.suppressor._keycodes


def test_suppression_can_be_disabled():
    assert KeyboardListener(suppress_hotkeys=False).suppress_hotkeys is False


# -- knowing whether a stray character was actually typed ---------------

KEY_UP = 11


def test_a_swallowed_space_was_not_typed(suppressor, monkeypatch):
    intercept(suppressor, monkeypatch, SPACE_VK, ALT_FLAG_MASK)

    assert suppressor.was_typed(keyboard.Key.space) is False


def test_a_space_that_slipped_through_was_typed(suppressor, monkeypatch):
    """The race: Space arrives before the Option flag, so it reaches the app."""
    intercept(suppressor, monkeypatch, SPACE_VK, 0)

    assert suppressor.was_typed(keyboard.Key.space) is True


def test_nothing_typed_reports_false(suppressor):
    assert suppressor.was_typed(keyboard.Key.space) is False


def test_reading_consumes_the_signal(suppressor, monkeypatch):
    """One stray space must never be backspaced twice."""
    intercept(suppressor, monkeypatch, SPACE_VK, 0)
    suppressor.was_typed(keyboard.Key.space)

    assert suppressor.was_typed(keyboard.Key.space) is False


def test_a_stale_keypress_does_not_count(suppressor, monkeypatch):
    intercept(suppressor, monkeypatch, SPACE_VK, 0)

    assert suppressor.was_typed(keyboard.Key.space, within=-1) is False


def test_key_releases_are_not_counted_as_typing(suppressor, monkeypatch):
    import src.hotkey_suppression as module

    monkeypatch.setattr(module.Quartz, "CGEventGetIntegerValueField",
                        lambda event, field: SPACE_VK)
    monkeypatch.setattr(module.Quartz, "CGEventGetFlags", lambda event: 0)
    suppressor.intercept(KEY_UP, SENTINEL)

    assert suppressor.was_typed(keyboard.Key.space) is False


def test_unregistered_keys_are_not_tracked(suppressor, monkeypatch):
    """Ordinary typing must not be recorded — we only track our own key."""
    intercept(suppressor, monkeypatch, C_KEY_VK, 0)

    assert suppressor.was_typed(keyboard.KeyCode.from_char("c")) is False


# -- noticing that macOS switched the tap off ---------------------------

TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF


def test_a_tap_disabled_by_timeout_asks_for_recovery():
    """macOS delivers this and then stops delivering anything at all. Missing
    it is what left the hotkey dead until the app was restarted."""
    asked = []
    suppressor = HotkeySuppressor(on_tap_disabled=lambda: asked.append(1))

    suppressor.intercept(TAP_DISABLED_BY_TIMEOUT, SENTINEL)

    assert asked == [1]


def test_a_tap_disabled_by_user_input_asks_for_recovery():
    asked = []
    suppressor = HotkeySuppressor(on_tap_disabled=lambda: asked.append(1))

    suppressor.intercept(TAP_DISABLED_BY_USER_INPUT, SENTINEL)

    assert asked == [1]


def test_an_ordinary_key_does_not_ask_for_recovery(monkeypatch):
    """Every keystroke passes through here; only the notification counts."""
    asked = []
    suppressor = HotkeySuppressor(on_tap_disabled=lambda: asked.append(1))
    suppressor.suppress_key(keyboard.Key.space)

    intercept(suppressor, monkeypatch, SPACE_VK, ALT_FLAG_MASK)

    assert asked == []


def test_the_notification_reads_no_event_fields(suppressor, monkeypatch):
    """Its keycode and flags are meaningless, and reading them on a broken
    event would raise inside the tap callback."""
    import src.hotkey_suppression as module

    def explode(*args, **kwargs):
        raise AssertionError("the notification's fields must not be read")

    monkeypatch.setattr(module.Quartz, "CGEventGetIntegerValueField", explode)
    monkeypatch.setattr(module.Quartz, "CGEventGetFlags", explode)

    assert suppressor.intercept(TAP_DISABLED_BY_TIMEOUT, SENTINEL) is SENTINEL
