"""Owns every CoreAudio call, on a thread that is allowed to block forever.

CoreAudio's HAL mutex can deadlock. Measured on this machine, with the app
alive and looking perfectly healthy:

    hotkey-dispatch   Pa_OpenStream -> AudioDeviceCreateIOProcID
                                    -> HALB_Mutex::Lock  (blocked)
    audio-teardown    AudioOutputUnitStop -> StopIOProc
                                    -> HALB_Mutex::Lock  (blocked)

Nothing here can unwedge CoreAudio — the mutex is process-wide, so retrying
on a fresh thread blocks identically. What this module guarantees is that
the blocking call happens on a thread *nobody waits on*, so hotkeys, the
overlay and quitting all keep working while the microphone is lost.

An earlier fix moved only the stream *close* to a background thread. The
deadlock simply moved to the next open, which was still on the dispatch
thread, and every hotkey died again. That is why every device call lives
here now, not just the one that was slow last time.
"""

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

# A device call still running after this long is not slow, it is stuck. A
# healthy open takes ~70ms, and ~2.5s the very first time CoreAudio wakes up,
# so this has to clear that cold start with room to spare.
WEDGED_SECONDS = 6.0

# A recording this long that never received a single frame was talking to a
# microphone that is not coming. Sits above the ~2.5s cold wake, so a stop
# during a slow first open is not blamed on the device, and below
# WEDGED_SECONDS, so a stop that beats the wedge timer still gets reported —
# which is exactly the case that used to vanish with only "Nothing captured".
SILENT_START_SECONDS = 3.0

_SHUTDOWN = object()
_CLOSE = object()


class AudioDevice:
    """Serialises microphone open/close onto one dedicated thread."""

    def __init__(self, wedged_seconds=WEDGED_SECONDS):
        self.wedged_seconds = wedged_seconds
        self._queue = queue.Queue()
        self._thread = None
        self._stream = None
        self._busy_since = None
        self._busy_what = ""
        self._lock = threading.Lock()

    def open(self, factory, on_ready=None, on_error=None):
        """Ask for a stream. Returns immediately; never blocks.

        `factory` is called on the device thread and must return an already
        started stream. The callbacks are invoked there too, so they must
        not block either.
        """
        self._ensure_thread()
        self._queue.put((factory, on_ready, on_error))

    def close(self):
        """Tear the current stream down. Returns immediately; never blocks."""
        self._ensure_thread()
        self._queue.put(_CLOSE)

    def is_wedged(self):
        """True if a device call has been running implausibly long."""
        with self._lock:
            since = self._busy_since
        return since is not None and time.monotonic() - since > self.wedged_seconds

    def busy_with(self):
        """What the device thread is stuck on, for the log. '' when idle."""
        with self._lock:
            return self._busy_what if self._busy_since is not None else ""

    def shutdown(self):
        """Ask the thread to close anything open and exit.

        Deliberately does not join: if CoreAudio has wedged, the thread is
        never coming back and waiting for it would hang the quit path that
        releases the whisper-server.
        """
        if self._thread is not None:
            self._queue.put(_SHUTDOWN)
            self._thread = None

    # -- device thread ---------------------------------------------------

    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="audio-device", daemon=True
        )
        self._thread.start()

    def _loop(self):
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                self._close_current()
                return
            if item is _CLOSE:
                self._close_current()
                continue
            try:
                self._open(*item)
            except Exception as e:  # a dead device thread is a dead mic
                logger.error(f"Audio device error: {e}", exc_info=True)

    def _open(self, factory, on_ready, on_error):
        self._close_current()  # never hold two streams on one device
        self._begin("opening the microphone")
        try:
            stream = factory()
        except Exception as e:
            self._end()
            logger.error(f"Could not open the microphone: {e}")
            _safely(on_error, e)
            return
        self._end()
        self._stream = stream
        logger.info("Microphone open")
        _safely(on_ready)

    def _close_current(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return
        self._begin("closing the microphone")
        try:
            # abort() rather than stop(): pending buffers are of no interest
            # once the frames have been taken.
            stream.abort()
            stream.close()
        except Exception as e:
            logger.error(f"Error closing audio stream: {e}")
        finally:
            self._end()

    def _begin(self, what):
        with self._lock:
            self._busy_since = time.monotonic()
            self._busy_what = what

    def _end(self):
        with self._lock:
            self._busy_since = None
            self._busy_what = ""


def _safely(callback, *args):
    if callback is None:
        return
    try:
        callback(*args)
    except Exception as e:
        logger.error(f"Audio device callback failed: {e}", exc_info=True)
