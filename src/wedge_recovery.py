"""Decides whether a wedged microphone should cost the app its process.

CoreAudio's HAL mutex is process-wide, so a wedged microphone cannot be
recovered from the inside — not by a fresh thread, and not by the menu's
Stop→Start, which builds a new AudioDevice that blocks on the same mutex.
Only a new process gets the microphone back.

That makes this the one part of the app allowed to terminate it, and the
asymmetry is what every gate here protects: a restart costs about three
seconds, but a quit with no relaunch behind it costs the user the app. So
`attempt()` reads as a series of reasons *not* to quit, and only the last
line does.

Ordering that matters:

- The bundle is checked first, before anything is said or spent. Terminal
  mode has no .app to reopen and must be left completely alone.
- The relaunch is armed *before* quitting, because the reverse is
  unrecoverable — a spawn that fails after `quit_app()` leaves nothing
  running and nothing coming back.
- The re-check after draining is free evidence. If CoreAudio came back
  while we waited, there is nothing to recover and quitting is pure loss.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

TITLE = "🎙️ VibePaste"
DRAIN_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.5

RESTARTING = "Microphone lost — restarting VibePaste…"
RESTARTED = "Restarted after a microphone failure."
GAVE_UP = (
    "Restarting has not fixed the microphone. CoreAudio is wedged "
    "system-wide — try `killall coreaudiod`, or reboot."
)


class WedgeRecovery:
    """Restarts the app to recover a microphone this process cannot."""

    def __init__(self, ledger, restarter, pending, is_wedged, notify,
                 quit_app, drain_timeout_seconds=DRAIN_TIMEOUT_SECONDS,
                 poll_seconds=POLL_SECONDS, clock=time.monotonic,
                 sleep=time.sleep):
        """
        Args:
            pending: callable returning the transcription queue depth.
            is_wedged: callable re-asked after draining, so a microphone
                that recovered on its own keeps its process.
            quit_app: callable that quits through the normal path, so
                `before_quit` still releases the whisper-server.
        """
        self.ledger = ledger
        self.restarter = restarter
        self.pending = pending
        self.is_wedged = is_wedged
        self.notify = notify
        self.quit_app = quit_app
        self.drain_timeout_seconds = drain_timeout_seconds
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.sleep = sleep

        self._lock = threading.Lock()
        self._thread = None

    def on_wedge(self):
        """Signal a confirmed wedge. Returns immediately.

        Called from the wedge timer and the hotkey dispatch thread, where
        draining would be exactly the slow callback that gets the event tap
        disabled. At most one recovery runs at a time: three presses against
        a dead microphone must not mean three quits.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.info("Recovery already under way — ignoring")
                return
            self._thread = threading.Thread(
                target=self._run, name="wedge-recovery", daemon=True
            )
            self._thread.start()

    def wait(self, timeout=None):
        """Join the recovery thread. For tests and orderly shutdown."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def announce_restart(self):
        """Explain the blip, if this process is the result of one."""
        if self.ledger.just_restarted():
            logger.info("This process followed an automatic restart")
            self.notify(TITLE, RESTARTED)

    def attempt(self):
        """Run the sequence. True only if the app was actually quit."""
        if not self.restarter.can_restart():
            logger.error("Microphone wedged, but there is no bundle to "
                         "reopen — leaving the app running")
            return False

        if not self.ledger.allowed():
            logger.error("Restart cap reached — not restarting again")
            self.notify(TITLE, GAVE_UP)
            return False

        self.notify(TITLE, RESTARTING)
        self._drain()

        if not self.is_wedged():
            logger.info("Microphone recovered while draining — staying up")
            return False

        # Recorded before quitting: after `quit_app()` there is no process
        # left to write it, and the next one needs the count.
        self.ledger.record()
        if not self.restarter.restart():
            logger.error("Relaunch could not be armed — staying up")
            return False

        logger.info("Restarting to recover the microphone")
        self.quit_app()
        return True

    # -- internals -------------------------------------------------------

    def _run(self):
        try:
            self.attempt()
        except Exception as e:
            logger.error(f"Wedge recovery failed: {e}", exc_info=True)

    def _drain(self):
        """Let queued transcriptions finish, but never wait forever.

        A clip already on its way to the clipboard should still get there;
        a wedged transcription must not also cost the microphone recovery.
        """
        deadline = self.clock() + self.drain_timeout_seconds
        while self.pending() > 0 and self.clock() < deadline:
            self.sleep(self.poll_seconds)
