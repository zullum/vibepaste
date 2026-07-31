"""Drives the persistent overlay UI process.

The old design forked a fresh Python + PyObjC interpreter and rewrote a
generated script file on every single show, costing hundreds of
milliseconds each time and leaving orphan processes behind. Here one
process is started once and told what to display over stdin.
"""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

UI_APP_PATH = Path(__file__).with_name("ui_app.py")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class OverlayController:
    """Starts the overlay process and sends it display commands."""

    def __init__(self, on_auto_stop=None):
        """
        Args:
            on_auto_stop: called (on a worker thread) when the UI reports the
                recording hit its hard time limit.
        """
        self.on_auto_stop = on_auto_stop
        self._process = None
        self._lock = threading.Lock()
        self._reader = None

    def start(self):
        """Launch the UI process. Safe to call repeatedly."""
        with self._lock:
            if self._is_alive():
                return True
            return self._start_locked()

    def show_recording(self, warn_seconds, max_seconds):
        """Show pulsating dots plus the duration bar."""
        self._send(f"record {warn_seconds} {max_seconds}")

    def show_processing(self):
        """Swap to the transcription spinner."""
        self._send("processing")

    def hide(self):
        """Hide the overlay without tearing the process down."""
        self._send("hide")

    def stop(self):
        """Terminate the UI process."""
        with self._lock:
            if self._process is None:
                return
            logger.info("Stopping overlay process")
            try:
                if self._process.poll() is None:
                    self._write_locked("quit")
                    try:
                        self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._process.terminate()
                        self._process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._process.kill()
                except OSError:
                    pass
            finally:
                self._process = None

    # -- internals -----------------------------------------------------

    def _is_alive(self):
        return self._process is not None and self._process.poll() is None

    def _start_locked(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(UI_APP_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as e:
            logger.error(f"Could not start overlay process: {e}")
            self._process = None
            return False

        self._reader = threading.Thread(
            target=self._read_loop, args=(self._process,),
            name="overlay-reader", daemon=True,
        )
        self._reader.start()
        logger.info(f"Overlay process started (pid {self._process.pid})")
        return True

    def _send(self, command):
        """Write a command, restarting the UI process if it has died."""
        with self._lock:
            if not self._is_alive():
                logger.warning("Overlay process not running, restarting")
                self._process = None
                if not self._start_locked():
                    return
            self._write_locked(command)

    def _write_locked(self, command):
        try:
            self._process.stdin.write(command + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError, AttributeError) as e:
            logger.error(f"Could not send '{command}' to overlay: {e}")

    def _read_loop(self, process):
        """Relay messages the UI sends back, e.g. the hard time-limit stop."""
        try:
            for line in process.stdout:
                if line.strip() == "auto_stop" and self.on_auto_stop:
                    try:
                        self.on_auto_stop()
                    except Exception as e:
                        logger.error(f"auto_stop handler failed: {e}",
                                     exc_info=True)
        except (OSError, ValueError):
            pass
