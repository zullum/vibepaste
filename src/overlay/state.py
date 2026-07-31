"""Shared display state for the overlay UI process.

The stdin reader thread writes it, the AppKit main thread polls it. A plain
dict behind a lock keeps AppKit objects off the reader thread entirely.
"""

import sys
import threading
import time

_state = {
    "mode": "hidden",          # hidden | record | processing
    "started_at": 0.0,
    "warn_seconds": 60.0,
    "max_seconds": 120.0,
}
_lock = threading.Lock()


def read_state():
    """Snapshot of the current display state."""
    with _lock:
        return dict(_state)


def set_mode(mode):
    with _lock:
        _state["mode"] = mode


def apply_command(line):
    """Apply one stdin command.

    Returns:
        False if the UI should exit, True otherwise.
    """
    parts = line.strip().split()
    if not parts:
        return True
    command = parts[0]

    with _lock:
        if command == "record":
            _state["mode"] = "record"
            _state["started_at"] = time.monotonic()
            if len(parts) >= 3:
                _state["warn_seconds"] = float(parts[1])
                _state["max_seconds"] = float(parts[2])
        elif command == "processing":
            _state["mode"] = "processing"
        elif command == "hide":
            _state["mode"] = "hidden"
        elif command == "quit":
            return False
    return True


def emit(message):
    """Send a message back to the parent process. Never raises."""
    try:
        sys.stdout.write(message + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
