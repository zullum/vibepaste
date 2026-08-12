"""VibePaste menu bar app.

The menu is deliberately short: the recordings themselves live in their own
window, where a transcript can be read rather than truncated to one line.
"""

import logging
import sys
import threading
from pathlib import Path

VIBEPASTE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(VIBEPASTE_DIR))

import config  # noqa: E402
from src.app_restart import AppRestarter, resolve_bundle_path  # noqa: E402
from src.dock_icon import set_dock_icon_handler  # noqa: E402
from src.recording_store import RecordingStore  # noqa: E402
from src.recordings_window import RecordingsWindow  # noqa: E402
from src.restart_ledger import RestartLedger  # noqa: E402
from src.wedge_recovery import WedgeRecovery  # noqa: E402

logger = logging.getLogger(__name__)

HISTORY_TITLE = "Recent recordings…"

# Dock behaviour is decided by LSUIElement in VibePaste.app's own Info.plist.
#
# There used to be a runtime hack here that forced LSUIElement on the main
# bundle. It existed only because the bundle executable was a shebang script,
# which made Python.app the main bundle and left our own plist unread. The
# executable is a real binary now (see tools/launcher.c), so the plist is
# authoritative and the hack would only override the user's choice.

from src.permissions import (  # noqa: E402
    check_accessibility, check_input_monitoring, check_microphone,
)

try:
    import rumps
    RUMPS_AVAILABLE = True
except ImportError:
    rumps = None
    RUMPS_AVAILABLE = False


if RUMPS_AVAILABLE:

    def _quit_on_main_thread():
        """Quit from a background thread without touching AppKit off-main.

        The recovery runs on its own thread, and `NSApp.terminate_()` is an
        AppKit call. Going through rumps also keeps `before_quit` in the
        path, which is the only hook that releases the whisper-server.
        """
        from PyObjCTools import AppHelper

        AppHelper.callAfter(rumps.quit_application)

    def _build_recovery(vibepaste):
        """Wire the wedge recovery to a running VibePaste."""
        bundle = resolve_bundle_path()
        # The one line that says whether auto-restart is actually armed.
        # A mismatched bundle identifier disables it silently and correctly
        # — failing safe, but invisibly, which is worth a log line.
        logger.info("DIAG relaunch target: %s", bundle or "none (no restart)")
        return WedgeRecovery(
            ledger=RestartLedger(config.VIBEPASTE_DIR / "restarts.json"),
            restarter=AppRestarter(bundle),
            pending=lambda: vibepaste.worker.pending,
            is_wedged=vibepaste.audio_recorder.is_wedged,
            notify=lambda title, message: rumps.notification(
                title, "", message
            ),
            quit_app=_quit_on_main_thread,
        )

    class VibePasteMenuBar(rumps.App):
        """Menu bar front end for VibePaste."""

        def __init__(self):
            super().__init__(name="VibePaste", title="🎙️",
                             quit_button="Quit VibePaste")
            self.vibepaste = None
            # Built with VibePaste, so it is absent until it is started.
            self.recovery = None
            self.store = RecordingStore(
                config.RECORDINGS_DIR, config.MAX_STORED_RECORDINGS
            )
            self._recordings = RecordingsWindow()
            self.menu = [
                rumps.MenuItem("Start VibePaste", callback=self.start_vibepaste),
                rumps.MenuItem("Stop VibePaste", callback=self.stop_vibepaste),
                None,
                rumps.MenuItem("⌥L + Space → English", callback=None),
                rumps.MenuItem("⌥R + Space → Bosnian", callback=None),
                rumps.MenuItem("Space → stop & paste", callback=None),
                None,
                rumps.MenuItem(HISTORY_TITLE, callback=self.show_recordings),
            ]
            # Clicking the Dock tile opens the same window.
            set_dock_icon_handler(lambda: self.show_recordings(None))
            # Quitting has to reach VibePaste.stop(), or the whisper-server it
            # started (~2.9GB for large-v3) is orphaned to launchd and never
            # unloads — the leak that once took a 24GB machine down.
            #
            # before_quit is the *only* hook that fires here, which was
            # measured rather than assumed: applicationWillTerminate_ emits
            # it, while atexit never runs (NSApp.terminate_ skips
            # Py_FinalizeEx) and a SIGTERM handler never runs either — the
            # AppKit run loop parks the main thread, so the signal does not
            # even stop the process.
            rumps.events.before_quit.register(self._on_before_quit)
            self.start_vibepaste(None)
            # Report what actually got created once the run loop is up. The
            # menu bar item and the event tap are both invisible failures
            # otherwise: nothing raises, they just don't appear or don't fire.
            threading.Timer(3.0, self._log_diagnostics).start()

        def _log_diagnostics(self):
            # The item's *height* is the meaningful field: a status item that
            # macOS never laid into the menu bar still reports isVisible=True
            # and a correct title, but its window stays 0-high at (0, 0).
            try:
                item = self._nsapp.nsstatusitem
                frame = item.button().window().frame()
                drawn = frame.size.height > 0
                logger.info(
                    "DIAG menubar item: drawn=%s title=%r at x=%.0f y=%.0f h=%.0f",
                    drawn, item.button().title(),
                    frame.origin.x, frame.origin.y, frame.size.height,
                )
                if not drawn:
                    logger.error(
                        "Menu bar icon was not drawn — the bundle's launcher "
                        "must not re-exec the interpreter; see its docstring."
                    )
            except Exception as e:
                logger.error(f"DIAG menubar item unavailable: {e}")

            listener = getattr(self.vibepaste, "keyboard_listener", None)
            if listener is None:
                logger.error("DIAG hotkeys: VibePaste not started")
                return
            # healthy= is the field that matters: running= stays True after
            # macOS disables the event tap, which is how a stone-deaf hotkey
            # used to report itself as fine.
            logger.info(
                "DIAG hotkeys: running=%s healthy=%s watchdog=%s "
                "suppression=%s registered=%s",
                listener.is_running(), listener.is_healthy(),
                listener.watchdog.is_running(), listener.suppress_hotkeys,
                list(listener._toggles),
            )

        def _on_device_failed(self, reason, wedged=False):
            """Make a lost microphone impossible to miss.

            CoreAudio can deadlock in a way nothing in this process can
            undo, and the app otherwise looks perfectly healthy while every
            recording silently does nothing.

            A wedge is handed to the recovery, which may end this process to
            get the microphone back. A device that answered and refused is
            not: restarting would not free a microphone another app holds.
            """
            self.title = "🎙️ ⚠️"
            rumps.notification(
                "🎙️ VibePaste", "Microphone unavailable",
                f"{reason}. Quit and reopen VibePaste.",
            )
            if wedged and self.recovery is not None:
                self.recovery.on_wedge()

        def _on_device_ok(self):
            """A recording reached disk, so the microphone is working.

            Without this the warning above never came down on its own, and a
            menu bar still showing ⚠️ over a perfectly healthy app is worse
            than no warning at all. Guarded because every saved recording
            calls it and reassigning the title redraws the status item.
            """
            if self.title != "🎙️":
                self.title = "🎙️"
                logger.info("Microphone recovered — warning cleared")

        def _on_before_quit(self):
            """Release the whisper-server before the process disappears."""
            if self.vibepaste is None:
                return
            try:
                self.vibepaste.stop()
            except Exception as e:
                logger.error(f"Shutdown on quit failed: {e}", exc_info=True)

        # -- recordings window ------------------------------------------

        def show_recordings(self, _sender):
            """Open the window, always with the current contents."""
            store = getattr(self.vibepaste, "store", None) or self.store
            self._recordings.show(store)

        # -- lifecycle --------------------------------------------------

        def start_vibepaste(self, sender):
            if self.vibepaste is not None:
                rumps.notification("🎙️ VibePaste", "Already Running",
                                   "VibePaste is already active.")
                return
            try:
                from src.main import VibePaste

                self.vibepaste = VibePaste()
                self.vibepaste.on_device_failed = self._on_device_failed
                self.vibepaste.on_device_ok = self._on_device_ok
                self.vibepaste.run_background()
                self.store = self.vibepaste.store
                self.recovery = _build_recovery(self.vibepaste)
                self.title = "🎙️"
                rumps.notification("🎙️ VibePaste", "Started",
                                   "Ready — ⌥+Space to record.")
                logger.info("VibePaste started")
                # If this process is itself the result of a restart, explain
                # the blip rather than leaving the user to wonder.
                self.recovery.announce_restart()
            except Exception as e:
                logger.error(f"Failed to start VibePaste: {e}", exc_info=True)
                rumps.notification("🎙️ VibePaste", "Error",
                                   f"Failed to start: {e}")

        def stop_vibepaste(self, sender):
            if self.vibepaste is None:
                rumps.notification("🎙️ VibePaste", "Not Running",
                                   "VibePaste is not currently running.")
                return
            try:
                self.vibepaste.stop()
            except Exception as e:
                logger.error(f"Error stopping VibePaste: {e}")
            self.vibepaste = None
            self.title = "🎙️ (off)"
            rumps.notification("🎙️ VibePaste", "Stopped",
                               "VibePaste has been stopped.")


def main():
    from src.main import setup_logging

    setup_logging()
    if not check_accessibility(prompt=True):
        print("⚠️  Accessibility denied — auto-paste will not work.")
    if not check_input_monitoring(prompt=True):
        print("⚠️  Input Monitoring denied — the hotkey will never fire.")
    if not check_microphone(prompt=True):
        print("⚠️  Microphone denied — recordings will be silent and the "
              "transcript will be invented.")
    if not RUMPS_AVAILABLE:
        print("❌ rumps not installed. Run: pip install rumps")
        sys.exit(1)
    VibePasteMenuBar().run()


if __name__ == "__main__":
    main()
