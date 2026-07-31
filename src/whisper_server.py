"""Lifecycle and HTTP client for a resident whisper-server process.

Forking whisper-cli reloads the model every time — 2.2s for large-v3, 0.6s
for turbo. A resident server removes that: a warm /inference call on a 5s
clip takes ~0.6s. One server per model, started lazily and unloaded after
an idle period so the RAM comes back when dictation stops.
"""

import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from src.http_util import build_multipart, find_free_port, port_is_open

logger = logging.getLogger(__name__)

REAP_INTERVAL_SECONDS = 30


class WhisperServer:
    """A single resident whisper-server bound to one model."""

    def __init__(self, server_path, model_path, threads=4,
                 startup_timeout=120, idle_unload_seconds=600):
        self.server_path = Path(server_path)
        self.model_path = Path(model_path)
        self.threads = threads
        self.startup_timeout = startup_timeout
        self.idle_unload_seconds = idle_unload_seconds

        self._process = None
        self._port = None
        self._lock = threading.Lock()
        self._last_used = time.monotonic()
        self._reaper = None
        self._stopping = False

    @property
    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def ensure_started(self):
        """Start the server if it isn't already up. Returns True when ready."""
        with self._lock:
            if self.is_running and port_is_open(self._port):
                return True
            self._stop_locked()
            return self._start_locked()

    def transcribe(self, wav_path, language, timeout):
        """POST the WAV to /inference.

        Returns:
            Transcribed text, or None if the request failed or came back empty.
        """
        if not self.ensure_started():
            return None

        self._last_used = time.monotonic()
        body, content_type = build_multipart(
            wav_path,
            {
                "response_format": "json",
                "language": language,
                "temperature": "0.0",
            },
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/inference",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.error(f"whisper-server request failed: {e}")
            return None
        finally:
            self._last_used = time.monotonic()

        return self._extract_text(payload)

    @staticmethod
    def _extract_text(payload):
        try:
            data = json.loads(payload)
        except ValueError:
            logger.error(f"whisper-server returned non-JSON: {payload[:200]}")
            return None
        return (data.get("text") or "").strip() or None

    def stop(self):
        """Terminate the server and release its memory."""
        with self._lock:
            self._stopping = True
            self._stop_locked()

    # -- internals, all called with self._lock held --------------------

    def _start_locked(self):
        if not self.server_path.exists():
            logger.error(f"whisper-server not found at {self.server_path}")
            return False
        if not self.model_path.exists():
            logger.error(f"Model not found at {self.model_path}")
            return False

        self._port = find_free_port()
        cmd = [
            str(self.server_path), "-m", str(self.model_path),
            "--port", str(self._port), "--host", "127.0.0.1",
            "-t", str(self.threads), "-nt",
        ]
        logger.info(f"Starting whisper-server for {self.model_path.name} "
                    f"on :{self._port}")
        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as e:
            logger.error(f"Could not spawn whisper-server: {e}")
            self._process = None
            return False

        if not self._wait_until_ready():
            self._stop_locked()
            return False

        self._stopping = False
        self._last_used = time.monotonic()
        self._start_reaper()
        logger.info(f"whisper-server ready for {self.model_path.name}")
        return True

    def _wait_until_ready(self):
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                logger.error(
                    f"whisper-server exited during startup "
                    f"(code {self._process.returncode})"
                )
                return False
            if port_is_open(self._port):
                return True
            time.sleep(0.25)
        logger.error("whisper-server did not become ready in time")
        return False

    def _stop_locked(self):
        if self._process is None:
            return
        logger.info(f"Stopping whisper-server for {self.model_path.name}")
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        except OSError as e:
            logger.error(f"Error stopping whisper-server: {e}")
        finally:
            self._process = None
            self._port = None

    def _start_reaper(self):
        """Background thread that unloads the model once it goes unused."""
        if self._reaper is not None and self._reaper.is_alive():
            return

        def reap():
            while True:
                time.sleep(REAP_INTERVAL_SECONDS)
                with self._lock:
                    if self._stopping or not self.is_running:
                        return
                    idle = time.monotonic() - self._last_used
                    if idle >= self.idle_unload_seconds:
                        logger.info(
                            f"Unloading {self.model_path.name} after "
                            f"{idle:.0f}s idle"
                        )
                        self._stop_locked()
                        return

        self._reaper = threading.Thread(
            target=reap, name="whisper-reaper", daemon=True
        )
        self._reaper.start()
