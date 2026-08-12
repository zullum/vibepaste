"""Tests for the decision to trade this process for a working microphone.

Everything here guards one asymmetry: recovering a wedged microphone is
worth a 3-second restart, but quitting *without* a relaunch behind it leaves
the user with no app at all. So every gate — the cap, the bundle check, the
post-drain re-check, the armed helper — is a reason not to quit, and each
one gets its own test.

The ledger and the restarter are the real classes; only the process-level
effects (quitting, notifying, sleeping) are stood in for.
"""

import threading

import pytest

from src.app_restart import AppRestarter
from src.restart_ledger import RestartLedger
from src.wedge_recovery import WedgeRecovery


class FakeSpawn:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.error:
            raise self.error


class FakeTime:
    """A clock that only moves when something sleeps on it."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Harness:
    def __init__(self, tmp_path, pending=None, wedged_after_drain=True,
                 spawn_error=None, max_restarts=3, bundle=True):
        app = tmp_path / "VibePaste.app"
        app.mkdir()
        self.spawn = FakeSpawn(error=spawn_error)
        self.ledger = RestartLedger(
            path=tmp_path / "restarts.json", max_restarts=max_restarts,
            clock=lambda: 1_000_000.0,
        )
        self.restarter = AppRestarter(
            app if bundle else None, pid=999, spawn=self.spawn
        )
        self.time = FakeTime()
        self.notifications = []
        self.quits = 0
        self._pending = list(pending or [0])
        self.wedged_after_drain = wedged_after_drain
        self.recovery = WedgeRecovery(
            ledger=self.ledger,
            restarter=self.restarter,
            pending=self.pending,
            is_wedged=lambda: self.wedged_after_drain,
            notify=lambda title, message: self.notifications.append(message),
            quit_app=self.quit_app,
            drain_timeout_seconds=30.0,
            poll_seconds=0.5,
            clock=self.time.clock,
            sleep=self.time.sleep,
        )

    def pending(self):
        return self._pending.pop(0) if len(self._pending) > 1 \
            else self._pending[0]

    def quit_app(self):
        self.quits += 1

    @property
    def armed(self):
        return len(self.spawn.calls) == 1


def test_a_confirmed_wedge_trades_the_process_for_a_working_microphone(tmp_path):
    h = Harness(tmp_path)

    assert h.recovery.attempt() is True
    assert h.armed is True
    assert h.quits == 1
    assert h.ledger.allowed() is True  # one of three used


def test_the_relaunch_is_armed_before_the_app_quits(tmp_path):
    """The other order is unrecoverable: quit first and a failed spawn
    leaves nothing running and nothing coming back."""
    order = []
    h = Harness(tmp_path)
    h.spawn.calls = order  # both append here, so order is observable
    h.recovery.quit_app = lambda: order.append("quit")
    h.recovery.attempt()

    assert order[-1] == "quit"
    assert len(order) == 2


def test_queued_transcriptions_are_drained_before_quitting(tmp_path):
    """A clip already on its way to the clipboard should still get there."""
    h = Harness(tmp_path, pending=[2, 2, 1, 0])

    h.recovery.attempt()

    assert h.time.sleeps == [0.5, 0.5, 0.5]
    assert h.quits == 1


def test_a_queue_that_never_drains_does_not_block_the_restart(tmp_path):
    """A wedged transcription must not also cost the microphone recovery."""
    h = Harness(tmp_path, pending=[1])

    h.recovery.attempt()

    assert h.time.now == pytest.approx(30.0)
    assert h.quits == 1


def test_a_microphone_that_recovers_during_the_drain_keeps_the_process(tmp_path):
    """The drain is free evidence: if CoreAudio came back while we waited,
    there is nothing to recover and quitting would be pure loss."""
    h = Harness(tmp_path, pending=[2, 1, 0], wedged_after_drain=False)

    assert h.recovery.attempt() is False
    assert h.quits == 0
    assert h.armed is False
    assert h.ledger.allowed() is True


def test_past_the_cap_it_explains_instead_of_restarting(tmp_path):
    """Restarting has not helped three times, so the fault is not ours —
    saying so is worth more than a fourth relaunch."""
    h = Harness(tmp_path, max_restarts=1)
    h.ledger.record()

    assert h.recovery.attempt() is False
    assert h.quits == 0
    assert h.armed is False
    assert any("coreaudiod" in m for m in h.notifications)


def test_terminal_mode_is_left_alone_entirely(tmp_path):
    """No bundle to reopen. Quitting would close the app being watched."""
    h = Harness(tmp_path, bundle=False)

    assert h.recovery.attempt() is False
    assert h.quits == 0
    assert h.notifications == []


def test_a_relaunch_that_could_not_be_armed_does_not_quit(tmp_path):
    """The single most important guarantee here."""
    h = Harness(tmp_path, spawn_error=OSError("fork failed"))

    assert h.recovery.attempt() is False
    assert h.quits == 0


def test_the_user_is_told_before_the_app_disappears(tmp_path):
    h = Harness(tmp_path)

    h.recovery.attempt()

    assert any("estart" in m for m in h.notifications)


def test_signalling_a_wedge_does_not_block_the_caller(tmp_path):
    """on_wedge() is called from the hotkey dispatch thread and the wedge
    timer. Draining there is exactly the slow callback that gets the event
    tap disabled."""
    import time

    h = Harness(tmp_path)
    released = threading.Event()

    def blocking_sleep(seconds):
        released.wait(5)
        h.time.sleep(seconds)  # keep the drain deadline moving, or it spins

    h.recovery.sleep = blocking_sleep
    h.recovery.pending = lambda: 1

    started = time.monotonic()
    h.recovery.on_wedge()
    elapsed = time.monotonic() - started
    released.set()

    assert elapsed < 0.5, "on_wedge() drained on the caller's thread"


def test_a_second_wedge_does_not_start_a_second_recovery(tmp_path):
    """Three presses against a dead microphone must not mean three quits."""
    h = Harness(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def blocking_pending():
        started.set()
        release.wait(2)
        return 0

    h.recovery.pending = blocking_pending
    h.recovery.on_wedge()
    started.wait(2)
    h.recovery.on_wedge()
    release.set()
    h.recovery.wait(2)

    assert h.quits == 1
