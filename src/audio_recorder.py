"""Audio capture from the default microphone.

Capture only — persisting audio is RecordingStore's job.
"""

import logging
import threading
import time

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioRecorder:
    """Captures microphone audio into memory as int16 PCM frames."""

    def __init__(self, sample_rate=16000, channels=1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames = []
        self._lock = threading.Lock()
        self._is_recording = False
        self._stream = None
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

    def start_recording(self):
        """Open the input stream and begin capturing.

        Raises whatever sounddevice raises if the device is unavailable.
        """
        if self._is_recording:
            logger.warning("Already recording")
            return

        with self._lock:
            self._frames = []
            self._generation += 1
            generation = self._generation

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=(
                    lambda indata, frames, time_info, status:
                    self._audio_callback(generation, indata, status)
                ),
                dtype=np.float32,
            )
            self._stream.start()
        except Exception:
            self._stream = None
            raise

        self._is_recording = True
        self._started_at = time.monotonic()
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
        duration = self.elapsed_seconds()
        self._started_at = None

        stream, self._stream = self._stream, None
        if stream is not None:
            self._close_in_background(stream)

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

    @staticmethod
    def _close_in_background(stream):
        """Tear the stream down without blocking the caller.

        Closing a CoreAudio input stream can block forever: observed with
        the hotkey thread stuck in HALB_Mutex::Lock, reached through
        PortAudio's FinishStoppingStream. Because that call sat on the
        hotkey dispatch thread, the stop hotkey — and every hotkey after
        it — stopped working, and the recording ran on with no way to end
        it.

        The captured audio has already been taken by this point, so the
        teardown has nothing left to give us and can safely be abandoned to
        its own thread. abort() rather than stop() because pending buffers
        are of no interest once the frames are in hand.
        """
        def close():
            try:
                stream.abort()
                stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")

        threading.Thread(target=close, name="audio-teardown",
                         daemon=True).start()

    def get_default_device(self):
        """Return default input device info, or None if it can't be queried."""
        try:
            device_info = sd.query_devices(kind="input")
            logger.info(f"Default input device: {device_info['name']}")
            return device_info
        except Exception as e:
            logger.error(f"Failed to query devices: {e}")
            return None
