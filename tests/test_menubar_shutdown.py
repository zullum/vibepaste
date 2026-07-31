"""Tests for the one hook that releases the whisper-server on quit.

Quitting used to skip VibePaste.stop() entirely, orphaning a ~3.4GB server
every single run. before_quit is the only hook that fires under NSApp's
terminate path — atexit and SIGTERM handlers were both measured not to —
so if this callback stops working, nothing else catches it.

The callback is exercised unbound, with a stub for `self`: constructing the
real menu bar app would need a GUI session, an event tap and a microphone.
"""

import pytest

rumps = pytest.importorskip("rumps")

from src.menubar import VibePasteMenuBar  # noqa: E402


class FakeVibePaste:
    def __init__(self, error=None):
        self.stops = 0
        self.error = error

    def stop(self):
        self.stops += 1
        if self.error:
            raise self.error


class Stub:
    """Stands in for the menu bar app, which cannot be built headless."""

    def __init__(self, vibepaste):
        self.vibepaste = vibepaste


def quit_with(vibepaste):
    VibePasteMenuBar._on_before_quit(Stub(vibepaste))


def test_quitting_stops_vibepaste_and_releases_the_server():
    vibepaste = FakeVibePaste()

    quit_with(vibepaste)

    assert vibepaste.stops == 1


def test_quitting_before_vibepaste_started_does_nothing():
    quit_with(None)  # must not raise


def test_a_failing_shutdown_does_not_block_the_quit():
    """Raising here would leave the app hung on the way out."""
    vibepaste = FakeVibePaste(error=RuntimeError("teardown exploded"))

    quit_with(vibepaste)

    assert vibepaste.stops == 1
