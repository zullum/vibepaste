"""Owns the start/stop lifecycle of a single recording.

Kept separate from the orchestrator so there is exactly one place that
decides whether a recording is in progress — the old `is_recording` boolean
scattered across the app is what allowed double-stops and racing threads.
"""

import logging
import threading

from src.audio_device import SILENT_START_SECONDS, WEDGED_SECONDS

logger = logging.getLogger(__name__)


class RecordingSession:
    """Start and stop recordings, saving audio before anything else."""

    def __init__(self, audio_recorder, store, overlay, sounds, sample_rate,
                 warn_seconds, max_seconds, on_saved, on_failed=None,
                 wedged_seconds=WEDGED_SECONDS):
        """
        Args:
            on_saved: called with (wav_path, model_path, language, duration)
                once the audio is safely on disk.
            on_failed: called with (reason, wedged) when a recording is
                lost. `wedged` means CoreAudio stopped answering, which only
                a new process can fix; a clean refusal — the device held by
                another app — is False, because restarting would not free it.
        """
        self.audio_recorder = audio_recorder
        self.store = store
        self.overlay = overlay
        self.sounds = sounds
        self.sample_rate = sample_rate
        self.warn_seconds = warn_seconds
        self.max_seconds = max_seconds
        self.on_saved = on_saved
        self.on_failed = on_failed
        self.wedged_seconds = wedged_seconds

        self._active = False
        self._language = None
        self._model_path = None
        self._lock = threading.Lock()
        self._auto_stop_timer = None
        self._wedge_timer = None

    @property
    def is_recording(self):
        with self._lock:
            return self._active

    def start(self, language, model_path, model_name):
        """Begin recording.

        Counts as recording from this instant, before the microphone has
        actually opened. The open happens on the device thread and takes
        ~70ms warm but ~2.5s the first time CoreAudio wakes up; waiting for
        it would put that delay on the hotkey and, when CoreAudio wedges,
        would never return at all. So the overlay appears immediately and a
        second press stops it, exactly as before — and if the microphone
        never arrives, `_abort` takes it all back.

        Refuses outright while the device is already wedged. Nothing is put
        in place first, so there is nothing to take back: the old path armed
        the overlay and timers and then spent the whole wedge deadline
        discovering what `is_wedged()` could have said immediately.
        """
        if self.audio_recorder.is_wedged():
            self._report_failure(
                "the microphone is still not responding — restart VibePaste",
                wedged=True,
            )
            return False

        with self._lock:
            self._active = True
            self._language = language
            self._model_path = model_path

        # Everything the abort path has to undo is put in place *before* the
        # microphone is asked for. The device thread can fail the open before
        # this method returns, and an abort that ran first would otherwise be
        # overwritten by the overlay and timers set up after it.
        self._start_auto_stop_timer()
        self._start_wedge_timer()
        self.sounds.play_start()
        self.overlay.show_recording(self.warn_seconds, self.max_seconds)
        logger.info(f"Recording started ({language}, {model_name})")
        print(f"\n🔴 Recording... ({language}, {model_name}) — "
              f"same combo to stop")

        self.audio_recorder.start_recording(on_error=self._on_device_error)
        return self.is_recording

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

        self._cancel_timers()
        self.sounds.play_stop()
        # Read before stopping — stop_recording() clears it. Whether the
        # microphone ever delivered a frame is the only thing separating a
        # dead device from a double-tap, and both arrive here as no audio.
        microphone_opened = self.audio_recorder.is_open
        audio, duration = self.audio_recorder.stop_recording()

        if audio is None or duration <= 0:
            if not microphone_opened and duration >= SILENT_START_SECONDS:
                # Long enough that the device was never coming, yet possibly
                # short enough to have beaten the wedge timer — which is how
                # this used to pass for an ordinary empty recording.
                self._report_failure(
                    f"the microphone never started in {duration:.0f}s "
                    f"— restart VibePaste",
                    wedged=True,
                )
            else:
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
        self._cancel_timers()

    # -- the microphone never arrived ----------------------------------

    def _on_device_error(self, error):
        self._abort(f"the microphone could not be opened: {error}")

    def _on_wedge_timeout(self):
        """The device thread is still inside CoreAudio after all this time.

        Nothing can be done about that — the HAL mutex is process-wide, so
        even a fresh thread would block on it. Give the recording up and say
        so loudly; a restart is the only cure.
        """
        if self.audio_recorder.is_open:
            return
        self._abort(
            f"the microphone did not respond within {self.wedged_seconds:.0f}s "
            f"— restart VibePaste",
            wedged=True,
        )

    def _abort(self, reason, wedged=False):
        """Take back a recording that never really started."""
        with self._lock:
            if not self._active:
                return
            self._active = False

        self._cancel_timers()
        self.audio_recorder.discard()
        self.overlay.hide()
        self._report_failure(reason, wedged)

    def _report_failure(self, reason, wedged=False):
        """Announce a lost recording as loudly as the app can.

        Shared by every path that loses one: the abort above, a press
        refused because the device is already wedged, and a stop whose
        microphone never delivered a frame. None of them can be worked
        around inside this process, so none of them may be quiet — a failure
        that only reached the log is what made a wedged microphone look like
        a bug in the overlay.

        Deliberately not folded into `_abort`: that returns early unless a
        recording is active, and a refused press never became one.
        """
        self.sounds.play_error()
        logger.error(f"Recording aborted: {reason}")
        print(f"\n⚠️  Recording aborted: {reason}")
        if self.on_failed is not None:
            try:
                self.on_failed(reason, wedged)
            except Exception as e:
                logger.error(f"on_failed handler raised: {e}", exc_info=True)

    # -- internals -----------------------------------------------------

    def _start_auto_stop_timer(self):
        """Hard cap, in case the overlay process isn't there to report one."""
        self._auto_stop_timer = self._timer(self.max_seconds, self._on_timeout)

    def _start_wedge_timer(self):
        """Catches a microphone that never opens at all."""
        self._wedge_timer = self._timer(
            self.wedged_seconds, self._on_wedge_timeout
        )

    @staticmethod
    def _timer(seconds, target):
        timer = threading.Timer(seconds, target)
        timer.daemon = True
        timer.start()
        return timer

    def _cancel_timers(self):
        for name in ("_auto_stop_timer", "_wedge_timer"):
            timer = getattr(self, name)
            if timer is not None:
                timer.cancel()
                setattr(self, name, None)

    def _on_timeout(self):
        logger.warning("Recording hit the hard time limit — stopping")
        self.stop()
