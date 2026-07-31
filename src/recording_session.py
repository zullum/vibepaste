"""Owns the start/stop lifecycle of a single recording.

Kept separate from the orchestrator so there is exactly one place that
decides whether a recording is in progress — the old `is_recording` boolean
scattered across the app is what allowed double-stops and racing threads.
"""

import logging
import threading

logger = logging.getLogger(__name__)


class RecordingSession:
    """Start and stop recordings, saving audio before anything else."""

    def __init__(self, audio_recorder, store, overlay, sounds, sample_rate,
                 warn_seconds, max_seconds, on_saved):
        """
        Args:
            on_saved: called with (wav_path, model_path, language, duration)
                once the audio is safely on disk.
        """
        self.audio_recorder = audio_recorder
        self.store = store
        self.overlay = overlay
        self.sounds = sounds
        self.sample_rate = sample_rate
        self.warn_seconds = warn_seconds
        self.max_seconds = max_seconds
        self.on_saved = on_saved

        self._active = False
        self._language = None
        self._model_path = None
        self._lock = threading.Lock()
        self._auto_stop_timer = None

    @property
    def is_recording(self):
        with self._lock:
            return self._active

    def start(self, language, model_path, model_name):
        """Begin recording. Returns False if the microphone couldn't open."""
        try:
            self.audio_recorder.start_recording()
        except Exception as e:
            logger.error(f"Could not start recording: {e}")
            self.sounds.play_error()
            print(f"\n⚠️  Could not start recording: {e}")
            print("Check microphone permissions in System Settings")
            return False

        with self._lock:
            self._active = True
            self._language = language
            self._model_path = model_path

        self._start_auto_stop_timer()
        self.sounds.play_start()
        self.overlay.show_recording(self.warn_seconds, self.max_seconds)
        logger.info(f"Recording started ({language}, {model_name})")
        print(f"\n🔴 Recording... ({language}, {model_name}) — "
              f"same combo to stop")
        return True

    def stop(self):
        """Stop, persist the audio, and hand it to `on_saved`.

        Idempotent: a second call while not recording does nothing, so a
        duplicate hotkey or a racing auto-stop can't double-process a clip.
        """
        with self._lock:
            if not self._active:
                return False
            self._active = False
            language, model_path = self._language, self._model_path

        self._cancel_auto_stop_timer()
        self.sounds.play_stop()
        audio, duration = self.audio_recorder.stop_recording()

        if audio is None or duration <= 0:
            logger.warning("Nothing captured")
            print("⚠️  No audio recorded")
            return False

        # Priority: get the audio onto disk before anything can go wrong.
        try:
            wav_path = self.store.save(audio, self.sample_rate, language)
        except OSError as e:
            logger.error(f"Could not save recording: {e}")
            self.sounds.play_error()
            print(f"❌ Could not save recording: {e}")
            return False

        print(f"💾 Saved {duration:.1f}s → {wav_path.name}")
        self.on_saved(wav_path, model_path, language, duration)
        return True

    def shutdown(self):
        self._cancel_auto_stop_timer()

    # -- internals -----------------------------------------------------

    def _start_auto_stop_timer(self):
        """Hard cap, in case the overlay process isn't there to report one."""
        self._cancel_auto_stop_timer()
        timer = threading.Timer(self.max_seconds, self._on_timeout)
        timer.daemon = True
        timer.start()
        self._auto_stop_timer = timer

    def _cancel_auto_stop_timer(self):
        if self._auto_stop_timer is not None:
            self._auto_stop_timer.cancel()
            self._auto_stop_timer = None

    def _on_timeout(self):
        logger.warning("Recording hit the hard time limit — stopping")
        self.stop()
