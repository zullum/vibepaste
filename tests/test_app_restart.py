"""Tests for arming a relaunch that outlives the process arming it.

The app cannot restart itself from the inside. os.execv is what gives the
bundle a zero-height status item (see tools/launcher.c), so the relaunch has
to be a detached helper that waits for this process to die and then reopens
the bundle.

The consequence that shapes every test here: quitting is only safe once the
helper is actually armed. A quit with no relaunch behind it leaves the user
with no app at all, which is far worse than the wedged microphone it was
trying to fix.
"""

import pytest

from src.app_restart import BUNDLE_IDENTIFIER, AppRestarter, resolve_bundle_path


class FakeBundle:
    """Stands in for NSBundle.mainBundle()."""

    def __init__(self, identifier, path="/Applications/VibePaste.app"):
        self._identifier = identifier
        self._path = path

    def bundleIdentifier(self):
        return self._identifier

    def bundlePath(self):
        return self._path


def test_our_own_bundle_is_resolved():
    resolved = resolve_bundle_path(FakeBundle(BUNDLE_IDENTIFIER))

    assert str(resolved) == "/Applications/VibePaste.app"


def test_pythons_own_bundle_is_not_mistaken_for_ours():
    """Measured, not hypothetical: run from a terminal, NSBundle.mainBundle()
    is the interpreter's own Python.app — which ends in .app and exists, so a
    path-shaped check accepts it and arms `open -a Python.app`.

    This is the same confusion CLAUDE.md records for TCC permissions: when we
    are not launched from our bundle, macOS considers Python the application.
    """
    python_app = (
        "/opt/homebrew/Cellar/python@3.14/3.14.2/Frameworks/"
        "Python.framework/Versions/3.14/Resources/Python.app"
    )
    resolved = resolve_bundle_path(FakeBundle("org.python.python", python_app))

    assert resolved is None


def test_a_bundle_without_an_identifier_is_refused():
    assert resolve_bundle_path(FakeBundle(None)) is None


class FakeSpawn:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error

    @property
    def command(self):
        return self.calls[0][0][-1]


def bundle(tmp_path, name="VibePaste.app"):
    path = tmp_path / name
    path.mkdir()
    return path


def test_the_helper_waits_for_this_process_before_reopening(tmp_path):
    """Reopening while we are still alive just activates the running app —
    LaunchServices will not start a second instance — and the PID-file guard
    would refuse it anyway."""
    spawn = FakeSpawn()
    restarter = AppRestarter(bundle(tmp_path), pid=4242, spawn=spawn)

    assert restarter.restart() is True
    assert "kill -0 4242" in spawn.command
    assert "open -a" in spawn.command
    assert "VibePaste.app" in spawn.command


def test_the_helper_gives_up_if_the_app_never_quits(tmp_path):
    """The helper is armed before the quit, so a quit that never happens
    leaves it polling. Unbounded, that is a stray process spinning until
    logout — and reopening a bundle that never died would only activate the
    running app anyway. Bounded, the worst case is a helper that expires.
    """
    from src.app_restart import GIVE_UP_SECONDS, POLL_SECONDS

    spawn = FakeSpawn()
    AppRestarter(bundle(tmp_path), pid=1, spawn=spawn).restart()

    assert str(int(GIVE_UP_SECONDS / POLL_SECONDS)) in spawn.command
    assert "exit" in spawn.command


def test_the_helper_outlives_the_process_that_armed_it():
    """Without a new session the helper dies with its parent, and the parent
    dying is the entire trigger."""
    spawn = FakeSpawn()
    restarter = AppRestarter("/tmp/x.app", pid=1, spawn=spawn,
                             verify_exists=False)

    restarter.restart()

    assert spawn.calls[0][1]["start_new_session"] is True


def test_the_helper_holds_no_handle_on_the_log(tmp_path):
    """The launcher points stdout at vibepaste_debug.log. A helper that
    inherits it keeps the old process's log file open indefinitely."""
    import subprocess

    spawn = FakeSpawn()
    AppRestarter(bundle(tmp_path), pid=1, spawn=spawn).restart()

    _, kwargs = spawn.calls[0]
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_a_path_with_spaces_survives_the_shell(tmp_path):
    """The helper is a shell string, so an unquoted path with a space
    reopens the wrong thing — or nothing at all."""
    import shlex

    spawn = FakeSpawn()
    path = bundle(tmp_path, "Vibe Paste.app")
    AppRestarter(path, pid=1, spawn=spawn).restart()

    assert shlex.split(spawn.command)[-1] == str(path)


def test_terminal_mode_cannot_restart():
    """run.sh is a foreground process with no bundle to reopen. Quitting it
    automatically would just close the app the developer is watching."""
    spawn = FakeSpawn()
    restarter = AppRestarter(None, pid=1, spawn=spawn)

    assert restarter.can_restart() is False
    assert restarter.restart() is False
    assert spawn.calls == []


def test_a_bundle_that_is_not_there_cannot_restart(tmp_path):
    """A moved or deleted .app must not cost the user a running app."""
    spawn = FakeSpawn()
    restarter = AppRestarter(tmp_path / "Gone.app", pid=1, spawn=spawn)

    assert restarter.can_restart() is False
    assert restarter.restart() is False
    assert spawn.calls == []


def test_a_path_that_is_not_an_app_cannot_restart(tmp_path):
    spawn = FakeSpawn()
    restarter = AppRestarter(bundle(tmp_path, "not-a-bundle"), pid=1,
                             spawn=spawn)

    assert restarter.can_restart() is False


def test_a_failed_spawn_reports_rather_than_raises(tmp_path):
    """The caller quits only on True. Raising here would escape into the
    failure path and could leave the app quit with nothing coming back."""
    spawn = FakeSpawn(error=OSError("fork failed"))
    restarter = AppRestarter(bundle(tmp_path), pid=1, spawn=spawn)

    assert restarter.restart() is False
