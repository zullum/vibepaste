"""VibePaste - Main orchestrator"""

import logging
import sys
import time
from pathlib import Path

from pynput import keyboard

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
from src.audio_recorder import AudioRecorder  # noqa: E402
from src.keyboard_listener import KeyboardListener  # noqa: E402
from src.overlay import OverlayController, SoundEffects  # noqa: E402
from src.paster import Paster  # noqa: E402
from src.recording_session import RecordingSession  # noqa: E402
from src.recording_store import RecordingStore  # noqa: E402
from src.transcriber import Transcriber  # noqa: E402
from src.transcription_worker import TranscriptionJob, TranscriptionWorker  # noqa: E402

logger = logging.getLogger(__name__)

LANGUAGES = {
    "english": (config.LANGUAGE_ENGLISH, config.MODEL_FOR_ENGLISH, "turbo"),
    "bosnian": (config.LANGUAGE_BOSNIAN, config.MODEL_FOR_BOSNIAN, "v3"),
}


class VibePaste:
    """Wires hotkeys, recording, transcription and pasting together.

    Recording always takes priority: it can start even while an earlier clip
    is still transcribing, and audio is written to the store before any
    transcription is attempted, so a failure never loses what was said.
    """

    def __init__(self):
        logger.info("Initializing VibePaste")

        self.audio_recorder = AudioRecorder(
            sample_rate=config.SAMPLE_RATE, channels=config.CHANNELS
        )
        self.store = RecordingStore(
            config.RECORDINGS_DIR, config.MAX_STORED_RECORDINGS
        )
        self.transcriber = Transcriber(
            whisper_cli_path=config.WHISPER_PATH,
            server_path=config.WHISPER_SERVER_PATH,
            threads=config.WHISPER_THREADS,
            attempts=config.TRANSCRIBE_ATTEMPTS,
            timeout_base=config.TRANSCRIBE_TIMEOUT_BASE,
            timeout_per_second=config.TRANSCRIBE_TIMEOUT_PER_SECOND,
            startup_timeout=config.SERVER_STARTUP_TIMEOUT,
            idle_unload_seconds=config.SERVER_IDLE_UNLOAD_SECONDS,
        )
        self.paster = Paster()
        self.sounds = SoundEffects()
        self.keyboard_listener = KeyboardListener()
        self.overlay = OverlayController(on_auto_stop=self._on_auto_stop)
        self.worker = TranscriptionWorker(
            transcriber=self.transcriber, store=self.store,
            paster=self.paster, sounds=self.sounds,
            on_queue_change=self._refresh_overlay,
        )
        self.session = RecordingSession(
            audio_recorder=self.audio_recorder, store=self.store,
            overlay=self.overlay, sounds=self.sounds,
            sample_rate=config.SAMPLE_RATE,
            warn_seconds=config.RECORDING_WARN_SECONDS,
            max_seconds=config.RECORDING_MAX_SECONDS,
            on_saved=self._on_recording_saved,
        )

        # Stray characters the start hotkey typed into the focused field,
        # to be deleted just before the transcript is pasted over them.
        # Only one recording runs at a time and stop() hands the audio over
        # synchronously, so a single field cannot be claimed by two clips.
        self._stray_characters = 0

        self._register_hotkeys()
        logger.info("VibePaste initialized")

    def _register_hotkeys(self):
        for name, modifier in (("english", keyboard.Key.alt_l),
                               ("bosnian", keyboard.Key.alt_r)):
            self.keyboard_listener.register_toggle(
                name=name, modifier=modifier,
                key=keyboard.Key.space, callback=self._on_hotkey,
            )
        # Plain Space also stops, which is how this has always been used.
        # It is only listened for (and swallowed) while actually recording.
        self.keyboard_listener.register_bare_key(
            name="stop", key=keyboard.Key.space, callback=self._on_stop_key,
        )

    def _set_recording_state(self, recording):
        """Space means 'stop' only while a recording is running."""
        self.keyboard_listener.enable_bare_key("stop", recording)
        self._refresh_overlay()

    # -- callbacks -------------------------------------------------------

    def _on_hotkey(self, name):
        """Toggle: the same combo that starts a recording also stops it."""
        if self.session.is_recording:
            self.session.stop()
        else:
            # Ask before starting: the signal is consumed on read, and the
            # answer is about the keypress that got us here.
            self._stray_characters = int(
                self.keyboard_listener.hotkey_typed_a_character(name)
            )
            self.session.start(*LANGUAGES[name])
        self._set_recording_state(self.session.is_recording)

    def _on_stop_key(self, _name):
        """Plain Space pressed — stop if we are recording, else ignore."""
        if self.session.is_recording:
            self.session.stop()
        self._set_recording_state(self.session.is_recording)

    def _on_auto_stop(self):
        """The overlay reported the recording hit its hard time limit."""
        self.session.stop()
        self._set_recording_state(self.session.is_recording)

    def _on_recording_saved(self, wav_path, model_path, language, duration):
        stray = self._stray_characters
        self._stray_characters = 0
        self.worker.submit(
            TranscriptionJob(wav_path, model_path, language, duration,
                             stray_characters=stray)
        )

    def _refresh_overlay(self):
        """Overlay follows state: recording > transcribing > hidden."""
        if self.session.is_recording:
            return  # show_recording already runs the bar; don't restart it
        if self.worker.pending > 0:
            self.overlay.show_processing()
        else:
            self.overlay.hide()

    # -- lifecycle -------------------------------------------------------

    def run_background(self):
        """Start hotkeys without blocking — used by the menubar app."""
        device = self.audio_recorder.get_default_device()
        if device:
            logger.info(f"Using microphone: {device['name']}")
        self.overlay.start()
        self.keyboard_listener.start()
        logger.info("VibePaste background mode started")

    def run(self):
        """Run in the foreground until interrupted."""
        _print_banner()
        device = self.audio_recorder.get_default_device()
        if device:
            print(f"🎤 Microphone: {device['name']}\n")

        self.overlay.start()
        self.keyboard_listener.start()
        try:
            while self.keyboard_listener.is_running():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n\n👋 Shutting down VibePaste...")
        finally:
            self.stop()

    def stop(self):
        """Shut everything down, leaving no orphan processes."""
        logger.info("Stopping VibePaste")
        self.session.shutdown()
        self.keyboard_listener.stop()
        self.overlay.stop()
        self.worker.shutdown()
        self.transcriber.shutdown()
        logger.info("VibePaste stopped")


def _print_banner():
    print("\n" + "=" * 62)
    print("🎙️  VibePaste - Voice to Text Auto-Paste")
    print("=" * 62)
    print("  Left Option + Space   →  English (turbo), press again to stop")
    print("  Right Option + Space  →  Bosnian (v3), press again to stop")
    print(f"\n  Last {config.MAX_STORED_RECORDINGS} recordings kept in "
          f"{config.RECORDINGS_DIR}")
    print(f"  Bar turns red at {config.RECORDING_WARN_SECONDS}s, "
          f"auto-stops at {config.RECORDING_MAX_SECONDS}s")
    print("\n  Ctrl+C to exit")
    print("=" * 62 + "\n")


def setup_logging():
    config.VIBEPASTE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_PATH),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main():
    setup_logging()
    try:
        VibePaste().run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n❌ FATAL ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
