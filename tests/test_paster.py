"""Tests for deleting the character the hotkey typed before pasting.

Backspace is only safe when something observed a character actually being
typed, and only in the same place the paste is about to land — so these
pin down when it is sent and, just as importantly, when it is not.
"""

import pytest

from src.paster import MAX_STRAY_DELETIONS, Paster


@pytest.fixture
def keys(monkeypatch):
    """Record every synthesised keystroke, in order."""
    pressed = []
    import src.paster as module

    monkeypatch.setattr(module.pyautogui, "press", pressed.append)
    monkeypatch.setattr(module.pyautogui, "hotkey",
                        lambda *combo: pressed.append("+".join(combo)))
    monkeypatch.setattr(module, "accessibility_granted", lambda: True)
    monkeypatch.setattr(module.pyperclip, "copy", lambda text: None)
    monkeypatch.setattr(module.pyperclip, "paste", lambda: "hello")
    monkeypatch.setattr(module, "CLIPBOARD_SETTLE_SECONDS", 0)
    monkeypatch.setattr(module, "BACKSPACE_SETTLE_SECONDS", 0)
    monkeypatch.setattr(module, "PASTE_SETTLE_SECONDS", 0)
    return pressed


def test_backspace_precedes_the_paste(keys):
    Paster(notify=False).paste_text("hello", delete_first=1)

    assert keys == ["backspace", "command+v"]


def test_nothing_is_deleted_by_default(keys):
    Paster(notify=False).paste_text("hello")

    assert keys == ["command+v"]


def test_a_zero_count_sends_no_backspace(keys):
    Paster(notify=False).paste_text("hello", delete_first=0)

    assert keys == ["command+v"]


def test_the_deletion_count_is_capped(keys):
    """A confused caller must never chew through the user's own text."""
    Paster(notify=False).paste_text("hello", delete_first=99)

    assert keys.count("backspace") == MAX_STRAY_DELETIONS


def test_a_negative_count_deletes_nothing(keys):
    Paster(notify=False).paste_text("hello", delete_first=-3)

    assert keys == ["command+v"]


def test_nothing_is_deleted_without_accessibility(keys, monkeypatch):
    """No permission means no paste — so the field must be left untouched."""
    import src.paster as module
    monkeypatch.setattr(module, "accessibility_granted", lambda: False)

    result = Paster(notify=False).paste_text("hello", delete_first=1)

    assert keys == []
    assert result.pasted is False


def test_nothing_is_deleted_when_the_clipboard_write_fails(keys, monkeypatch):
    """There is no transcript to paste, so deleting would just destroy text."""
    import src.paster as module
    monkeypatch.setattr(module.pyperclip, "paste", lambda: "something else")
    monkeypatch.setattr(module, "COPY_ATTEMPTS", 1)

    Paster(notify=False).paste_text("hello", delete_first=1)

    assert keys == []


def test_empty_text_deletes_nothing(keys):
    Paster(notify=False).paste_text("", delete_first=1)

    assert keys == []


def test_a_failed_backspace_still_pastes(keys, monkeypatch):
    """A leftover space is a blemish; losing the transcript is not."""
    import src.paster as module

    def explode(_key):
        raise RuntimeError("no permission")

    monkeypatch.setattr(module.pyautogui, "press", explode)

    result = Paster(notify=False).paste_text("hello", delete_first=1)

    assert result.pasted is True
