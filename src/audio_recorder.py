"""Audio capture from the default microphone.

Capture only — persisting audio is RecordingStore's job, and every call
into CoreAudio belongs to AudioDevice. Nothing here may block: this runs on
the hotkey dispatch thread, and a blocked call there kills every later
hotkey (see src/audio_device.py for the deadlock that taught us).
"""

import logging
import threading
import time

import numpy as np
import sounddevice as sd

from src.audio_device import AudioDevice

logger = logging.getLogger(__name__)


class AudioRecorder:
    """Captures microphone audio into memory as int16 PCM frames."""

    def __init__(self, sample_rate=16000, channels=1, device=None):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device if device is not None else AudioDevice()
        self._frames = []
        self._lock = threading.Lock()
        self._is_recording = False
        self._is_open = False
        self._started_at = None
        self._generation = 0   # identifies the current stream's callbacks

    @property
    def is_recording(self):
        return self._is_recording

    def elapsed_seconds(self):
        """Seconds since recording started, or 0.0 when idle."""
        if not self._is_recording or self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    def _audio_callback(self, generation, indata, status):
        """sounddevice callback — must stay cheap, runs on the audio thread.

        `generation` identifies the stream this callback belongs to. A stream
        whose teardown blocked can keep firing after it was abandoned, and
        without this check it would append its frames to the *next*
        recording.
        """
        if status:
            logger.warning(f"Audio callback status: {status}")
        with self._lock:
            if self._is_recording and generation == self._generation:
                self._frames.append(indata.copy())

    @property
    def is_open(self):
        """True once the microphone has actually started delivering."""
        return self._is_open

    def start_recording(self, on_error=None):
        """Ask for the microphone and begin collecting. Never blocks.

        The open happens on the device thread, so this returns before any
        audio arrives — frames simply start landing once it does. Device
        failures cannot be raised here for the same reason; they arrive on
        `on_error` instead.
        """
        if self._is_recording:
            logger.warning("Already recording")
            return

        with self._lock:
            self._frames = []
            self._generation += 1
            generation = self._generation

        self._is_recording = True
        self._is_open = False
        self._started_at = time.monotonic()

        def factory():
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=(
                    lambda indata, frames, time_info, status:
                    self._audio_callback(generation, indata, status)
                ),
                dtype=np.float32,
            )
            stream.start()
            return stream

        self.device.open(
            factory,
            on_ready=lambda: setattr(self, "_is_open", True),
            on_error=on_error,
        )
        logger.info("Audio recording started")

    def stop_recording(self):
        """Stop capturing and return the audio as int16 PCM.

        Returns:
            (audio, duration_seconds) where audio is an int16 ndarray, or
            (None, 0.0) if nothing was captured.
        """
        if not self._is_recording:
            logger.warning("Not currently recording")
            return None, 0.0

        self._is_recording = False
        self._is_open = False
        duration = self.elapsed_seconds()
        self._started_at = None

        # Hands the teardown to the device thread; this call cannot block.
        self.device.close()

        with self._lock:
            frames = self._frames
            self._frames = []

        if not frames:
            logger.warning("No audio data recorded")
            return None, duration

        audio = np.concatenate(frames, axis=0)
        # float32 [-1, 1] -> int16, clipped so loud input can't wrap around
        audio = np.int16(np.clip(audio, -1.0, 1.0) * 32767)

        actual_duration = len(audio) / float(self.sample_rate)
        peak = int(np.abs(audio).max()) if audio.size else 0
        if peak == 0:
            # Denied microphone access does not raise — CoreAudio just returns
            # silence. Whisper then "transcribes" that silence into a stock
            # sentence the user never said, which looks like a working app.
            logger.error(
                "Captured %.1fs of pure silence (peak=0) — microphone access "
                "is almost certainly denied. Any transcript would be invented.",
                actual_duration,
            )
        logger.info(f"Captured {actual_duration:.1f}s of audio (peak={peak})")
        return audio, actual_duration

    def discard(self):
        """Give up on a recording without collecting its audio."""
        self._is_recording = False
        self._is_open = False
        self._started_at = None
        self.device.close()
        with self._lock:
            self._frames = []

    def is_wedged(self):
        """True if CoreAudio has stopped responding — see AudioDevice."""
        return self.device.is_wedged()

    def shutdown(self):
        self.device.shutdown()

    def get_default_device(self):
        """Return default input device info, or None if it can't be queried."""
        try:
            device_info = sd.query_devices(kind="input")
            logger.info(f"Default input device: {device_info['name']}")
            return device_info
        except Exception as e:
            logger.error(f"Failed to query devices: {e}")
            return None
