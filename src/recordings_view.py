"""Builds what the Recent Recordings window shows.

Kept free of AppKit so the presentation logic can be tested directly.
"""

import json
import logging
from pathlib import Path

from src.history_menu import load_entries
from src.waveform import duration_seconds, envelope, is_silent

logger = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent.parent / "assets" / "recordings.html"
DATA_TOKEN = "__DATA__"

SILENT_NOTE = "No sound captured — check microphone access."
NO_TEXT_NOTE = "No transcript — the audio is still here."


def format_duration(seconds):
    minutes, remainder = divmod(int(round(seconds)), 60)
    return f"{minutes}:{remainder:02d}"


def build_items(store):
    """One dict per recording, newest first, ready to render."""
    items = []
    for entry in load_entries(store):
        stamp = entry.timestamp
        silent = is_silent(entry.wav_path)
        # A silent recording's transcript is whatever the model invented for
        # digital silence, so it is not offered for copying. Saying so is
        # more useful than showing a sentence the user never said.
        text = "" if silent else entry.text
        items.append({
            "time": stamp.strftime("%H:%M:%S") if stamp else "--:--:--",
            "language": entry.language,
            "duration": format_duration(duration_seconds(entry.wav_path)),
            "levels": envelope(entry.wav_path),
            "text": text,
            "note": SILENT_NOTE if silent else NO_TEXT_NOTE,
            "path": str(entry.wav_path),
        })
    return items


def render_html(items):
    """The window's HTML with this session's recordings baked in."""
    template = TEMPLATE.read_text(encoding="utf-8")
    # json.dumps escapes the payload; "</" is split so a transcript can
    # never close the surrounding <script> element.
    payload = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    return template.replace(DATA_TOKEN, payload)
