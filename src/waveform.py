"""Amplitude envelope for a recording.

Each recording is shown with the shape of the audio it actually contains,
rather than a generic icon: speech, pauses and emphasis are all visible, so
a recording is recognisable at a glance without reading the transcript.

It doubles as a fault indicator. A recording made without microphone access
is pure silence, and draws as a flat line — the failure is visible instead
of being hidden behind an invented transcript.
"""

import logging
import wave

import numpy as np

logger = logging.getLogger(__name__)

BUCKETS = 96
SILENCE_PEAK = 0


def envelope(wav_path, buckets=BUCKETS):
    """Return `buckets` floats in 0..1 describing the loudness over time.

    Returns all zeros for silence or an unreadable file, which is drawn as
    a flat line — an honest representation of both.
    """
    try:
        with wave.open(str(wav_path)) as handle:
            frames = handle.readframes(handle.getnframes())
            channels = handle.getnchannels()
    except (OSError, wave.Error) as e:
        logger.error(f"Could not read {wav_path}: {e}")
        return [0.0] * buckets

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size == 0:
        return [0.0] * buckets

    # RMS per bucket: closer to perceived loudness than peak, and steadier.
    chunks = np.array_split(samples, buckets)
    levels = np.array([
        float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        for chunk in chunks
    ])

    loudest = levels.max()
    if loudest <= 0:
        return [0.0] * buckets
    # Normalise to the recording's own peak so quiet speech is still legible,
    # then take a root to lift the low end the way an audio meter does.
    return [round(float(value), 3) for value in np.sqrt(levels / loudest)]


def is_silent(wav_path):
    """True if the file contains no signal at all."""
    try:
        with wave.open(str(wav_path)) as handle:
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return False
    samples = np.frombuffer(frames, dtype=np.int16)
    return samples.size > 0 and int(np.abs(samples).max()) == SILENCE_PEAK


def duration_seconds(wav_path):
    """Length of the recording in seconds, or 0 if it can't be read."""
    try:
        with wave.open(str(wav_path)) as handle:
            rate = handle.getframerate()
            return handle.getnframes() / float(rate) if rate else 0.0
    except (OSError, wave.Error, ZeroDivisionError):
        return 0.0
