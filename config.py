"""Configuration constants for VibePaste"""

import os
from pathlib import Path

# Paths
WHISPER_CPP_DIR = Path("/Users/sanel.zulic/myprojects/whisper.cpp")
WHISPER_PATH = WHISPER_CPP_DIR / "build" / "bin" / "whisper-cli"
MODEL_PATH_TURBO = WHISPER_CPP_DIR / "models" / "ggml-large-v3-turbo.bin"
MODEL_PATH_V3 = WHISPER_CPP_DIR / "models" / "ggml-large-v3.bin"
TEMP_AUDIO_PATH = "/tmp/vibepaste_recording.wav"

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
