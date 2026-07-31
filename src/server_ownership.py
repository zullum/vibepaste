"""Remembers which whisper-server processes we started.

Adoption and killing need different levels of proof. Adopting a server is a
read: the worst case is that we use one somebody else started, which is
harmless because it is the same binary serving the same model. Killing is
destructive -- terminating a whisper-server that a developer started by hand
for other work would be a background app silently destroying their state.

So discovery may look at the whole process table, but the sweep may only
touch pids recorded here. If this file is lost the failure is benign: we stop
recognising a server as ours and simply adopt it instead of reclaiming it.
"""

import json
import logging

from src.process_util import process_exists

logger = logging.getLogger(__name__)


class OwnershipRegistry:
    """The set of whisper-server pids this app is allowed to kill."""

    def __init__(self, path):
        self.path = path

    def pids(self):
        """Recorded pids that are still alive, pruning the ones that aren't."""
        recorded = self._read()
        alive = [pid for pid in recorded if process_exists(pid)]
        if len(alive) != len(recorded):
            self._write(alive)
        return alive

    def record(self, pid):
        """Claim a pid.

        Called immediately after spawning, *before* waiting for the model to
        load: large-v3 takes ~9s to become ready, and a crash inside that
        window would otherwise leave 2.9GB we no longer have permission to
        reclaim.
        """
        pids = self._read()
        if pid not in pids:
            pids.append(pid)
            self._write(pids)

    def forget(self, pid):
        """Release a pid we have stopped."""
        pids = [p for p in self._read() if p != pid]
        self._write(pids)

    def owns(self, pid):
        return pid in self._read()

    # -- internals -----------------------------------------------------

    def _read(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [p for p in data if isinstance(p, int)]

    def _write(self, pids):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(pids))
        except OSError as e:
            logger.warning(f"Could not update the whisper-server registry: {e}")
