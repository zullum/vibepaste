"""Finds whisper-server processes by reading the process table.

A whisper-server holding large-v3 costs ~2.9GB of resident memory, and
nothing reclaims it when its parent dies: the child is reparented to launchd
and the idle reaper that would have unloaded it lived in the dead parent.
Fifteen such orphans once filled a 24GB machine and froze it.

The fix needs to answer "is one of ours already running, and on which port?"
across app restarts. The process table answers both: we spawn with the model
and the port on the command line, so a running server describes itself. A
state file would have to be kept in sync with reality; the process table *is*
reality, and it cannot go stale.

whisper-server's own /health endpoint reports {"status":"ok"} once the model
is loaded, but it does *not* say which model that is -- hence the split here
between identity (process table) and readiness (/health).
"""

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# "-m <path>" and "--port <n>" as whisper_server.py spells them.
_MODEL_RE = re.compile(r"-m\s+(\S+)")
_PORT_RE = re.compile(r"--port\s+(\d+)")


@dataclass(frozen=True)
class RunningServer:
    """A whisper-server found in the process table."""

    pid: int
    model_path: str
    port: int


def parse_servers(ps_output):
    """Extract whisper-server processes from `ps -axo pid=,command=` output.

    Lines that don't name a model *and* a port are skipped: a server we can't
    address is one we can neither adopt nor safely reason about.
    """
    servers = []
    for line in ps_output.splitlines():
        line = line.strip()
        if not line or "whisper-server" not in line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        # The `ps` line for a grep/pgrep that merely mentions whisper-server
        # matches the substring test above but carries no -m/--port pair.
        model = _MODEL_RE.search(command)
        port = _PORT_RE.search(command)
        if not model or not port:
            continue
        servers.append(
            RunningServer(pid=pid, model_path=model.group(1),
                          port=int(port.group(1)))
        )
    return servers


def running_servers(model_path=None, runner=None):
    """List live whisper-servers, optionally only those for one model.

    Returns an empty list if `ps` can't be run -- an unreadable process table
    must not stop transcription, it only costs us adoption.
    """
    runner = runner or _read_process_table
    try:
        output = runner()
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not read the process table: {e}")
        return []

    servers = parse_servers(output)
    if model_path is None:
        return servers
    wanted = str(model_path)
    return [s for s in servers if s.model_path == wanted]


def _read_process_table():
    return subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True, text=True, timeout=10,
    ).stdout


def server_is_healthy(port, timeout=2.0):
    """True when whisper-server on this port has finished loading its model.

    /health answers {"status":"ok"} when loaded and {"status":"loading model"}
    while still reading the weights off disk. Anything else -- a stranger on
    the port, a wedged server -- is not adoptable.
    """
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
    try:
        return json.loads(payload).get("status") == "ok"
    except ValueError:
        return False
