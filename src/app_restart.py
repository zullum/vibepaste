"""Arms a relaunch that outlives the process arming it.

This process cannot restart itself. `os.execv` replaces the image that
LaunchServices registered, after which the app never completes check-in and
NSStatusBar hands back a status item zero pixels high that is never drawn —
the same failure `tools/launcher.c` exists to avoid, which is why the bundle
embeds the interpreter instead of exec'ing one.

So the relaunch is a detached `/bin/sh` that waits for our PID to disappear
and then reopens the bundle. Waiting is not optional: LaunchServices will
not start a second instance while the first is alive, and the single-instance
PID-file guard in the launcher would refuse it anyway. Once we are gone that
guard sees a PID that no longer exists and lets the new process through.

The rule the callers depend on: `restart()` returns True only when a helper
is genuinely armed. Quitting on anything less leaves the user with no app,
which is a far worse outcome than the wedged microphone being recovered.
"""

import logging
import os
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Long enough for LaunchServices to notice the old instance is gone. Without
# it `open` can attach to the dying registration instead of starting a new
# process, and nothing comes back.
DEREGISTER_GRACE_SECONDS = 0.5
POLL_SECONDS = 0.2

# The helper is armed *before* the quit, so a quit that never arrives leaves
# it polling. Unbounded that is a process spinning until logout, and
# reopening a bundle that never died would only activate the running app.
# Bounded, the worst case is a helper that quietly expires.
GIVE_UP_SECONDS = 60.0

# Matches CFBundleIdentifier in VibePaste.app's Info.plist. The relaunch
# target is identified by this and not by the path's shape — see
# resolve_bundle_path() for the .app that shape-matching picks up instead.
BUNDLE_IDENTIFIER = "com.vibepaste.app"


class AppRestarter:
    """Spawns a detached helper that reopens the bundle once we exit."""

    def __init__(self, bundle_path, pid=None, spawn=subprocess.Popen,
                 verify_exists=True):
        self.bundle_path = Path(bundle_path) if bundle_path else None
        self.pid = os.getpid() if pid is None else pid
        self.spawn = spawn
        self.verify_exists = verify_exists

    def can_restart(self):
        """True only for a real .app we can actually reopen.

        Terminal mode has no bundle: `run.sh` is a foreground process the
        developer is watching, and quitting it automatically would close the
        very thing they are reading.
        """
        if self.bundle_path is None:
            return False
        if self.bundle_path.suffix != ".app":
            return False
        return self.bundle_path.exists() if self.verify_exists else True

    def restart(self):
        """Arm the relaunch. True only if the helper is really running."""
        if not self.can_restart():
            logger.error(
                "No bundle to reopen (%s) — not restarting", self.bundle_path
            )
            return False

        target = shlex.quote(str(self.bundle_path))
        attempts = int(GIVE_UP_SECONDS / POLL_SECONDS)
        script = (
            f"n=0; "
            f"while kill -0 {self.pid} 2>/dev/null; do "
            f"sleep {POLL_SECONDS}; "
            f"n=$((n+1)); "
            f"[ $n -ge {attempts} ] && exit 0; "
            f"done; "
            f"sleep {DEREGISTER_GRACE_SECONDS}; "
            f"open -a {target}"
        )
        try:
            self.spawn(
                ["/bin/sh", "-c", script],
                # Its parent is about to die; without its own session it dies
                # too, and the relaunch never happens.
                start_new_session=True,
                # The launcher points stdout at vibepaste_debug.log. Inheriting
                # it would keep the dead process's log file open.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as e:
            logger.error(f"Could not arm the relaunch: {e}")
            return False

        logger.info("Relaunch armed for %s", self.bundle_path)
        return True


def _main_bundle():  # pragma: no cover - AppKit is not in the tests
    try:
        from AppKit import NSBundle
    except ImportError:
        return None
    try:
        return NSBundle.mainBundle()
    except Exception as e:
        logger.error(f"Could not read the main bundle: {e}")
        return None


def resolve_bundle_path(main_bundle=None):
    """Our own .app, or None if this process was not launched from it.

    Identity is checked by bundle identifier, never by the path looking like
    an .app. Run from a terminal, `NSBundle.mainBundle()` is the *interpreter's*
    Python.app — a real, existing .app — so a shape-based check happily arms
    `open -a Python.app` on quit. It is the same substitution CLAUDE.md
    records for TCC: outside our bundle, macOS considers Python the app.
    """
    bundle = main_bundle if main_bundle is not None else _main_bundle()
    if bundle is None:
        return None
    identifier = bundle.bundleIdentifier()
    if identifier != BUNDLE_IDENTIFIER:
        logger.info(
            "Not running from the VibePaste bundle (%s) — no relaunch target",
            identifier,
        )
        return None
    path = bundle.bundlePath()
    return Path(path) if path else None
