# 🎙️ VibePaste

**VibePaste** is a powerful macOS menu bar application that brings offline, high-accuracy voice-to-text to any application. It allows you to record audio, transcribe it locally using `whisper.cpp`, and automatically paste the text into your active window.

---

## 🚀 Features

- **Offline & Private**: All transcription happens locally on your machine.
- **Fast**: Keeps a `whisper-server` resident so the model isn't reloaded on
  every recording. A warm transcription of a short clip takes ~0.6 s instead
  of ~4 s.
- **Audio is never lost**: Every recording is written to disk *before*
  transcription is attempted. The last 10 are kept; older ones are pruned.
- **Recent Recordings menu**: The menu bar lists the last 10 recordings with
  a preview of their text — click one to copy its transcript to the clipboard.
- **Global Hotkeys**: The same combo starts and stops a recording.
- **Visual Feedback**:
  - 🔴 **Recording** — pulsating red dots, plus a duration bar that fills
    left-to-right and shifts blue → green → red as you approach one minute,
    so you know when to break your dictation into parts.
  - 🔵 **Processing** — spinning teal arc while audio is transcribed.
- **Escalating retries**: Transcription is retried three times, each with a
  *different* mechanism (resident server → restarted server → `whisper-cli`
  fork), so a wedged server can't silently produce nothing.
- **Honest paste**: Checks macOS Accessibility permission before simulating
  `Cmd+V`, falls back to AppleScript, and otherwise tells you the text is in
  the clipboard rather than reporting a success that didn't happen.

---

## 🛠️ Prerequisites

VibePaste is built as a frontend for the incredible **[whisper.cpp](https://github.com/ggerganov/whisper.cpp)** project. You **must** have `whisper.cpp` installed and built on your system for VibePaste to function.

### 1. Install & Build `whisper.cpp`

1.  Clone the repository (or ensure you have it):
    ```bash
    git clone https://github.com/ggerganov/whisper.cpp.git
    cd whisper.cpp
    ```
2.  Build the project. VibePaste uses **both** `whisper-server` (the fast
    path, keeps the model in RAM) and `whisper-cli` (the fallback):
    ```bash
    cmake -B build && cmake --build build -j --config Release
    # Ensure build/bin/whisper-server and build/bin/whisper-cli both exist.
    ```
3.  Download models (VibePaste uses `large-v3-turbo` for English and `large-v3` for Multilingual):
    ```bash
    sh ./models/download-ggml-model.sh large-v3-turbo
    sh ./models/download-ggml-model.sh large-v3
    ```

---

## 📥 Installation

### 1. Clone VibePaste

Clone this repository. It is recommended, but not strictly required, to place it inside or next to your `whisper.cpp` folder for easier auto-discovery.

### 2. Set up Python Environment

VibePaste requires Python 3. Create a virtual environment to manage dependencies:

```bash
cd vibepaste
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Build the App

The bundle's executable is a small compiled launcher, not a script. This
matters: when the executable is a `#!` script, macOS runs *Python* and
treats Python as the application, so `VibePaste.app`'s own `Info.plist` is
ignored and every permission is attributed to the interpreter. The symptoms
are silent — no menu bar icon, and recordings that come out as silence.

```bash
./tools/build_app.sh --install
```

That compiles `tools/launcher.c`, ad-hoc signs the bundle so your granted
permissions survive a rebuild, and copies it to `/Applications`. Drop
`--install` to build in place.

The Python side lives in `VibePaste.app/Contents/Resources/main.py`. If your
checkout is not at `~/myprojects/vibepaste`, update `PROJECT_ROOT` there and
the interpreter path in `tools/build_app.sh`, then rebuild.

### 4. Grant Permissions

On first launch VibePaste asks for **Microphone**, **Accessibility** and
**Input Monitoring**. All three are required: without Microphone, macOS
feeds the app silence rather than failing, and the transcript is invented.
The permissions are attributed to VibePaste itself, so they persist when
Homebrew upgrades Python.

### 5. Configuration (Crucial Step)

VibePaste needs to know where `whisper.cpp` is located. It tries to find it automatically in the following order:

1.  **Environment Variable**: `WHISPER_CPP_HOME`
2.  **Sibling Directory**: `../whisper.cpp` (if `vibepaste` is next to it)
3.  **Parent Directory**: If `vibepaste` is inside `whisper.cpp`

**Recommended:** If VibePaste assumes the wrong path, export the variable in your shell profile (e.g., `.zshrc`) or set it before running:

```bash
export WHISPER_CPP_HOME="/absolute/path/to/whisper.cpp"
```

---

## 🖥️ Usage

You can run VibePaste in two ways:

### Method A: The macOS App (Recommended)

1.  Navigate to the `vibepaste` folder.
2.  Double-click **VibePaste.app**.
3.  **First Run Permissions**: macOS will ask for permissions. You **must** grant:
    - **Microphone**: To record audio.
    - **Accessibility / Input Monitoring**: To detect global hotkeys and paste text.
      _If the app fails to launch initially, check `System Settings > Privacy & Security > Input Monitoring` and ensure VibePaste (or Terminal) is checked._

### Method B: Terminal

```bash
./vibepaste.command
```

or manually:

```bash
source venv/bin/activate
python3 -m src.menubar
```

---

## ⌨️ How to Use

1.  App runs in the **Menu Bar** (look for the 🎙️ icon).
2.  Place your cursor in any text field (Notes, Slack, VS Code, etc.).
3.  Press the hotkey to **start recording**:
    - **Left Option (⌥) + Space** → English (uses turbo model)
    - **Right Option (⌥) + Space** → Bosnian/Other (uses large-v3 model)
4.  Speak your message. Watch the bar under the dots — it fills over 60 s and
    turns red when your clip is getting long.
5.  Press plain **Space** — or the same combo again — to stop, transcribe
    and paste.

The hotkey combination is swallowed before it reaches the app you're typing
in, so `⌥+Space` doesn't leave a stray non-breaking space in the field you're
about to paste into. Plain `Space` is never touched — only Space *with Option
held*. (This needs Accessibility permission; without it the hotkey still
works but types a space, and the log says so.)

When a space does slip through anyway — either in that no-permission mode,
or because macOS delivered Space a few milliseconds ahead of Option — it is
backspaced away immediately before the transcript is pasted over it. The
Backspace is sent *only* when the intercept actually watched the character
reach the app, and only where the paste is about to land, so a field that
never received a stray space is never touched.

Plain Space only means "stop" while a recording is actually running, and it
is never swallowed: it still types a space as well. Suppressing it would
mean that one missed stop event leaves you unable to type a space anywhere,
which is a worse failure than a stray character.

The hotkey is a toggle, so you can keep typing normally while a
transcription runs. A new recording can be
started while the previous one is still transcribing — results paste in the
order you spoke them. Recordings hard-stop at 120 s as a safety net.

---

## 🕘 Recent Recordings

Every recording is saved to `~/.vibepaste/recordings/` before transcription
is attempted, as `rec_<date>_<time>_<ms>_<lang>.wav`, with its transcript
alongside as a `.txt`. The **last 10** are kept and older ones are deleted
automatically.

They open in their own window, reachable three ways: **Recent recordings…**
at the bottom of the menu bar menu, clicking the Dock icon, or right-clicking
the Dock icon.

Each row draws the recording's real amplitude envelope, so you can pick one
out without reading the transcript — and a recording captured with no
microphone access shows as a flat line rather than looking like every other
one. Those are labelled *No sound captured* and offer **Reveal** instead of
**Copy**, because a transcript of silence is whatever the model invented for
it, not something you said.

---

## 🌍 Changing the Second Language

By default, the Right Option + Space hotkey transcribes in **Bosnian**. To change this to another language:

1. Open `config.py`
2. Find line 51:
   ```python
   LANGUAGE_BOSNIAN = "bs"
   ```
3. Change `"bs"` to your desired [ISO 639-1 language code](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes):
   ```python
   LANGUAGE_BOSNIAN = "de"  # German
   LANGUAGE_BOSNIAN = "es"  # Spanish
   LANGUAGE_BOSNIAN = "fr"  # French
   LANGUAGE_BOSNIAN = "ja"  # Japanese
   ```

> **Note**: The variable is named `LANGUAGE_BOSNIAN` for historical reasons, but it controls the second language slot.

---

## 🔧 Troubleshooting

- **"VibePaste is already running!"**: Check your menu bar for the icon. If not there, run `pgrep -lf python` in terminal and kill any stuck processes.
- **No Text Pasted**:
  - The text is in your clipboard whenever the terminal/notification says so —
    press `Cmd+V`. VibePaste only claims a paste succeeded when it actually
    sent the keystroke.
  - "Grant Accessibility to auto-paste" means macOS is blocking synthetic
    keystrokes. Add the app in **System Settings → Privacy & Security →
    Accessibility**. Note that *editing the app bundle can invalidate an
    existing grant* — toggle it off and on again if paste stops working after
    an update.
  - Ensure `whisper-cli` is executable at `$WHISPER_CPP_HOME/build/bin/whisper-cli`.
  - Check that **Input Monitoring** permission is granted in System Settings.
- **Transcription failed**: the terminal prints the reason for each of the
  three attempts, and the audio is kept in `~/.vibepaste/recordings/` so
  nothing is lost. The full log is at `~/.vibepaste/vibepaste.log`.
- **Overlay animation stuck on screen**: it exits with the app, but to force it:
  ```bash
  pkill -f 'overlay/ui_app.py'
  ```
- **Leftover whisper-server**: the model is unloaded after 10 minutes idle and
  on quit. To force it:
  ```bash
  pkill -f whisper-server
  ```
- **Audio Issues**: Check System Settings to ensure the correct microphone is selected as default.

---

## 🧪 Development

```bash
source venv/bin/activate
python -m pytest
```

To rebuild the app icon from source artwork:

```bash
python tools/build_icon.py assets/AppIcon-source.png
```

---

## 📜 License

[MIT](LICENSE)
