"""Spawning a whisper-server process and waiting for it to serve.

Separated from WhisperServer so the *policy* (which server should exist) is
readable apart from the *mechanism* (how one is launched).
"""

import logging
import subprocess
import time

from src.http_util import port_is_open

logger = logging.getLogger(__name__)


def spawn_server(server_path, model_path, port, threads):
    """Launch whisper-server. Returns the Popen, or None if it wouldn't start.

    The model path and port are passed as command-line arguments, which is
    also how a later run recognises this process in the process table — see
    server_discovery. Changing this command's shape breaks adoption.
    """
    cmd = [
        str(server_path), "-m", str(model_path),
        "--port", str(port), "--host", "127.0.0.1",
        "-t", str(threads), "-nt",
    ]
    logger.info(f"Starting whisper-server for {model_path.name} on :{port}")
    try:
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError as e:
        logger.error(f"Could not spawn whisper-server: {e}")
        return None


def wait_until_ready(process, port, timeout):
    """Block until the server accepts connections, or give up.

    whisper-server loads its model before it starts listening, so an open
    port means the model is resident and ready to serve.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            logger.error(f"whisper-server exited during startup "
                         f"(code {process.returncode})")
            return False
        if port_is_open(port):
            return True
        time.sleep(0.25)
    logger.error("whisper-server did not become ready in time")
    return False
