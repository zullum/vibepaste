"""Transcription with escalating retries.

Each attempt uses a *different* mechanism so a wedged server cannot produce
three identical failures:

    1. resident whisper-server
    2. whisper-server, restarted first
    3. a plain whisper-cli fork (no server involved)

Timeouts scale with audio duration — a fixed 60s cap silently dropped every
long recording.
"""

import logging
import subprocess
import time
from pathlib import Path

from src.whisper_server import WhisperServer

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Every attempt failed. Carries the per-attempt reasons."""

    def __init__(self, reasons):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class Transcriber:
    """Transcribes WAV files, preferring a resident server over a CLI fork."""

    def __init__(self, whisper_cli_path, server_path, threads=4,
                 attempts=3, timeout_base=30, timeout_per_second=4.0,
                 startup_timeout=120, idle_unload_seconds=600):
        self.whisper_cli_path = Path(whisper_cli_path)
        self.server_path = Path(server_path)
        self.threads = threads
        self.attempts = attempts
        self.timeout_base = timeout_base
        self.timeout_per_second = timeout_per_second
        self.startup_timeout = startup_timeout
        self.idle_unload_seconds = idle_unload_seconds
        self._servers = {}

    def timeout_for(self, duration_seconds):
        """Per-attempt timeout budget for a clip of this length."""
        return self.timeout_base + self.timeout_per_second * duration_seconds

    def transcribe(self, wav_path, model_path, language, duration_seconds):
        """Transcribe a WAV, escalating through mechanisms on failure.

        Returns:
            The transcribed text.

        Raises:
            TranscriptionError: if every attempt failed.
        """
        timeout = self.timeout_for(duration_seconds)
        reasons = []

        for attempt in range(1, self.attempts + 1):
            strategy = self._strategy_for(attempt)
            logger.info(
                f"Transcription attempt {attempt}/{self.attempts} via {strategy}"
            )
            try:
                text = self._run_strategy(
                    strategy, wav_path, model_path, language, timeout
                )
            except Exception as e:
                text = None
                reasons.append(f"attempt {attempt} ({strategy}): {e}")
                logger.error(f"Attempt {attempt} raised: {e}")
            else:
                # Whitespace-only output is a failure, not a result to paste.
                text = text.strip() if text else None
                if text:
                    logger.info(
                        f"Transcribed {len(text)} chars on attempt {attempt} "
                        f"via {strategy}"
                    )
                    return text
                reasons.append(f"attempt {attempt} ({strategy}): empty result")

            if attempt < self.attempts:
                time.sleep(0.5 * attempt)

        raise TranscriptionError(reasons)

    def shutdown(self):
        """Stop every resident server."""
        for server in self._servers.values():
            server.stop()
        self._servers.clear()

    # -- internals -----------------------------------------------------

    def _strategy_for(self, attempt):
        if attempt == 1:
            return "server"
        if attempt == 2:
            return "server-restart"
        return "cli"

    def _run_strategy(self, strategy, wav_path, model_path, language, timeout):
        if strategy == "cli":
            return self._transcribe_via_cli(
                wav_path, model_path, language, timeout
            )

        server = self._server_for(model_path)
        if server is None:
            return None
        if strategy == "server-restart":
            server.stop()
        return server.transcribe(wav_path, language, timeout)

    def _server_for(self, model_path):
        """Get (creating if needed) the resident server for this model."""
        key = str(model_path)
        if key not in self._servers:
            if not self.server_path.exists():
                logger.warning(
                    "whisper-server binary missing, falling back to CLI only"
                )
                return None
            self._servers[key] = WhisperServer(
                server_path=self.server_path,
                model_path=model_path,
                threads=self.threads,
                startup_timeout=self.startup_timeout,
                idle_unload_seconds=self.idle_unload_seconds,
            )
        return self._servers[key]

    def _transcribe_via_cli(self, wav_path, model_path, language, timeout):
        """Fork whisper-cli and read the transcript from stdout.

        Reading stdout avoids the old round-trip through a .txt file, which
        failed silently whenever the file didn't appear where we expected.
        """
        if not self.whisper_cli_path.exists():
            raise FileNotFoundError(
                f"whisper-cli not found at {self.whisper_cli_path}"
            )

        cmd = [
            str(self.whisper_cli_path),
            "-m", str(model_path),
            "-f", str(wav_path),
            "-l", language,
            "-t", str(self.threads),
            "-nt",   # no timestamps — stdout is the transcript
            "-np",   # no progress/system prints
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"whisper-cli exited {result.returncode}: "
                f"{result.stderr.strip()[:200]}"
            )
        return result.stdout.strip() or None
