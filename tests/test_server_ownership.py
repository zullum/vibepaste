"""Tests for the record of which servers we are allowed to kill.

The asymmetry this enforces: adopting a server is a read and may be
permissive, but killing one is destructive and must be provably ours. A
whisper-server started by hand for other work must survive us.
"""

import os

import pytest

from src.server_ownership import OwnershipRegistry


@pytest.fixture
def registry(tmp_path):
    return OwnershipRegistry(tmp_path / "whisper-owned.json")


def test_owns_a_pid_it_recorded(registry):
    registry.record(4242)

    assert registry.owns(4242)


def test_does_not_own_a_pid_it_never_recorded(registry):
    registry.record(4242)

    assert not registry.owns(9999)


def test_owns_nothing_before_anything_is_recorded(registry):
    assert not registry.owns(os.getpid())


def test_recording_the_same_pid_twice_keeps_one_entry(registry):
    registry.record(4242)
    registry.record(4242)

    assert registry.pids().count(4242) <= 1


def test_forgetting_a_pid_gives_up_the_right_to_kill_it(registry):
    registry.record(os.getpid())
    registry.forget(os.getpid())

    assert not registry.owns(os.getpid())


def test_lists_only_pids_that_are_still_alive(registry):
    registry.record(os.getpid())
    registry.record(999999)  # far above any live pid

    assert registry.pids() == [os.getpid()]


def test_a_corrupt_registry_owns_nothing_rather_than_raising(registry):
    """A damaged file must not stop transcription, only cost us reclaiming."""
    registry.path.write_text("{not json")

    assert registry.pids() == []
    assert not registry.owns(4242)


def test_a_registry_holding_the_wrong_shape_owns_nothing(registry):
    registry.path.write_text('{"pids": [1, 2]}')

    assert registry.pids() == []


def test_non_integer_entries_are_ignored(registry):
    registry.path.write_text('[1, "two", null]')

    assert registry.owns(1)
    assert not registry.owns("two")


def test_an_unwritable_registry_does_not_raise(tmp_path):
    """Losing the record is benign — we adopt instead of reclaiming."""
    registry = OwnershipRegistry(tmp_path / "nodir" / "x" / "owned.json")
    registry.path.parent.mkdir(parents=True)
    registry.path.parent.chmod(0o500)
    try:
        registry.record(4242)  # must not raise
    finally:
        registry.path.parent.chmod(0o700)
