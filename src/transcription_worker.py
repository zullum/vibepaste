"""Serialised transcribe-then-paste pipeline.

Recording must never wait on transcription, so saved recordings are queued
here and handled one at a time on a background thread. Queueing (rather
than rejecting) means a second recording can start while the first is
still being transcribed, and the results paste in the order they were spoken.
"""

import logging
import queue
import threading
import time

from src.transcriber import TranscriptionError

logger = logging.getLogger(__name__)

_SHUTDOWN = object()


class TranscriptionJob:
    """One saved recording waiting to be transcribed."""

    def __init__(self, wav_path, model_path, language, duration):
        self.wav_path = wav_path
        self.model_path = model_path
        self.language = language
        self.duration = duration


class TranscriptionWorker:
    """Consumes TranscriptionJobs: transcribe, store the text, paste it."""

    def __init__(self, transcriber, store, paster, sounds,
                 on_queue_change=None):
        self.transcriber = transcriber
        self.store = store
        self.paster = paster
        self.sounds = sounds
        self.on_queue_change = on_queue_change

        self._queue = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._loop, name="transcriber", daemon=True
        )
        self._thread.start()

    @property
    def pending(self):
        with self._lock:
            return self._pending

    def submit(self, job):
        """Queue a job and report the new queue depth."""
        with self._lock:
            self._pending += 1
        self._queue.put(job)
        self._notify()

    def shutdown(self):
        self._queue.put(_SHUTDOWN)

    # -- internals -----------------------------------------------------

    def _notify(self):
        if self.on_queue_change:
            try:
                self.on_queue_change()
            except Exception as e:
                logger.error(f"Queue-change handler failed: {e}", exc_info=True)

    def _loop(self):
        while True:
            job = self._queue.get()
            if job is _SHUTDOWN:
                return
            try:
                self._process(job)
            except Exception as e:
                logger.error(f"Unhandled error processing job: {e}",
                             exc_info=True)
            finally:
                with self._lock:
                    self._pending -= 1
                self._notify()

    def _process(self, job):
        print("🎙️  Transcribing...")
        started = time.monotonic()
        try:
            text = self.transcriber.transcribe(
                job.wav_path, job.model_path, job.language, job.duration
            )
        except TranscriptionError as e:
            logger.error(f"Transcription failed: {e}")
            self.sounds.play_error()
            print("❌ Transcription failed on every attempt.")
            print(f"   Audio kept at: {job.wav_path}")
            for reason in e.reasons:
                print(f"   - {reason}")
            return

        elapsed = time.monotonic() - started
        print(f"✅ ({elapsed:.1f}s) {text}")
        self.store.save_transcript(job.wav_path, text)
        self._deliver(text)

    def _deliver(self, text):
        result = self.paster.paste_text(text)
        if result.pasted:
            print("📋 Pasted!")
        elif result.copied:
            print(f"📋 In clipboard — press Cmd+V ({result.reason})")
        else:
            self.sounds.play_error()
            print(f"❌ Could not deliver text ({result.reason})")
