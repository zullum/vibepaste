"""Lifecycle of a resident whisper-server process.

Forking whisper-cli reloads the model every time — 2.2s for large-v3, 0.6s
for turbo. A resident server removes that: a warm /inference call on a 5s
clip takes ~0.6s.

**A server can outlive this process, and that is handled rather than
ignored.** whisper-server never reads stdin, so unlike the overlay it does
not die with its parent: a crash or kill -9 leaves it reparented to launchd
holding ~2.9GB for large-v3, with the idle reaper that would have unloaded
it dead alongside the parent. Fifteen such orphans once filled a 24GB
machine and froze it. `_acquire_locked` is where that is now prevented:
adopt an existing server, reclaim a wedged one of ours, or refuse — but
never load a second copy of the same weights.
"""

import logging
import threading
import time
from pathlib import Path

from src.http_util import find_free_port, port_is_open
from src.process_util import kill_pid, terminate_process
from src.server_acquisition import ADOPT, REFUSE, decide
from src.server_discovery import running_servers, server_is_healthy
from src.server_ownership import OwnershipRegistry
from src.server_reaper import start_idle_reaper
from src.server_spawn import spawn_server, wait_until_ready
from src.whisper_client import transcribe_via_http

logger = logging.getLogger(__name__)

OWNERSHIP_PATH = Path.home() / ".vibepaste" / "whisper-owned.json"


class WhisperServer:
    """A single resident whisper-server bound to one model."""

    def __init__(self, server_path, model_path, threads=4,
                 startup_timeout=120, idle_unload_seconds=600,
                 ownership=None):
        self.server_path = Path(server_path)
        self.model_path = Path(model_path)
        self.threads = threads
        self.startup_timeout = startup_timeout
        self.idle_unload_seconds = idle_unload_seconds
        self.ownership = ownership or OwnershipRegistry(OWNERSHIP_PATH)

        self._process = None      # set when we spawned it ourselves
        self._adopted_pid = None  # set when we adopted an existing one
        self._port = None
        self._lock = threading.Lock()
        self._last_used = time.monotonic()
        self._reaper = None
        self._stopping = False

    def ensure_started(self):
        """Adopt, reclaim or spawn a server. Returns True when ready."""
        with self._lock:
            if self._is_serving():
                return True
            self._release_locked()
            return self._acquire_locked()

    def transcribe(self, wav_path, language, timeout):
        """Transcribe a WAV, starting or adopting a server as needed."""
        if not self.ensure_started():
            return None
        self._last_used = time.monotonic()
        try:
            return transcribe_via_http(self._port, wav_path, language, timeout)
        finally:
            self._last_used = time.monotonic()

    def stop(self):
        """Terminate the server and release its memory."""
        with self._lock:
            self._stopping = True
            self._release_locked()

    def reap_if_idle(self):
        """Unload the model if unused. True when the reaper should stop."""
        with self._lock:
            if self._stopping or not self._is_serving():
                return True
            idle = time.monotonic() - self._last_used
            if idle < self.idle_unload_seconds:
                return False
            logger.info(
                f"Unloading {self.model_path.name} after {idle:.0f}s idle"
            )
            self._release_locked()
            return True

    # -- acquiring, all called with self._lock held ---------------------

    def _acquire_locked(self):
        existing = running_servers(self.model_path)
        if len(existing) > 1:
            logger.warning(f"{len(existing)} whisper-servers hold "
                           f"{self.model_path.name} — one model copy each")

        decision = decide(existing, server_is_healthy, self.ownership.owns)
        if decision.action == ADOPT:
            return self._adopt_locked(decision.server)

        for pid in decision.reclaim:
            logger.warning(f"Reclaiming unresponsive whisper-server pid {pid}")
            kill_pid(pid)
            self.ownership.forget(pid)

        if decision.action == REFUSE:
            logger.error(f"Refusing a second whisper-server for "
                         f"{self.model_path.name}: pid {decision.blocker.pid} "
                         f"holds it, unresponsive and not ours. Using the CLI.")
            return False
        return self._start_locked()

    def _adopt_locked(self, server):
        self._adopted_pid = server.pid
        self._port = server.port
        self._stopping = False
        self._last_used = time.monotonic()
        self._start_reaper()
        logger.info(f"Adopted whisper-server pid {server.pid} on "
                    f":{server.port} for {self.model_path.name} "
                    f"(no second copy of the model loaded)")
        return True

    def _start_locked(self):
        if not self.server_path.exists():
            logger.error(f"whisper-server not found at {self.server_path}")
            return False
        if not self.model_path.exists():
            logger.error(f"Model not found at {self.model_path}")
            return False

        self._port = find_free_port()
        self._process = spawn_server(
            self.server_path, self.model_path, self._port, self.threads
        )
        if self._process is None:
            return False

        # Claim it before the model finishes loading: a crash inside those
        # ~9 seconds would otherwise strand memory we are not allowed to kill.
        self.ownership.record(self._process.pid)

        if not wait_until_ready(self._process, self._port,
                                self.startup_timeout):
            self._release_locked()
            return False

        self._stopping = False
        self._last_used = time.monotonic()
        self._start_reaper()
        logger.info(f"whisper-server ready for {self.model_path.name} "
                    f"(pid {self._process.pid})")
        return True

    # -- releasing ------------------------------------------------------

    def _is_serving(self):
        if self._port is None:
            return False
        if self._process is not None and self._process.poll() is not None:
            return False
        return port_is_open(self._port)

    def _release_locked(self):
        """Stop the server we hold, if we are allowed to stop it."""
        if self._process is not None:
            self._terminate_own_process()
        elif self._adopted_pid is not None:
            self._release_adopted()
        self._process = None
        self._adopted_pid = None
        self._port = None

    def _terminate_own_process(self):
        pid = self._process.pid
        logger.info(f"Stopping whisper-server for {self.model_path.name}")
        terminate_process(self._process)
        self.ownership.forget(pid)

    def _release_adopted(self):
        pid = self._adopted_pid
        if not self.ownership.owns(pid):
            # Somebody else's server: ours to use, never ours to kill.
            logger.info(f"Releasing whisper-server pid {pid} (not ours)")
            return
        logger.info(f"Stopping adopted whisper-server pid {pid}")
        kill_pid(pid)
        self.ownership.forget(pid)

    def _start_reaper(self):
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._reaper = start_idle_reaper(self)
