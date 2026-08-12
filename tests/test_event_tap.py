"""Tests that a tap macOS switched off comes back on its own.

This is the failure the app used to need a restart to clear: macOS disables
the event tap, pynput never re-enables it, and the listener goes on
reporting itself alive while not one key event arrives.

The escalation is exercised against a fake controller — a real tap would
need Input Monitoring and real keystrokes, which is also why the end-to-end
proof lives in tools/check_tap_recovery.py rather than here.
"""

import threading

from src.event_tap import TapWatchdog


class FakeController:
    """Stands in for the listener, recording what the watchdog asked of it."""

    def __init__(self, healthy=True, revive_works=True, restart_works=True):
        self.healthy = healthy
        self.revive_works = revive_works
        self.restart_works = restart_works
        self.calls = []
        self.forgotten = 0

    def is_healthy(self):
        return self.healthy

    def revive(self):
        self.calls.append("revive")
        if self.revive_works:
            self.healthy = True
        return self.revive_works

    def restart(self):
        self.calls.append("restart")
        if self.restart_works:
            self.healthy = True
        return self.restart_works

    def forget_key_state(self):
        self.forgotten += 1


def went_deaf(controller, **kwargs):
    """A watchdog whose tap worked and has since stopped delivering.

    The transition is the point. Recovery stands down until the tap has been
    seen working at least once, because a tap that never worked is missing a
    permission — and rebuilding it every few seconds will not grant one.
    """
    watchdog = TapWatchdog(controller, **kwargs)
    controller.healthy = True
    assert watchdog.check_now() is True
    controller.healthy = False
    return watchdog


def test_a_healthy_tap_is_left_alone():
    controller = FakeController(healthy=True)

    assert TapWatchdog(controller).check_now() is True
    assert controller.calls == []


def test_a_disabled_tap_is_re_enabled():
    """The whole point: no restart, no user action, the hotkey just works."""
    controller = FakeController()
    watchdog = went_deaf(controller)

    assert watchdog.check_now() is True
    assert controller.calls == ["revive"]


def test_the_listener_is_restarted_when_re_enabling_does_not_take():
    """Two different mechanisms, so a wedged tap cannot fail the same way twice."""
    controller = FakeController(revive_works=False)
    watchdog = went_deaf(controller)

    assert watchdog.check_now() is True
    assert controller.calls == ["revive", "restart"]


def test_a_revive_that_reports_success_but_stays_deaf_still_restarts():
    """CGEventTapEnable cannot fail loudly, so its result is not trusted."""
    controller = FakeController()
    watchdog = went_deaf(controller)
    controller.revive = lambda: controller.calls.append("revive") or True

    assert watchdog.check_now() is True
    assert controller.calls == ["revive", "restart"]


def test_a_restart_that_leaves_the_tap_deaf_is_not_called_recovery():
    """A listener that started is not the same thing as a tap that delivers;
    treating them as equal logged 'hotkeys are live again' about dead ones."""
    controller = FakeController(revive_works=False)
    watchdog = went_deaf(controller)
    controller.restart = lambda: controller.calls.append("restart") or True

    assert watchdog.check_now() is False


def test_an_unrecoverable_tap_is_reported_rather_than_hidden():
    controller = FakeController(revive_works=False, restart_works=False)
    watchdog = went_deaf(controller)

    assert watchdog.check_now() is False


def test_a_tap_that_never_worked_is_not_rebuilt_over_and_over():
    """Without Input Monitoring the tap is created but never enabled. Nothing
    here can grant that permission, so retrying every few seconds only fills
    the log and churns listener threads for as long as the app runs."""
    controller = FakeController(healthy=False)
    watchdog = TapWatchdog(controller)

    assert watchdog.check_now() is False
    assert controller.calls == []


def test_recovery_waits_before_trying_again_after_it_fails():
    """Permission revoked mid-session must not become a restart loop."""
    controller = FakeController(revive_works=False, restart_works=False)
    watchdog = went_deaf(controller, backoff=30)
    watchdog.check_now()
    controller.calls.clear()

    assert watchdog.check_now() is False
    assert controller.calls == []


def test_a_failed_recovery_is_retried_once_the_wait_is_over():
    """Giving up permanently would need the restart this whole change removes."""
    controller = FakeController(revive_works=False, restart_works=False)
    watchdog = went_deaf(controller, backoff=0)
    watchdog.check_now()
    controller.calls.clear()
    controller.revive_works = True

    assert watchdog.check_now() is True
    assert controller.calls == ["revive"]


def test_recovery_discards_the_keys_held_while_the_tap_was_deaf():
    """Releases go missing while deaf; a stale Option misreads a plain Space."""
    controller = FakeController()
    watchdog = went_deaf(controller)

    watchdog.check_now()

    assert controller.forgotten == 1


def test_a_healthy_tap_does_not_discard_key_state():
    """A poll during normal typing must not forget genuinely held keys."""
    controller = FakeController(healthy=True)

    TapWatchdog(controller).check_now()

    assert controller.forgotten == 0


def test_the_notification_alone_authorises_recovery():
    """macOS only reports disabling a tap that was delivering, so the
    notification is itself proof there is something to bring back."""
    controller = FakeController(healthy=False)

    watchdog = TapWatchdog(controller)
    watchdog.request_recovery()

    assert watchdog.check_now() is True
    assert controller.calls == ["revive"]


def test_the_watchdog_recovers_without_waiting_for_its_next_poll():
    """macOS tells us the moment it happens; waiting out the interval instead
    would leave the hotkey dead for seconds every time."""
    controller = FakeController(healthy=False)
    watchdog = TapWatchdog(controller, interval=3600)
    recovered = threading.Event()
    controller.forget_key_state = recovered.set

    watchdog.start()
    try:
        watchdog.request_recovery()
        assert recovered.wait(2), "the wake-up signal was not acted on"
    finally:
        watchdog.stop()


def test_the_watchdog_reports_whether_it_is_watching():
    """A watchdog that quietly died is the same silent failure it exists to
    end, so the diagnostic line has to be able to ask."""
    watchdog = TapWatchdog(FakeController(), interval=0.01)

    assert watchdog.is_running() is False
    watchdog.start()
    try:
        assert watchdog.is_running() is True
    finally:
        watchdog.stop()


def test_a_raising_controller_does_not_kill_the_watchdog():
    """A dead watchdog is a dead hotkey — the failure it exists to prevent."""
    controller = FakeController(healthy=False)
    watchdog = TapWatchdog(controller, interval=0.01)
    calls = []
    recovered = threading.Event()

    def is_healthy():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        recovered.set()
        return True

    controller.is_healthy = is_healthy
    watchdog.start()
    try:
        assert recovered.wait(2), "the watchdog stopped after one exception"
    finally:
        watchdog.stop()
