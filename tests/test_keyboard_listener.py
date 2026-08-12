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
    """The hotkey names queued for the worker.

    The same queue also carries diagnostics the tap callback is not allowed
    to write itself (see drain_all); those are not hotkeys.
    """
    return [item for item in drain_all(listener) if isinstance(item, str)]


def drain_all(listener):
    items = []
    while not listener.dispatcher.queue.empty():
        items.append(listener.dispatcher.queue.get_nowait())
    return items


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
    listener.dispatcher.start()

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
    listener.dispatcher.start()

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


# -- surviving a tap macOS switched off ---------------------------------

class FakeTapListener:
    def __init__(self, alive=True, event_tap="tap"):
        self.alive = alive
        self.event_tap = event_tap

    def is_alive(self):
        return self.alive


def test_the_stuck_key_warning_is_written_off_the_tap_thread(listener):
    """Logging is file I/O, and file I/O in the tap callback is what gets the
    tap disabled — the very failure this whole path exists to survive."""
    listener.stuck_key_seconds = 0.05
    listener._on_press(ALT_L)
    drain_all(listener)
    time.sleep(0.1)

    listener._on_press(SPACE)

    reported = [item for item in drain_all(listener)
                if not isinstance(item, str)]
    assert [item.key for item in reported] == [ALT_L]


def test_a_dead_tap_is_not_reported_as_healthy(listener, monkeypatch):
    """running=True kept being printed about a stone-deaf tap."""
    import src.keyboard_listener as module

    listener._listener = FakeTapListener()
    monkeypatch.setattr(module, "tap_is_enabled", lambda tap: False)

    assert listener.is_running() is True
    assert listener.is_healthy() is False


def test_a_live_tap_is_reported_as_healthy(listener, monkeypatch):
    import src.keyboard_listener as module

    listener._listener = FakeTapListener()
    monkeypatch.setattr(module, "tap_is_enabled", lambda tap: True)

    assert listener.is_healthy() is True


def test_an_unreadable_tap_falls_back_to_thread_liveness(listener, monkeypatch):
    """Without a handle we cannot ask, and guessing 'dead' would restart a
    perfectly good listener every five seconds."""
    import src.keyboard_listener as module

    listener._listener = FakeTapListener(event_tap=None)
    monkeypatch.setattr(module, "tap_is_enabled", lambda tap: None)

    assert listener.is_healthy() is True


def test_a_dead_listener_thread_is_not_healthy(listener):
    listener._listener = FakeTapListener(alive=False)

    assert listener.is_healthy() is False


def test_a_stopped_listener_is_never_rebuilt_by_the_watchdog(listener):
    """Shutdown can race a health check; a tap created afterwards would keep
    its thread alive with nothing left to stop it."""
    listener.stop()

    assert listener.restart() is False
    assert listener.is_running() is False


def test_recovery_forgets_keys_held_while_the_tap_was_deaf(listener):
    """A stale Option makes the next plain Space look like the hotkey."""
    listener._on_press(ALT_L)

    listener.forget_key_state()
    listener._on_press(SPACE)

    assert drain(listener) == []
