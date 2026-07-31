"""Tests for the Recent Recordings window's contents."""

import wave

import numpy as np
import pytest

from src.recordings_view import (
    SILENT_NOTE, format_duration, render_html,
)
from src.waveform import duration_seconds, envelope, is_silent

RATE = 16000


def write_wav(path, samples):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return path


@pytest.fixture
def speech(tmp_path):
    tone = (np.sin(np.linspace(0, 200, RATE)) * 12000).astype(np.int16)
    return write_wav(tmp_path / "rec_20260731_120000_000_en.wav", tone)


@pytest.fixture
def silence(tmp_path):
    return write_wav(tmp_path / "rec_20260731_120100_000_en.wav",
                     np.zeros(RATE, dtype=np.int16))


def test_envelope_of_silence_is_flat(silence):
    assert set(envelope(silence)) == {0.0}


def test_envelope_of_speech_has_signal(speech):
    levels = envelope(speech)
    assert max(levels) == pytest.approx(1.0)
    assert all(0.0 <= level <= 1.0 for level in levels)


def test_envelope_of_an_unreadable_file_is_flat_not_an_error(tmp_path):
    broken = tmp_path / "not-audio.wav"
    broken.write_text("this is not a wav file")

    assert set(envelope(broken)) == {0.0}


def test_silence_is_detected(silence, speech):
    """A recording made without microphone access is pure silence."""
    assert is_silent(silence) is True
    assert is_silent(speech) is False


def test_duration_is_read_from_the_file(speech):
    assert duration_seconds(speech) == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"), (7, "0:07"), (66, "1:06"), (600, "10:00"),
])
def test_duration_formatting(seconds, expected):
    assert format_duration(seconds) == expected


def test_a_transcript_cannot_break_out_of_the_page():
    """Transcribed speech is untrusted text as far as the page is concerned."""
    nasty = [{"time": "12:00:00", "language": "en", "duration": "0:01",
              "levels": [0.0], "text": "</script><img src=x onerror=alert(1)>",
              "note": "", "path": "/tmp/x.wav"}]

    html = render_html(nasty)

    assert "</script><img" not in html
    assert "<\\/script>" in html


def test_a_silent_recording_is_not_offered_for_copying(silence, monkeypatch):
    """Its transcript is whatever the model invented for silence."""
    import src.recordings_view as view

    class Store:
        def list_recordings(self):
            return [silence]

    silence.with_suffix(".txt").write_text("Hvala što pratite kanal.")

    items = view.build_items(Store())

    assert items[0]["text"] == ""
    assert items[0]["note"] == SILENT_NOTE
