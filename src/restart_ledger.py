"""Counts automatic restarts, so the app cannot relaunch itself forever.

A wedged microphone is cured by a new process, but only when the fault
belongs to *this* process. When CoreAudio is wedged system-wide, a restart
fixes nothing and the next press wedges again — and at that point silently
relaunching is worse than saying so, because it hides the fact that the
answer is `killall coreaudiod` or a reboot.

The count is kept on disk and in wall-clock time on purpose: every restart
is a fresh process, so an in-memory count resets each time and a monotonic
clock starts over. Both would make the cap unable to bind on the one
sequence it exists to stop.

Every failure here fails *open* — a corrupt or unwritable ledger must cost
the user a count, never their recovery.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_RESTARTS = 3
WINDOW_SECONDS = 600
JUST_RESTARTED_SECONDS = 30


class RestartLedger:
    """A rolling count of recent automatic restarts."""

    def __init__(self, path, max_restarts=MAX_RESTARTS,
                 window_seconds=WINDOW_SECONDS, clock=time.time):
        self.path = Path(path)
        self.max_restarts = max_restarts
        self.window_seconds = window_seconds
        self.clock = clock

    def allowed(self):
        """True while the app may still restart itself."""
        return len(self._recent()) < self.max_restarts

    def record(self):
        """Note a restart about to happen, for the process that follows."""
        stamps = self._recent()
        stamps.append(self.clock())
        self._write(stamps)

    def just_restarted(self, within_seconds=JUST_RESTARTED_SECONDS):
        """True if this process is the result of a restart moments ago.

        How a fresh instance knows to explain the blip it just caused,
        rather than leaving the user to wonder where the app went.
        """
        stamps = self._recent()
        if not stamps:
            return False
        return abs(self.clock() - max(stamps)) <= within_seconds

    # -- internals -------------------------------------------------------

    def _recent(self):
        """Stamps inside the window, by distance rather than by sign.

        NTP and DST move wall-clock time in both directions; a stamp that
        ends up in the future must age out like any other, or it counts
        against the cap forever.
        """
        now = self.clock()
        return [t for t in self._read()
                if abs(now - t) <= self.window_seconds]

    def _read(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []  # missing or corrupt: no history, not an error
        if not isinstance(data, list):
            return []
        return [float(t) for t in data if isinstance(t, (int, float))]

    def _write(self, stamps):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(stamps))
        except OSError as e:
            # Losing the count is survivable. Raising here would take the
            # recovery down with it, which is the opposite of the point.
            logger.error(f"Could not record the restart: {e}")
