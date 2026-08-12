"""Tests that a wedged microphone blocks nobody but its own thread.

CoreAudio's HAL mutex can deadlock, and nothing in this process can undo
that. What must never happen again is the deadlock landing on the hotkey
dispatch thread, where it killed every hotkey until the app was restarted.
"""

import threading
import time

from src.audio_device import AudioDevice


class FakeStream:
    def __init__(self):
        self.aborted = False
        self.closed = False

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


def test_opening_reports_the_stream_ready():
    device = AudioDevice()
    ready = threading.Event()
    try:
        device.open(FakeStream, on_ready=ready.set)
        assert ready.wait(2)
    finally:
        device.shutdown()


def test_a_failed_open_is_reported():
    device = AudioDevice()
    errors = []
    done = threading.Event()

    def boom():
        raise RuntimeError("microphone busy")

    try:
        device.open(boom, on_error=lambda e: (errors.append(e), done.set()))
        assert done.wait(2)
        assert "microphone busy" in str(errors[0])
    finally:
        device.shutdown()


def test_open_returns_immediately_even_when_the_device_hangs():
    """The guarantee this module exists for: the caller is the hotkey
    dispatch thread, and it must never wait on CoreAudio."""
    blocked = threading.Event()
    device = AudioDevice(wedged_seconds=0.05)
    try:
        started = time.monotonic()
        device.open(lambda: blocked.wait(30) or FakeStream())
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, f"open() blocked the caller for {elapsed:.2f}s"
    finally:
        blocked.set()
        device.shutdown()


def test_close_returns_immediately_even_when_the_device_hangs():
    blocked = threading.Event()
    device = AudioDevice(wedged_seconds=0.05)
    try:
        device.open(lambda: blocked.wait(30) or FakeStream())
        started = time.monotonic()
        device.close()
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, f"close() blocked the caller for {elapsed:.2f}s"
    finally:
        blocked.set()
        device.shutdown()


def test_a_hung_device_is_reported_as_wedged():
    blocked = threading.Event()
    device = AudioDevice(wedged_seconds=0.05)
    try:
        device.open(lambda: blocked.wait(30) or FakeStream())

        deadline = time.monotonic() + 2
        while not device.is_wedged() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert device.is_wedged() is True
        assert "opening" in device.busy_with()
    finally:
        blocked.set()
        device.shutdown()


def test_a_healthy_device_is_never_reported_as_wedged():
    device = AudioDevice(wedged_seconds=0.05)
    ready = threading.Event()
    try:
        device.open(FakeStream, on_ready=ready.set)
        assert ready.wait(2)
        time.sleep(0.1)   # comfortably past the wedge threshold

        assert device.is_wedged() is False
        assert device.busy_with() == ""
    finally:
        device.shutdown()


def test_closing_aborts_the_stream_rather_than_draining_it():
    """The frames have already been taken by this point, so pending buffers
    are of no interest and abort() is the faster way out."""
    stream = FakeStream()
    device = AudioDevice()
    ready = threading.Event()
    try:
        device.open(lambda: stream, on_ready=ready.set)
        assert ready.wait(2)
        device.close()

        deadline = time.monotonic() + 2
        while not stream.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert stream.aborted and stream.closed
    finally:
        device.shutdown()


def test_a_second_open_closes_the_first_stream():
    """Two live input streams on one device is how the HAL mutex deadlock
    was reached in the first place."""
    first, second = FakeStream(), FakeStream()
    device = AudioDevice()
    ready = threading.Event()
    try:
        device.open(lambda: first)
        device.open(lambda: second, on_ready=ready.set)
        assert ready.wait(2)

        assert first.closed is True
        assert second.closed is False
    finally:
        device.shutdown()
