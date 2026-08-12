"""Tests for the menu bar's lost-microphone warning and how it clears.

CoreAudio wedging is the one failure the app cannot work around, so the ⚠️
has to appear. But it used to be a one-way door: nothing cleared it except a
manual Stop→Start, so a microphone that recovered left the menu bar claiming
a fault that no longer existed.

The callbacks are exercised unbound, with a stub for `self`: constructing
the real menu bar app would need a GUI session, and the real orchestrator a
microphone and an event tap.
"""

import pytest

rumps = pytest.importorskip("rumps")

from src.main import VibePaste  # noqa: E402
from src.menubar import VibePasteMenuBar  # noqa: E402

HEALTHY = "🎙️"
WARNING = "🎙️ ⚠️"


class FakeRecovery:
    def __init__(self):
        self.signals = 0

    def on_wedge(self):
        self.signals += 1


class MenuBarStub:
    """Stands in for the menu bar app, which cannot be built headless."""

    def __init__(self, title=HEALTHY):
        self.title = title
        self.notifications = []
        self.recovery = FakeRecovery()


class OrchestratorStub:
    """Stands in for VibePaste, whose __init__ opens real devices."""

    def __init__(self):
        self._stray_characters = 3
        self.submitted = []
        self.device_ok_calls = 0
        self.worker = self
        self.on_device_ok = lambda: setattr(
            self, "device_ok_calls", self.device_ok_calls + 1
        )

    def submit(self, job):
        self.submitted.append(job)


def test_a_lost_microphone_raises_the_warning(monkeypatch):
    monkeypatch.setattr(rumps, "notification", lambda *a, **k: None)
    app = MenuBarStub()

    VibePasteMenuBar._on_device_failed(app, "the microphone never started")

    assert app.title == WARNING


def test_a_wedged_microphone_asks_for_a_restart(monkeypatch):
    """Only a new process gets the microphone back, so a wedge is the one
    failure allowed to end this one."""
    monkeypatch.setattr(rumps, "notification", lambda *a, **k: None)
    app = MenuBarStub()

    VibePasteMenuBar._on_device_failed(app, "did not respond", wedged=True)

    assert app.recovery.signals == 1


def test_a_microphone_held_by_another_app_does_not_end_the_process(monkeypatch):
    """Restarting would not hand over a device something else is holding —
    it would just close the app for nothing."""
    monkeypatch.setattr(rumps, "notification", lambda *a, **k: None)
    app = MenuBarStub()

    VibePasteMenuBar._on_device_failed(app, "could not be opened", wedged=False)

    assert app.recovery.signals == 0
    assert app.title == WARNING


def test_a_wedge_before_vibepaste_started_is_survivable(monkeypatch):
    """The recovery is built with VibePaste, so it can be absent."""
    monkeypatch.setattr(rumps, "notification", lambda *a, **k: None)
    app = MenuBarStub()
    app.recovery = None

    VibePasteMenuBar._on_device_failed(app, "did not respond", wedged=True)

    assert app.title == WARNING


def test_a_successful_recording_clears_the_warning():
    """Without this the ⚠️ was a one-way door — it outlived the fault and
    only a manual Stop→Start ever took it back down."""
    app = MenuBarStub(title=WARNING)

    VibePasteMenuBar._on_device_ok(app)

    assert app.title == HEALTHY


def test_clearing_an_already_healthy_menu_bar_leaves_it_alone():
    """Every saved recording calls this, so it must not rewrite the title on
    each one — reassigning it makes rumps redraw the status item."""
    app = MenuBarStub(title=HEALTHY)

    VibePasteMenuBar._on_device_ok(app)

    assert app.title == HEALTHY


def test_a_saved_recording_reports_the_microphone_working():
    """The signal has to come from audio actually reaching disk. Anything
    earlier would clear the warning on the strength of a press alone."""
    app = OrchestratorStub()

    VibePaste._on_recording_saved(app, "rec.wav", "model.bin", "bs", 5.0)

    assert app.device_ok_calls == 1
    assert len(app.submitted) == 1


def test_a_missing_device_ok_handler_does_not_break_saving():
    """The menu bar sets this; the terminal mode in main() never does."""
    app = OrchestratorStub()
    app.on_device_ok = None

    VibePaste._on_recording_saved(app, "rec.wav", "model.bin", "bs", 5.0)

    assert len(app.submitted) == 1


def test_a_menu_bar_that_raises_does_not_cost_the_recording():
    """Audio reaching disk must never depend on a status icon. A rumps call
    raising here would drop the job that the whole pipeline exists to run."""
    app = OrchestratorStub()

    def explode():
        raise RuntimeError("status item is gone")

    app.on_device_ok = explode

    VibePaste._on_recording_saved(app, "rec.wav", "model.bin", "bs", 5.0)

    assert len(app.submitted) == 1
