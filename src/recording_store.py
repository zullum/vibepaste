"""Persistent store for recorded audio.

Audio is written here *before* transcription is attempted, so a failed or
slow transcription can never lose what the user said. The store keeps the
N most recent recordings and prunes everything older.
"""

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from scipy.io.wavfile import write as write_wav

logger = logging.getLogger(__name__)

# rec_20260731_143002_481_bs.wav  (date_time_milliseconds_language)
FILENAME_PATTERN = re.compile(r"^rec_\d{8}_\d{6}_\d{3}_[a-z]{2}\.wav$")


class RecordingStore:
    """Writes recordings to disk and keeps only the most recent ones."""

    def __init__(self, directory, max_recordings=10):
        self.directory = Path(directory)
        self.max_recordings = max_recordings
        self._lock = threading.Lock()
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, audio, sample_rate, language):
        """Write audio to a uniquely named WAV and prune old recordings.

        Args:
            audio: int16 ndarray of PCM samples.
            sample_rate: samples per second.
            language: two-letter language code, used in the filename.

        Returns:
            Path to the written WAV.

        Raises:
            OSError: if the file cannot be written. Callers should treat this
                as fatal for the recording — there is nothing else to fall
                back on.
        """
        with self._lock:
            path = self._unique_path(language)
            write_wav(str(path), sample_rate, audio)
            logger.info(f"Recording saved: {path.name}")
            self._prune()
            return path

    def save_transcript(self, wav_path, text):
        """Write the transcript next to its WAV. Best effort — never raises."""
        try:
            transcript_path = Path(wav_path).with_suffix(".txt")
            transcript_path.write_text(text, encoding="utf-8")
            logger.info(f"Transcript saved: {transcript_path.name}")
            return transcript_path
        except OSError as e:
            logger.error(f"Could not save transcript: {e}")
            return None

    def list_recordings(self):
        """Recordings currently in the store, newest first."""
        try:
            entries = [
                p for p in self.directory.iterdir()
                if p.is_file() and FILENAME_PATTERN.match(p.name)
            ]
        except OSError as e:
            logger.error(f"Could not list recordings: {e}")
            return []
        # mtime first so same-second recordings order correctly; name breaks ties.
        return sorted(
            entries,
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )

    def _unique_path(self, language):
        """Build a path that doesn't collide.

        Millisecond precision rather than a retry counter: a counter reuses
        a name as soon as pruning frees it, which can hand two different
        recordings the same filename.
        """
        while True:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            candidate = self.directory / f"rec_{stamp}_{language}.wav"
            if not candidate.exists():
                return candidate
            time.sleep(0.001)

    def _prune(self):
        """Delete everything beyond the newest `max_recordings`."""
        recordings = self.list_recordings()
        for stale in recordings[self.max_recordings:]:
            for path in (stale, stale.with_suffix(".txt")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.error(f"Could not delete {path.name}: {e}")
            logger.info(f"Pruned old recording: {stale.name}")
