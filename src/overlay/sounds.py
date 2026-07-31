"""Audio cues for recording events."""

import logging
import subprocess

logger = logging.getLogger(__name__)

START_SOUND = "/System/Library/Sounds/Hero.aiff"
STOP_SOUND = "/System/Library/Sounds/Glass.aiff"
ERROR_SOUND = "/System/Library/Sounds/Basso.aiff"


class SoundEffects:
    """Plays short system sounds. Fire and forget — never blocks."""

    @staticmethod
    def _play(path, volume="0.3"):
        try:
            subprocess.Popen(
                ["afplay", "-v", volume, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            logger.error(f"Failed to play {path}: {e}")

    @classmethod
    def play_start(cls):
        cls._play(START_SOUND)

    @classmethod
    def play_stop(cls):
        cls._play(STOP_SOUND)

    @classmethod
    def play_error(cls):
        cls._play(ERROR_SOUND, volume="0.4")
