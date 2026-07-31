"""Recent-recordings menu for the menubar app.

Lists the recordings the store is keeping, newest first. Clicking one copies
its transcript to the clipboard; recordings whose transcription failed are
shown too, so a failure is visible rather than silent.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 45
NO_TRANSCRIPT_LABEL = "no transcript — click to reveal audio"


class RecordingEntry:
    """One recording as presented in the menu."""

    def __init__(self, wav_path, text):
        self.wav_path = Path(wav_path)
        self.text = text

    @property
    def has_text(self):
        return bool(self.text)

    @property
    def timestamp(self):
        """Recording time parsed from the filename, or the file mtime."""
        stem = self.wav_path.stem  # rec_20260731_143002_bs
        parts = stem.split("_")
        if len(parts) >= 3:
            try:
                return datetime.strptime(f"{parts[1]}_{parts[2]}",
                                         "%Y%m%d_%H%M%S")
            except ValueError:
                pass
        try:
            return datetime.fromtimestamp(self.wav_path.stat().st_mtime)
        except OSError:
            return None

    @property
    def language(self):
        return self.wav_path.stem.split("_")[-1]

    def label(self):
        """Single-line menu title: time, language, and a text preview."""
        stamp = self.timestamp
        when = stamp.strftime("%H:%M") if stamp else "??:??"
        if not self.has_text:
            return f"{when}  {self.language}  ({NO_TRANSCRIPT_LABEL})"
        preview = " ".join(self.text.split())
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS].rstrip() + "…"
        return f"{when}  {self.language}  {preview}"


def load_entries(store):
    """Build RecordingEntry objects for everything currently in the store."""
    entries = []
    for wav_path in store.list_recordings():
        transcript = wav_path.with_suffix(".txt")
        text = ""
        try:
            if transcript.exists():
                text = transcript.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.error(f"Could not read {transcript.name}: {e}")
        entries.append(RecordingEntry(wav_path, text))
    return entries


def reveal_in_finder(path):
    """Open Finder with the file selected."""
    try:
        subprocess.run(["open", "-R", str(path)], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Could not reveal {path}: {e}")
