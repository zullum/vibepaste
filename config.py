"""Configuration constants for VibePaste"""

import os
from pathlib import Path

# Paths
# Paths
# Try to find whisper.cpp in order:
# 1. WHISPER_CPP_HOME env var
# 2. Sibling directory ../whisper.cpp
# 3. Default fallback (can be overridden by user)

def find_whisper_cpp():
    # 1. Check env var
    if os.getenv("WHISPER_CPP_HOME"):
        return Path(os.getenv("WHISPER_CPP_HOME"))
    
    # 2. Check sibling directory (assuming vibepaste is inside or next to whisper.cpp)
    # If vibepaste is inside whisper.cpp/vibepaste
    current_dir = Path(__file__).parent.resolve()
    if (current_dir.parent / "models").exists():
        return current_dir.parent
        
    # If vibepaste is a sibling of whisper.cpp
    if (current_dir.parent / "whisper.cpp").exists():
        return current_dir.parent / "whisper.cpp"
        
    # 3. Last resort fallback or raise error
    # For now, return a default relative path assuming standard structure
    return Path(__file__).parent.parent

WHISPER_CPP_DIR = find_whisper_cpp()
WHISPER_PATH = WHISPER_CPP_DIR / "build" / "bin" / "whisper-cli"
MODEL_PATH_TURBO = WHISPER_CPP_DIR / "models" / "ggml-large-v3-turbo.bin"
MODEL_PATH_V3 = WHISPER_CPP_DIR / "models" / "ggml-large-v3.bin"

# Use system temp directory
import tempfile
TEMP_AUDIO_PATH = Path(tempfile.gettempdir()) / "vibepaste_recording.wav"

# Audio settings
SAMPLE_RATE = 16000  # 16kHz for whisper.cpp
CHANNELS = 1  # Mono

# Hotkey configuration
# Using Key enum values for pynput
HOTKEY_ENGLISH = "english"  # Left Option + E
HOTKEY_BOSNIAN = "bosnian"  # Left Option + B

# Language codes and model mappings
LANGUAGE_BOSNIAN = "bs"
LANGUAGE_ENGLISH = None  # whisper auto-detects English

# Model selection per language
MODEL_FOR_ENGLISH = MODEL_PATH_TURBO  # Turbo for English
MODEL_FOR_BOSNIAN = MODEL_PATH_V3     # Full v3 for Bosnian

# Logging
LOG_LEVEL = "INFO"
