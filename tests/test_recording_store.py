"""Tests for the last-N recording store."""

import numpy as np
import pytest

from src.recording_store import RecordingStore


@pytest.fixture
def store(tmp_path):
    return RecordingStore(tmp_path / "recordings", max_recordings=3)


def _audio(seconds=0.1, sample_rate=16000):
    return np.int16(np.zeros(int(seconds * sample_rate)))


def test_save_creates_wav(store):
    path = store.save(_audio(), 16000, "bs")

    assert path.exists()
    assert path.suffix == ".wav"
    assert path.stem.endswith("_bs")


def test_save_never_overwrites_within_the_same_second(store):
    first = store.save(_audio(), 16000, "en")
    second = store.save(_audio(), 16000, "en")

    assert first != second
    assert first.exists() and second.exists()


def test_prunes_to_max_recordings(store):
    paths = [store.save(_audio(), 16000, "en") for _ in range(6)]

    remaining = store.list_recordings()
    assert len(remaining) == 3
    # The three most recent survive; the three oldest are gone.
    assert set(remaining) == set(paths[-3:])
    assert not any(p.exists() for p in paths[:3])


def test_pruning_removes_the_transcript_too(store):
    stale = store.save(_audio(), 16000, "en")
    store.save_transcript(stale, "old text")
    transcript = stale.with_suffix(".txt")
    assert transcript.exists()

    for _ in range(3):
        store.save(_audio(), 16000, "en")

    assert not stale.exists()
    assert not transcript.exists()


def test_save_transcript_writes_next_to_the_wav(store):
    wav = store.save(_audio(), 16000, "bs")

    transcript = store.save_transcript(wav, "zdravo svijete")

    assert transcript == wav.with_suffix(".txt")
    assert transcript.read_text(encoding="utf-8") == "zdravo svijete"


def test_list_ignores_unrelated_files(store):
    store.save(_audio(), 16000, "en")
    (store.directory / "notes.txt").write_text("junk")
    (store.directory / "random.wav").write_text("junk")

    assert len(store.list_recordings()) == 1


def test_list_is_newest_first(store):
    first = store.save(_audio(), 16000, "en")
    second = store.save(_audio(), 16000, "en")

    listed = store.list_recordings()
    assert listed.index(second) < listed.index(first)
