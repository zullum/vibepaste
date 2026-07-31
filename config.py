"""Configuration constants for VibePaste"""

import os
import tempfile
from pathlib import Path


def find_whisper_cpp():
    """Locate the whisper.cpp checkout.

    Order: WHISPER_CPP_HOME env var, parent dir holding models/, sibling
    whisper.cpp directory, then the project parent as a last resort.
    """
    if os.getenv("WHISPER_CPP_HOME"):
        return Path(os.getenv("WHISPER_CPP_HOME"))

    current_dir = Path(__file__).parent.resolve()
    if (current_dir.parent / "models").exists():
        return current_dir.parent
    if (current_dir.parent / "whisper.cpp").exists():
        return current_dir.parent / "whisper.cpp"
    return current_dir.parent


WHISPER_CPP_DIR = find_whisper_cpp()
WHISPER_PATH = WHISPER_CPP_DIR / "build" / "bin" / "whisper-cli"
WHISPER_SERVER_PATH = WHISPER_CPP_DIR / "build" / "bin" / "whisper-server"
MODEL_PATH_TURBO = WHISPER_CPP_DIR / "models" / "ggml-large-v3-turbo.bin"
MODEL_PATH_V3 = WHISPER_CPP_DIR / "models" / "ggml-large-v3.bin"

# Working directories
VIBEPASTE_DIR = Path.home() / ".vibepaste"
RECORDINGS_DIR = VIBEPASTE_DIR / "recordings"
TEMP_AUDIO_PATH = Path(tempfile.gettempdir()) / "vibepaste_recording.wav"

# Audio settings
SAMPLE_RATE = 16000  # 16kHz for whisper.cpp
CHANNELS = 1  # Mono

# Recording limits (seconds)
RECORDING_WARN_SECONDS = 60   # progress bar spans this, then turns red
RECORDING_MAX_SECONDS = 120   # hard auto-stop so a forgotten recording can't run away

# Recording store
MAX_STORED_RECORDINGS = 10

# Language codes and model mappings
LANGUAGE_BOSNIAN = "bs"
LANGUAGE_ENGLISH = "en"

MODEL_FOR_ENGLISH = MODEL_PATH_TURBO  # Turbo for English
MODEL_FOR_BOSNIAN = MODEL_PATH_V3     # Full v3 for Bosnian

# Transcription
# Leave 2 cores for the UI process and the audio callback.
WHISPER_THREADS = max(4, (os.cpu_count() or 8) - 2)
TRANSCRIBE_ATTEMPTS = 3
SERVER_STARTUP_TIMEOUT = 120   # large-v3 is 3.1GB; cold page cache is slow
SERVER_IDLE_UNLOAD_SECONDS = 600
# Per-attempt timeout: base + a multiple of the audio duration.
TRANSCRIBE_TIMEOUT_BASE = 30
TRANSCRIBE_TIMEOUT_PER_SECOND = 4.0

# Logging
LOG_LEVEL = "INFO"
LOG_PATH = VIBEPASTE_DIR / "vibepaste.log"
