# 🎙️ VibePaste

**VibePaste** is a powerful macOS menu bar application that brings offline, high-accuracy voice-to-text to any application. It allows you to record audio, transcribe it locally using `whisper.cpp`, and automatically paste the text into your active window.

---

## 🚀 Features

- **Offline & Private**: All transcription happens locally on your machine.
- **Fast & Accurate**: Powered by the state-of-the-art `whisper.cpp` engine.
- **Seamless Integration**: Pastes text directly into your active cursor position.
- **Global Hotkeys**: Start/Stop recording from anywhere.
- **Visual Feedback**: Elegant on-screen recording overlay.

---

## 🛠️ Prerequisites

VibePaste is built as a frontend for the incredible **[whisper.cpp](https://github.com/ggerganov/whisper.cpp)** project. You **must** have `whisper.cpp` installed and built on your system for VibePaste to function.

### 1. Install & Build `whisper.cpp`

1.  Clone the repository (or ensure you have it):
    ```bash
    git clone https://github.com/ggerganov/whisper.cpp.git
    cd whisper.cpp
    ```
2.  Build the project (specifically the `whisper-cli` tool):
    ```bash
    make
    # Ensure the 'build/bin/whisper-cli' executable is created.
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

### 3. Configure the App Launcher

The `VibePaste.app` launcher needs your paths. Edit:

```
VibePaste.app/Contents/MacOS/VibePaste
```

Update **two things**:

1. **Line 1 (Shebang)** - Point to your venv Python:

   ```python
   #!/path/to/your/vibepaste/venv/bin/python3
   ```

2. **Line 11 (PROJECT_ROOT)** - Point to your vibepaste folder:
   ```python
   PROJECT_ROOT = Path("/path/to/your/vibepaste")
   ```

### 4. (Optional) Install to Applications

Copy the app to `/Applications` for Dock access:

```bash
cp -R VibePaste.app /Applications/
```

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
3.  **Press and release** the hotkey to **Start Recording**:
    - **Left Option (⌥) + Space** → English (uses turbo model)
    - **Right Option (⌥) + Space** → Bosnian/Other (uses large-v3 model)
4.  Speak your message.
5.  **Press Space alone** to **Stop, Transcribe, and Paste**.

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
  - Ensure `whisper-cli` is executable at `$WHISPER_CPP_HOME/build/bin/whisper-cli`.
  - Check if you granted "Input Monitoring" permissions.
- **Audio Issues**: Check System Settings to ensure the correct microphone is selected as default.

---

## 📜 License

[MIT](LICENSE)
