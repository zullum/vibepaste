"""VibePaste - Main orchestrator"""

import signal
import sys
import os
import time
import shutil
import logging
import threading
from pathlib import Path
from pynput import keyboard

# Hide from macOS dock to prevent bouncing
try:
    import AppKit
    info = AppKit.NSBundle.mainBundle().infoDictionary()
    info["LSUIElement"] = "1"
except ImportError:
    pass  # AppKit not available (not on macOS)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    WHISPER_PATH,
    MODEL_PATH_TURBO,
    MODEL_PATH_V3,
    MODEL_FOR_ENGLISH,
    MODEL_FOR_BOSNIAN,
    TEMP_AUDIO_PATH,
    SAMPLE_RATE,
    CHANNELS,
    LANGUAGE_BOSNIAN,
    LANGUAGE_ENGLISH,
)
from src.audio_recorder import AudioRecorder
from src.transcriber import Transcriber
from src.paster import Paster
from src.keyboard_listener import KeyboardListener
from src.overlay import RecordingOverlay, SoundEffects

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VibePaste:
    """Main application orchestrator"""

    def __init__(self):
        logger.info("Initializing VibePaste")

        # Initialize components
        self.audio_recorder = AudioRecorder(
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS
        )
        # We'll create transcriber per request with appropriate model
        self.whisper_path = WHISPER_PATH
        self.paster = Paster(restore_clipboard=False)
        self.keyboard_listener = KeyboardListener()
        self.overlay = RecordingOverlay()
        self.sound_effects = SoundEffects()

        # Track recording state
        self.is_recording = False
        self.recording_language = None
        self.recording_model = None

        # Register hotkeys
        self._register_hotkeys()

        logger.info("VibePaste initialized successfully")

    def _register_hotkeys(self):
        """Register keyboard hotkeys"""
        # Left Option + Space (Start English)
        self.keyboard_listener.register_hotkey(
            name='english',
            modifier=keyboard.Key.alt_l,
            key=keyboard.Key.space,
            on_toggle=self._on_start_english
        )

        # Right Option + Space (Start Bosnian)
        self.keyboard_listener.register_hotkey(
            name='bosnian',
            modifier=keyboard.Key.alt_r,
            key=keyboard.Key.space,
            on_toggle=self._on_start_bosnian
        )

        # Space alone (Stop recording)
        self.keyboard_listener.register_single_key(
            name='stop',
            key=keyboard.Key.space,
            on_press=self._on_stop_recording
        )

    def _on_start_english(self, hotkey_name):
        """Start English recording"""
        if not self.is_recording:
            self._start_recording('english')

    def _on_start_bosnian(self, hotkey_name):
        """Start Bosnian recording"""
        if not self.is_recording:
            self._start_recording('bosnian')

    def _on_stop_recording(self):
        """Stop recording when Space is pressed alone"""
        try:
            print(f"DEBUG: _on_stop_recording called. is_recording={self.is_recording}")
            if self.is_recording:
                # DEBOUNCE: Check if recording just started (e.g. key repeat)
                if hasattr(self, 'recording_start_time') and (time.time() - self.recording_start_time < 1.0):
                    print("DEBUG: Stop ignored (too soon - debounce protection)")
                    return

                # Run in separate thread to avoid blocking listener
                threading.Thread(target=self._stop_and_transcribe).start()
            else:
                print("DEBUG: Stop ignored (not recording)")
        except Exception as e:
            print(f"ERROR in _on_stop_recording: {e}")
            import traceback
            traceback.print_exc(file=sys.stdout)

    def _start_recording(self, hotkey_name):
        """Start recording audio"""
        logger.info(f"Hotkey pressed: {hotkey_name} - Starting recording")
        
        self.recording_start_time = time.time()

        # Set language and model based on hotkey
        if hotkey_name == 'english':
            self.recording_language = LANGUAGE_ENGLISH
            self.recording_model = MODEL_FOR_ENGLISH
            model_name = "turbo"
        elif hotkey_name == 'bosnian':
            self.recording_language = LANGUAGE_BOSNIAN
            self.recording_model = MODEL_FOR_BOSNIAN
            model_name = "v3"

        # Start recording
        try:
            # Reset keyboard state to prevent stuck modifiers
            self.keyboard_listener.reset_keys()
            
            self.audio_recorder.start_recording()
            self.is_recording = True
            lang_display = self.recording_language or 'English'

            # Play start sound and show overlay
            self.sound_effects.play_start()
            self.overlay.show(language=lang_display, model=model_name)

            logger.info(f"Recording started (language: {lang_display}, model: {model_name})")
            print(f"\n🔴 Recording... ({lang_display}, {model_name})")
            print("Press Space to stop and transcribe")
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            print(f"\n⚠️  ERROR: Could not start recording: {e}")
            print("Make sure microphone permissions are granted in System Settings")
            self.is_recording = False

    def _stop_and_transcribe(self):
        """Stop recording and transcribe audio"""
        # Play stop sound and hide overlay
        self.sound_effects.play_stop()
        self.overlay.hide()
        
        print("DEBUG: _stop_and_transcribe called")

        # Stop recording
        try:
            print("DEBUG: Calling audio_recorder.stop_recording...")
            success = self.audio_recorder.stop_recording(TEMP_AUDIO_PATH)
            self.is_recording = False
            print(f"DEBUG: stop_recording returned {success}")

            if not success:
                print("⚠️  No audio recorded")
                return

            print("🎙️  Processing...")

            # Create transcriber with appropriate model
            print(f"DEBUG: Initializing transcriber with model {self.recording_model}")
            transcriber = Transcriber(
                whisper_path=self.whisper_path,
                model_path=self.recording_model
            )

            # Transcribe
            print("DEBUG: Starting transcription...")
            transcription = transcriber.transcribe(
                audio_path=TEMP_AUDIO_PATH,
                language=self.recording_language
            )
            print(f"DEBUG: Transcription result: {transcription}")

            if not transcription:
                print("⚠️  No transcription generated")
                return

            # Paste
            print(f"✅ Transcribed: {transcription}")

            print("DEBUG: Pasting text...")
            success = self.paster.paste_text(transcription)
            if success:
                print("📋 Pasted!")
            else:
                print("⚠️  Paste failed - text copied to clipboard instead")

        except Exception as e:
            print(f"\n⚠️  ERROR in stop/transcribe: {e}")
            import traceback
            traceback.print_exc(file=sys.stdout)
            self.is_recording = False

    def run_background(self):
        """Run in background mode - for menubar integration (non-blocking)"""
        logger.info("Starting VibePaste in background mode")
        
        # Check device
        device = self.audio_recorder.get_default_device()
        if device:
            logger.info(f"Using microphone: {device['name']}")
        
        # Start keyboard listener (non-blocking)
        self.keyboard_listener.start()
        logger.info("VibePaste background mode started - keyboard listener active")

    def run(self):
        """Run the application"""
        logger.info("Starting VibePaste")
        print("\n" + "="*60)
        print("🎙️  VibePaste - Voice to Text Auto-Paste (Toggle Mode)")
        print("="*60)
        print("\nHotkeys:")
        print("  Left Option + Space   →  Start English recording (turbo)")
        print("  Right Option + Space  →  Start Bosnian recording (v3)")
        print("  Space (alone)         →  Stop recording and transcribe")
        print("\nUsage:")
        print("  1. Press hotkey once to START recording")
        print("  2. Speak your message")
        print("  3. Press hotkey again to STOP, transcribe, and paste")
        print("\nPress Ctrl+C to exit")
        print("="*60 + "\n")

        # Check device
        device = self.audio_recorder.get_default_device()
        if device:
            print(f"🎤 Using microphone: {device['name']}\n")

        # Start keyboard listener
        self.keyboard_listener.start()

        # Keep running until interrupted
        try:
            import time
            while self.keyboard_listener.is_running():
                time.sleep(1)  # Sleep instead of signal.pause() to avoid macOS dock bouncing
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
            print("\n\n👋 Shutting down VibePaste...")
            self.stop()

    def stop(self):
        """Stop the application"""
        logger.info("Stopping VibePaste")
        self.overlay.hide()
        self.keyboard_listener.stop()
        logger.info("VibePaste stopped")


def main():
    """Entry point"""
    try:
        app = VibePaste()
        app.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"\n❌ FATAL ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
