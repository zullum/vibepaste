"""Runs hotkey callbacks off the tap thread, and survives one that hangs.

The tap callback may only touch memory and queue work (see
src/keyboard_listener.py), so something else has to run the callbacks. That
something used to be a single worker thread — and a callback that never
returned took every later hotkey down with it: presses kept queueing while
the one worker sat blocked forever.

That is not theoretical. It was measured twice, both times inside CoreAudio:

    hotkey-dispatch  Pa_OpenStream -> AudioDeviceCreateIOProcID
                                   -> HALB_Mutex::Lock  (blocked forever)

The known cause now lives on its own thread (src/audio_device.py). This is
the net for the one we have not found yet: a callback that overruns gets a
replacement worker so the queue drains again. The stuck worker is left
where it is — it is blocked in C and cannot be killed — so replacements are
capped, or a permanently wedged callback would spawn threads without end.
"""

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

# Callbacks here start recordings and stop them. Well under a second in
# normal use; this is the point where "slow" becomes "never coming back".
STUCK_CALLBACK_SECONDS = 8.0
CHECK_INTERVAL_SECONDS = 1.0
# Each replacement leaks the thread it replaces, so this is deliberately
# small: it buys a working hotkey, not an unbounded supply of threads.
MAX_WORKERS = 4

_SHUTDOWN = object()


class HotkeyDispatcher:
    """A worker pool of one that grows only when a callback wedges."""

    def __init__(self, resolve, stuck_seconds=STUCK_CALLBACK_SECONDS,
                 max_workers=MAX_WORKERS,
                 check_interval=CHECK_INTERVAL_SECONDS):
        """
        Args:
            resolve: name -> callable, or None if nothing is registered.
        """
        self.resolve = resolve
        self.stuck_seconds = stuck_seconds
        self.max_workers = max_workers
        self.check_interval = check_interval
        self.queue = queue.Queue()
        self._lock = threading.Lock()
        self._running = {}       # thread name -> (item, started_at)
        self._workers = 0
        self._stopping = threading.Event()
        self._guard = None

    def start(self):
        self._stopping.clear()
        self._add_worker()
        self._guard = threading.Thread(
            target=self._guard_loop, name="hotkey-guard", daemon=True
        )
        self._guard.start()

    def stop(self):
        self._stopping.set()
        with self._lock:
            workers = self._workers
        for _ in range(max(workers, 1)):
            self.queue.put(_SHUTDOWN)
        self._guard = None

    def submit(self, item):
        self.queue.put(item)

    def worker_count(self):
        with self._lock:
            return self._workers

    def stuck_for(self):
        """Longest time a callback has been running, or 0.0 if none is."""
        now = time.monotonic()
        with self._lock:
            if not self._running:
                return 0.0
            return now - min(started for _, started in self._running.values())

    # -- workers ---------------------------------------------------------

    def _add_worker(self):
        with self._lock:
            if self._workers >= self.max_workers:
                return False
            self._workers += 1
            index = self._workers
        threading.Thread(
            target=self._loop, name=f"hotkey-dispatch-{index}", daemon=True
        ).start()
        return True

    def _loop(self):
        name = threading.current_thread().name
        while True:
            item = self.queue.get()
            if item is _SHUTDOWN:
                with self._lock:
                    self._workers -= 1
                return
            callback = self.resolve(item)
            if callback is None:
                continue
            with self._lock:
                self._running[name] = (item, time.monotonic())
            try:
                callback(item)
            except Exception as e:
                logger.error(f"Error in hotkey callback '{item}': {e}",
                             exc_info=True)
            finally:
                with self._lock:
                    self._running.pop(name, None)

    def _guard_loop(self):
        while not self._stopping.wait(self.check_interval):
            try:
                self._check()
            except Exception as e:
                logger.error(f"Hotkey dispatch guard error: {e}", exc_info=True)

    def _check(self):
        now = time.monotonic()
        with self._lock:
            stuck = [
                (name, item, now - started)
                for name, (item, started) in self._running.items()
                if now - started > self.stuck_seconds
            ]
            idle = len(self._running) < self._workers
        if not stuck or idle:
            return  # something is still free to take the next hotkey
        name, item, seconds = stuck[0]
        if self._add_worker():
            logger.error(
                "Hotkey callback '%s' has been running %.0fs on %s — started "
                "a replacement worker so hotkeys keep firing",
                item, seconds, name,
            )
        else:
            logger.error(
                "Hotkey callback '%s' stuck %.0fs and no workers left "
                "(max %d) — hotkeys are dead, restart VibePaste",
                item, seconds, self.max_workers,
            )
