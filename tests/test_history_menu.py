"""Tests for the recent-recordings menu entries."""

import numpy as np
import pytest

from src.history_menu import PREVIEW_CHARS, RecordingEntry, load_entries
from src.recording_store import RecordingStore


@pytest.fixture
def store(tmp_path):
    return RecordingStore(tmp_path / "recordings", max_recordings=10)


def _audio():
    return np.int16(np.zeros(100))


def test_entry_reads_time_and_language_from_the_filename(tmp_path):
    entry = RecordingEntry(tmp_path / "rec_20260731_143002_bs.wav", "zdravo")

    assert entry.timestamp.hour == 14
    assert entry.timestamp.minute == 30
    assert entry.language == "bs"
    assert entry.label().startswith("14:30  bs  zdravo")


def test_long_text_is_truncated_in_the_label(tmp_path):
    entry = RecordingEntry(tmp_path / "rec_20260731_143002_en.wav", "x" * 200)

    label = entry.label()
    assert label.endswith("…")
    assert len(label) < PREVIEW_CHARS + 30


def test_newlines_are_flattened_in_the_label(tmp_path):
    entry = RecordingEntry(
        tmp_path / "rec_20260731_143002_en.wav", "line one\n\nline two"
    )

    assert "\n" not in entry.label()
    assert "line one line two" in entry.label()


def test_entry_without_transcript_is_marked(tmp_path):
    entry = RecordingEntry(tmp_path / "rec_20260731_143002_en.wav", "")

    assert entry.has_text is False
    assert "no transcript" in entry.label()


def test_load_entries_pairs_wavs_with_their_transcripts(store):
    first = store.save(_audio(), 16000, "en")
    store.save_transcript(first, "hello there")
    store.save(_audio(), 16000, "bs")  # no transcript

    entries = load_entries(store)

    assert len(entries) == 2
    by_path = {e.wav_path: e for e in entries}
    assert by_path[first].text == "hello there"
    assert sum(1 for e in entries if not e.has_text) == 1


def test_load_entries_is_newest_first(store):
    older = store.save(_audio(), 16000, "en")
    newer = store.save(_audio(), 16000, "en")

    entries = load_entries(store)

    assert entries[0].wav_path == newer
    assert entries[-1].wav_path == older


def test_load_entries_on_empty_store(store):
    assert load_entries(store) == []
