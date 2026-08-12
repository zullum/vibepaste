"""Tests for the cap on automatic restarts.

The app can now terminate itself to recover a wedged microphone. The one
thing standing between that and an app that disappears repeatedly is this
ledger, so its failure modes matter more than its happy path.

Wall-clock time, not monotonic: the whole point is to count across process
restarts, and a monotonic clock starts over in every new process.
"""

import json

import pytest

from src.restart_ledger import RestartLedger


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def build(tmp_path, clock=None, max_restarts=3, window_seconds=600):
    clock = clock or FakeClock()
    ledger = RestartLedger(
        path=tmp_path / "restarts.json",
        max_restarts=max_restarts,
        window_seconds=window_seconds,
        clock=clock,
    )
    return ledger, clock


def test_a_fresh_ledger_allows_a_restart(tmp_path):
    ledger, _ = build(tmp_path)

    assert ledger.allowed() is True


def test_restarts_are_allowed_up_to_the_cap(tmp_path):
    ledger, _ = build(tmp_path, max_restarts=3)

    for _ in range(3):
        assert ledger.allowed() is True
        ledger.record()

    assert ledger.allowed() is False


def test_restarts_older_than_the_window_stop_counting(tmp_path):
    """Otherwise one bad afternoon disables the recovery for good."""
    ledger, clock = build(tmp_path, max_restarts=3, window_seconds=600)
    for _ in range(3):
        ledger.record()
    assert ledger.allowed() is False

    clock.advance(601)

    assert ledger.allowed() is True


def test_the_count_survives_a_new_process(tmp_path):
    """Each restart is a fresh process, so an in-memory count would reset
    every time and the cap would never bind."""
    clock = FakeClock()
    first, _ = build(tmp_path, clock=clock, max_restarts=2)
    first.record()
    first.record()

    second, _ = build(tmp_path, clock=clock, max_restarts=2)

    assert second.allowed() is False


def test_a_corrupt_ledger_does_not_block_a_restart(tmp_path):
    """A half-written file must not cost the user their recovery. Failing
    open is right here: the cap is a safety valve, not a security control."""
    path = tmp_path / "restarts.json"
    path.write_text("{not json at all")
    ledger = RestartLedger(path=path, clock=FakeClock())

    assert ledger.allowed() is True


def test_a_corrupt_ledger_is_replaced_rather_than_appended_to(tmp_path):
    path = tmp_path / "restarts.json"
    path.write_text("{not json at all")
    ledger = RestartLedger(path=path, clock=FakeClock())

    ledger.record()

    assert json.loads(path.read_text()) == [1_000_000.0]


def test_recording_creates_the_directory(tmp_path):
    ledger, _ = build(tmp_path / "missing")

    ledger.record()

    assert (tmp_path / "missing" / "restarts.json").exists()


def test_an_unwritable_ledger_does_not_raise(tmp_path):
    """Losing the count is survivable; crashing the recovery path is not."""
    ledger = RestartLedger(path=tmp_path, clock=FakeClock())  # a directory

    ledger.record()

    assert ledger.allowed() is True


def test_a_restart_just_happened_is_true_immediately_after_one(tmp_path):
    """How the fresh process knows to explain the blip it just caused."""
    ledger, clock = build(tmp_path)
    ledger.record()

    clock.advance(3)

    assert ledger.just_restarted(within_seconds=30) is True


def test_an_old_restart_is_not_reported_as_just_happened(tmp_path):
    ledger, clock = build(tmp_path)
    ledger.record()

    clock.advance(31)

    assert ledger.just_restarted(within_seconds=30) is False


def test_no_restarts_at_all_is_not_reported_as_just_happened(tmp_path):
    ledger, _ = build(tmp_path)

    assert ledger.just_restarted(within_seconds=30) is False


def test_a_clock_that_jumped_backwards_does_not_wedge_the_cap(tmp_path):
    """NTP and DST move wall-clock time. A future timestamp must age out
    rather than counting forever against the cap."""
    ledger, clock = build(tmp_path, max_restarts=1, window_seconds=600)
    clock.advance(10_000)
    ledger.record()
    clock.now -= 10_000

    assert ledger.allowed() is True
