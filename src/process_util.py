"""Process existence and termination helpers.

Shared by the whisper-server ownership registry and its reclaim path, which
both have to reason about processes this app no longer has a Popen handle
for -- an orphan reparented to launchd is addressable only by pid.
"""

import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)


def process_exists(pid):
    """True if the pid is live. Signal 0 checks without touching it."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_process(process, grace_seconds=5):
    """Stop a child we still hold a Popen for, escalating if it lingers."""
    try:
        process.terminate()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace_seconds)
    except OSError as e:
        logger.error(f"Error stopping pid {process.pid}: {e}")


def _reap_if_child(pid):
    """Collect the exit status if this pid happens to be our child.

    A killed child stays a zombie -- and so still answers signal 0 -- until
    someone reaps it. Without this, killing a server we spawned looks like a
    process refusing to die, and burns the whole grace period before
    escalating pointlessly. That delay lands inside applicationWillTerminate,
    which macOS watchdogs, so it can eat the cleanup it was meant to perform.
    """
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass  # not our child; launchd will reap it


def kill_pid(pid, grace_seconds=5):
    """SIGTERM, then SIGKILL if it is still there after the grace period.

    whisper-server handles SIGTERM normally -- unlike VibePaste itself, whose
    AppKit run loop ignores it outright -- so the escalation rarely gets past
    the first step. It exists for a server wedged mid-inference.
    """
    try:
        os.kill(pid, 15)
    except OSError:
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        _reap_if_child(pid)
        if not process_exists(pid):
            return
        time.sleep(0.2)

    logger.warning(f"pid {pid} ignored SIGTERM, sending SIGKILL")
    try:
        os.kill(pid, 9)
    except OSError:
        pass
