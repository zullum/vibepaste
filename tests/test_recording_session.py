"""Tests for the recording lifecycle and audio-first persistence."""

import numpy as np
import pytest

from src.recording_session import RecordingSession


class FakeRecorder:
    """Stands in for the recorder, whose device work is now asynchronous.

    `fail_start` reports through on_error rather than raising: the open
    happens on the device thread, so it cannot raise back into the caller.
    `never_opens` is the wedged CoreAudio case — the request is accepted and
    the microphone simply never arrives.
    """

    def __init__(self, audio=None, duration=3.0, fail_start=False,
                 never_opens=False, wedged=False):
        self.audio = np.int16(np.zeros(100)) if audio is None else audio
        self.duration = duration
        self.fail_start = fail_start
        self.never_opens = never_opens
        self.wedged = wedged
        self.started = 0
        self.stopped = 0
        self.discarded = 0
        self.is_open = False

    def is_wedged(self):
        return self.wedged

    def start_recording(self, on_error=None):
        self.started += 1
        if self.fail_start:
            if on_error is not None:
                on_error(RuntimeError("microphone busy"))
            return
        self.is_open = not self.never_opens

    def stop_recording(self):
        self.stopped += 1
        self.is_open = False
        return self.audio, self.duration

    def discard(self):
        self.discarded += 1
        self.is_open = False


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

    def hide(self):
        self.calls.append(("hide",))


class FakeSounds:
    def __init__(self):
        self.played = []

    def play_start(self):
        self.played.append("start")

    def play_stop(self):
        self.played.append("stop")

    def play_error(self):
        self.played.append("error")


def build(recorder=None, store=None, max_seconds=120, wedged_seconds=30,
          failures=None):
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
        on_failed=(
            (lambda reason, wedged: failures.append((reason, wedged)))
            if failures is not None else None
        ),
        wedged_seconds=wedged_seconds,
    )
    return session, saved


def test_starts_and_reports_recording():
    session, _ = build()

    assert session.start("en", "model.bin", "turbo") is True
    assert session.is_recording is True


def test_failed_microphone_leaves_session_idle():
    """The open now fails on the device thread, so it cannot raise back into
    start() — the session has to be taken back out of recording instead."""
    failures = []
    session, _ = build(recorder=FakeRecorder(fail_start=True),
                       failures=failures)

    assert session.start("en", "model.bin", "turbo") is False
    assert session.is_recording is False
    assert len(failures) == 1 and "microphone busy" in failures[0][0]


def test_a_failed_start_hides_the_overlay_it_just_showed():
    """start() shows the overlay before asking for the microphone, so the
    abort has to take it back down or a dead recording stays on screen."""
    session, _ = build(recorder=FakeRecorder(fail_start=True))

    session.start("en", "model.bin", "turbo")

    assert session.overlay.calls[-1] == ("hide",)


def test_a_microphone_that_never_opens_is_given_up_on():
    """The wedged-CoreAudio case: the request is accepted and the device
    simply never arrives. Waiting forever is what killed every hotkey."""
    failures = []
    recorder = FakeRecorder(never_opens=True)
    session, _ = build(recorder=recorder, wedged_seconds=0.05,
                       failures=failures)
    session.start("en", "model.bin", "turbo")

    session._wedge_timer.join(2)

    assert session.is_recording is False
    assert recorder.discarded == 1
    assert len(failures) == 1 and "did not respond" in failures[0][0]


def test_a_recording_that_did_start_is_not_given_up_on():
    """The wedge timer must not shoot down a healthy long recording."""
    session, _ = build(recorder=FakeRecorder(), wedged_seconds=0.05)
    session.start("en", "model.bin", "turbo")

    session._wedge_timer.join(2)

    assert session.is_recording is True


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


def test_a_silent_recording_whose_microphone_never_opened_is_reported():
    """The failure that looked like a bug in the overlay.

    CoreAudio wedged, the microphone never delivered a frame, and the user
    stopped after 5.25s — under the wedge deadline, so `_on_wedge_timeout`
    never fired. All the app said was 'Nothing captured', the overlay simply
    vanished, and three presses in a row gave no hint that a restart was the
    only cure.
    """
    failures = []
    recorder = FakeRecorder(never_opens=True, duration=5.25)
    session, saved = build(recorder=recorder, failures=failures)
    session.start("bs", "model.bin", "v3")
    recorder.audio = None

    assert session.stop() is False
    assert saved == []
    assert len(failures) == 1 and "never started" in failures[0][0]


def test_a_quick_double_tap_is_not_blamed_on_the_microphone():
    """A press-press inside the cold-start window captures nothing either.

    The microphone has simply not woken yet (~2.5s the first time CoreAudio
    is asked), so shouting 'restart VibePaste' would be wrong.
    """
    failures = []
    recorder = FakeRecorder(never_opens=True, duration=0.2)
    session, _ = build(recorder=recorder, failures=failures)
    session.start("en", "model.bin", "turbo")
    recorder.audio = None

    assert session.stop() is False
    assert failures == []


def test_an_empty_recording_from_a_working_microphone_is_not_a_device_failure():
    """The device opened, so whatever went wrong, it was not the device."""
    failures = []
    recorder = FakeRecorder(duration=5.0)
    session, _ = build(recorder=recorder, failures=failures)
    session.start("en", "model.bin", "turbo")
    recorder.audio = None

    assert session.stop() is False
    assert failures == []


def test_a_press_against_a_wedged_microphone_is_refused():
    """Once CoreAudio has wedged, every later press was pretending.

    The old path started a recording, showed the overlay and waited the full
    wedge deadline before admitting the microphone was never coming — six
    seconds of theatre per press, with the device already known to be stuck.
    """
    failures = []
    recorder = FakeRecorder(wedged=True)
    session, _ = build(recorder=recorder, failures=failures)

    assert session.start("en", "model.bin", "turbo") is False
    assert session.is_recording is False
    assert recorder.started == 0
    assert len(failures) == 1 and "still not responding" in failures[0][0]


def test_a_refused_press_puts_nothing_on_screen_to_take_back():
    """The guard runs before the overlay, sounds and timers are armed, so a
    refusal has nothing to undo — no flicker of a recording that never was."""
    session, _ = build(recorder=FakeRecorder(wedged=True))

    session.start("en", "model.bin", "turbo")

    assert session.overlay.calls == []
    assert session.sounds.played == ["error"]
    assert session._wedge_timer is None
    assert session._auto_stop_timer is None


def test_every_wedge_signal_is_reported_as_a_wedge():
    """A restart only helps when CoreAudio stopped answering. The three
    signals that mean that have to be distinguishable by structure — string
    matching on the reason is how this kind of thing rots."""
    refused, silent, timed_out = [], [], []

    session, _ = build(recorder=FakeRecorder(wedged=True), failures=refused)
    session.start("en", "model.bin", "turbo")

    recorder = FakeRecorder(never_opens=True, duration=5.25)
    session, _ = build(recorder=recorder, failures=silent)
    session.start("en", "model.bin", "turbo")
    recorder.audio = None
    session.stop()

    session, _ = build(recorder=FakeRecorder(never_opens=True),
                       wedged_seconds=0.05, failures=timed_out)
    session.start("en", "model.bin", "turbo")
    session._wedge_timer.join(2)

    assert refused[0][1] is True
    assert silent[0][1] is True
    assert timed_out[0][1] is True


def test_a_microphone_held_by_another_app_is_not_reported_as_a_wedge():
    """CoreAudio answered — it just said no. Restarting the app would not
    hand over a device that something else is holding, so this must not
    trigger one."""
    failures = []
    session, _ = build(recorder=FakeRecorder(fail_start=True),
                       failures=failures)

    session.start("en", "model.bin", "turbo")

    assert failures[0][1] is False


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
