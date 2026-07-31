"""Tests for hotkey detection.

These exercise the tap-side callbacks directly — starting a real pynput
listener would need Input Monitoring permission and real key presses.
"""

import threading
import time

import pytest
from pynput import keyboard

from src.hotkey_suppression import virtual_keycode
from src.keyboard_listener import KeyboardListener

ALT_L = keyboard.Key.alt_l
ALT_R = keyboard.Key.alt_r
SPACE = keyboard.Key.space


@pytest.fixture
def listener():
    listener = KeyboardListener()
    listener.register_toggle("english", ALT_L, SPACE, callback=None)
    listener.register_toggle("bosnian", ALT_R, SPACE, callback=None)
    return listener


def drain(listener):
    names = []
    while not listener._queue.empty():
        names.append(listener._queue.get_nowait())
    return names


def test_modifier_plus_key_fires_once(listener):
    listener._on_press(ALT_L)
    listener._on_press(SPACE)

    assert drain(listener) == ["english"]


def test_key_alone_does_not_fire(listener):
    """Plain Space must never trigger anything — it used to fire globally."""
    listener._on_press(SPACE)

    assert drain(listener) == []


def test_left_and_right_option_are_distinct(listener):
    listener._on_press(ALT_R)
    listener._on_press(SPACE)

    assert drain(listener) == ["bosnian"]


def test_key_before_modifier_still_fires(listener):
    """macOS delivers Space *before* Option, not after.

    Observed on a real tap: pressing ⌥+Space reports Space at t=0ms and
    alt_r at t=+25ms, every time. Matching only the key that just arrived
    means the combination is never recognised, which is exactly how the
    hotkey silently stopped working.
    """
    listener._on_press(SPACE)
    listener._on_press(ALT_R)

    assert drain(listener) == ["bosnian"]


def test_key_before_modifier_fires_only_once(listener):
    listener._on_press(SPACE)
    listener._on_press(ALT_R)
    listener._on_press(SPACE)  # repeat while both are held

    assert drain(listener) == ["bosnian"]


def test_auto_repeat_does_not_refire(listener):
    listener._on_press(ALT_L)
    listener._on_press(SPACE)
    listener._on_press(SPACE)  # key repeat from holding it down
    listener._on_press(SPACE)

    assert drain(listener) == ["english"]


def test_second_press_after_release_fires_again(listener):
    """The toggle-off press must work — this is the 'press twice' bug."""
    listener._on_press(ALT_L)
    listener._on_press(SPACE)
    listener._on_release(SPACE)
    drain(listener)

    listener._on_press(SPACE)

    assert drain(listener) == ["english"]


def test_a_missed_release_cannot_wedge_the_listener(listener):
    """A dropped release event used to leave state stuck forever."""
    listener.stuck_key_seconds = 0.05
    listener._on_press(ALT_L)
    listener._on_press(SPACE)
    drain(listener)
    # Releases never arrive — the event tap dropped them.
    time.sleep(0.1)

    listener._on_press(ALT_L)
    listener._on_press(SPACE)

    assert drain(listener) == ["english"]


def test_unrelated_keys_are_ignored(listener):
    listener._on_press(keyboard.KeyCode.from_char("a"))
    listener._on_press(keyboard.KeyCode.from_char("b"))

    assert drain(listener) == []


def test_release_of_the_modifier_stops_the_combo(listener):
    listener._on_press(ALT_L)
    listener._on_release(ALT_L)
    listener._on_press(SPACE)

    assert drain(listener) == []


def test_bare_key_is_ignored_until_enabled(listener):
    """The space bar must keep working when nothing is being recorded."""
    listener.register_bare_key("stop", SPACE, callback=None)

    listener._on_press(SPACE)

    assert drain(listener) == []


def test_bare_key_fires_when_enabled(listener):
    listener.register_bare_key("stop", SPACE, callback=None)
    listener.enable_bare_key("stop", True)

    listener._on_press(SPACE)

    assert drain(listener) == ["stop"]


def test_bare_key_ignores_space_with_a_modifier_held(listener):
    """⌥+Space must start a recording, not immediately stop it."""
    listener.register_bare_key("stop", SPACE, callback=None)
    listener.enable_bare_key("stop", True)

    listener._on_press(ALT_R)
    listener._on_press(SPACE)

    assert drain(listener) == ["bosnian"]


def test_bare_key_stops_firing_once_disabled(listener):
    listener.register_bare_key("stop", SPACE, callback=None)
    listener.enable_bare_key("stop", True)
    listener._on_press(SPACE)
    listener._on_release(SPACE)
    drain(listener)

    listener.enable_bare_key("stop", False)
    listener._on_press(SPACE)

    assert drain(listener) == []


def test_callbacks_run_off_the_tap_thread(listener):
    seen = []
    tap_thread = threading.current_thread()
    done = threading.Event()

    def callback(name):
        seen.append((name, threading.current_thread() is tap_thread))
        done.set()

    listener.register_toggle("english", ALT_L, SPACE, callback=callback)
    worker = threading.Thread(target=listener._dispatch_loop, daemon=True)
    worker.start()

    listener._on_press(ALT_L)
    listener._on_press(SPACE)

    assert done.wait(2), "callback never ran"
    assert seen == [("english", False)]


def test_a_raising_callback_does_not_kill_the_dispatcher(listener):
    calls = []
    done = threading.Event()

    def callback(name):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("boom")
        done.set()

    listener.register_toggle("english", ALT_L, SPACE, callback=callback)
    threading.Thread(target=listener._dispatch_loop, daemon=True).start()

    for _ in range(2):
        listener._on_press(ALT_L)
        listener._on_press(SPACE)
        listener._on_release(SPACE)
        listener._on_release(ALT_L)

    assert done.wait(2), "dispatcher died on the first exception"
    assert calls == ["english", "english"]


# -- reporting the stray character the hotkey typed ---------------------

def test_no_stray_character_when_the_key_was_swallowed(listener):
    listener._intercepting = True

    assert listener.hotkey_typed_a_character("english") is False


def test_stray_character_when_the_key_slipped_through(listener):
    listener._intercepting = True
    listener.suppressor._typed_at[
        virtual_keycode(keyboard.Key.space)
    ] = time.monotonic()

    assert listener.hotkey_typed_a_character("english") is True


def test_without_an_intercepting_tap_the_key_always_lands(listener):
    """Nothing is swallowed in the fallback mode, so a space is always typed."""
    listener._intercepting = False

    assert listener.hotkey_typed_a_character("english") is True


def test_an_unknown_hotkey_reports_no_stray_character(listener):
    listener._intercepting = False

    assert listener.hotkey_typed_a_character("nope") is False
