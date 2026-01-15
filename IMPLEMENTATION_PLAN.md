# VibePaste - Voice-to-Text Auto-Paste Tool

A macOS menu bar application for offline voice-to-text using whisper.cpp.

## Overview

VibePaste captures audio, transcribes it locally using whisper.cpp, and auto-pastes the text.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     VibePaste Architecture                       │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │   Keyboard   │───▶│    Audio     │───▶│   whisper.cpp    │   │
│  │   Listener   │    │   Recorder   │    │   Transcription  │   │
│  └──────────────┘    └──────────────┘    └──────────────────┘   │
│         │                                         │              │
│         │                                         ▼              │
│         │                              ┌──────────────────┐      │
│         └─────────────────────────────▶│ Clipboard+Paste  │      │
│                                        └──────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

## Hotkeys

| Action               | Hotkey                   | Model               |
| -------------------- | ------------------------ | ------------------- |
| Start English        | **Left Option + Space**  | ggml-large-v3-turbo |
| Start Other Language | **Right Option + Space** | ggml-large-v3       |
| Stop & Paste         | **Space** (alone)        | —                   |

## Language Configuration

Edit `config.py` line 51 to change the second language:

```python
LANGUAGE_BOSNIAN = "bs"  # Change to: de, es, fr, ja, zh, etc.
```

## File Structure

```
vibepaste/
├── config.py                 # Paths, language, hotkey config
├── requirements.txt
├── vibepaste.command         # Terminal launcher
├── VibePaste.app/            # macOS app bundle
├── src/
│   ├── main.py               # Orchestrator
│   ├── keyboard_listener.py  # Hotkey detection
│   ├── audio_recorder.py     # Microphone recording
│   ├── transcriber.py        # whisper.cpp integration
│   ├── paster.py             # Clipboard & paste
│   ├── overlay.py            # Recording UI overlay
│   └── menubar.py            # Menu bar app
└── README.md
```

## Dependencies

```txt
pynput>=1.7.6          # Global keyboard hooks
sounddevice>=0.4.6     # Audio recording
scipy>=1.11.0          # WAV file handling
numpy>=1.24.0          # Audio processing
pyperclip>=1.8.2       # Clipboard operations
pyautogui>=0.9.54      # Paste simulation
rumps                   # Menu bar integration
pyobjc-framework-Cocoa # macOS overlay
```

## macOS Permissions Required

1. **Microphone**: System Settings → Privacy & Security → Microphone
2. **Accessibility**: System Settings → Privacy & Security → Accessibility
3. **Input Monitoring**: System Settings → Privacy & Security → Input Monitoring

## Usage Flow

```
1. Click VibePaste.app (or run ./vibepaste.command)
2. 🎙️ icon appears in menu bar
3. Focus on any text field
4. Press Left Option + Space (English) or Right Option + Space (other)
5. Recording overlay appears - speak your message
6. Press Space alone to stop
7. Text is transcribed and pasted automatically
```

## Error Handling

| Error               | Handling                             |
| ------------------- | ------------------------------------ |
| No mic access       | Notification + open Privacy settings |
| No accessibility    | Notification + open Privacy settings |
| whisper.cpp fails   | Log error, show notification         |
| Empty transcription | Skip paste                           |
