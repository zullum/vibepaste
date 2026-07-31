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
