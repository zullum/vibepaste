"""Tests for the recording lifecycle and audio-first persistence."""

import numpy as np
import pytest

from src.recording_session import RecordingSession


class FakeRecorder:
    def __init__(self, audio=None, duration=3.0, fail_start=False):
        self.audio = np.int16(np.zeros(100)) if audio is None else audio
        self.duration = duration
        self.fail_start = fail_start
        self.started = 0
        self.stopped = 0

    def start_recording(self):
        self.started += 1
        if self.fail_start:
            raise RuntimeError("microphone busy")

    def stop_recording(self):
        self.stopped += 1
        return self.audio, self.duration


class FakeStore:
    def __init__(self, fail=False):
        self.saved = []
        self.fail = fail

    def save(self, audio, sample_rate, language):
        if self.fail:
            raise OSError("disk full")
        from pathlib import Path
        path = Path(f"/tmp/rec_{len(self.saved)}_{language}.wav")
        self.saved.append(path)
        return path


class FakeOverlay:
    def __init__(self):
        self.calls = []

    def show_recording(self, warn, maximum):
        self.calls.append(("record", warn, maximum))


class FakeSounds:
    def __init__(self):
        self.played = []

    def play_start(self):
        self.played.append("start")

    def play_stop(self):
        self.played.append("stop")

    def play_error(self):
        self.played.append("error")


def build(recorder=None, store=None, max_seconds=120):
    saved = []
    session = RecordingSession(
        audio_recorder=recorder or FakeRecorder(),
        store=store or FakeStore(),
        overlay=FakeOverlay(),
        sounds=FakeSounds(),
        sample_rate=16000,
        warn_seconds=60,
        max_seconds=max_seconds,
        on_saved=lambda *args: saved.append(args),
    )
    return session, saved


def test_starts_and_reports_recording():
    session, _ = build()

    assert session.start("en", "model.bin", "turbo") is True
    assert session.is_recording is True


def test_failed_microphone_leaves_session_idle():
    session, _ = build(recorder=FakeRecorder(fail_start=True))

    assert session.start("en", "model.bin", "turbo") is False
    assert session.is_recording is False


def test_audio_is_saved_before_the_job_is_handed_on():
    store = FakeStore()
    session, saved = build(store=store)
    session.start("bs", "model.bin", "v3")

    session.stop()

    assert len(store.saved) == 1
    wav_path, model_path, language, duration = saved[0]
    assert wav_path == store.saved[0]
    assert language == "bs"
    assert duration == 3.0


def test_stop_is_idempotent():
    """A duplicate hotkey or a racing auto-stop must not double-process."""
    store = FakeStore()
    session, saved = build(store=store)
    session.start("en", "model.bin", "turbo")

    assert session.stop() is True
    assert session.stop() is False
    assert session.stop() is False
    assert len(saved) == 1


def test_stop_without_start_does_nothing():
    session, saved = build()

    assert session.stop() is False
    assert saved == []


def test_no_job_when_nothing_was_captured():
    session, saved = build(recorder=FakeRecorder(audio=None, duration=0.0))
    session.start("en", "model.bin", "turbo")
    session.audio_recorder.audio = None

    assert session.stop() is False
    assert saved == []
    assert session.is_recording is False


def test_a_save_failure_does_not_queue_a_job():
    session, saved = build(store=FakeStore(fail=True))
    session.start("en", "model.bin", "turbo")

    assert session.stop() is False
    assert saved == []


def test_hard_time_limit_stops_the_recording():
    store = FakeStore()
    session, saved = build(store=store, max_seconds=0.05)
    session.start("en", "model.bin", "turbo")

    session._auto_stop_timer.join(2)

    assert session.is_recording is False
    assert len(saved) == 1


def test_stopping_cancels_the_hard_limit_timer():
    session, saved = build(max_seconds=0.2)
    session.start("en", "model.bin", "turbo")
    session.stop()

    import time
    time.sleep(0.4)

    assert len(saved) == 1, "the cancelled timer fired anyway"
