# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source venv/bin/activate                 # all commands assume the venv
python -m pytest                         # full suite (testpaths=tests)
python -m pytest tests/test_paster.py    # one file
python -m pytest tests/test_paster.py::test_backspace_precedes_the_paste
python -m pytest -k "stray"              # by name
```

Running the app:

```bash
./vibepaste.command        # menu bar app (python3 -m src.menubar); refuses to start twice
./run.sh                   # foreground/terminal mode (python src/main.py), Ctrl+C to exit
./tools/build_app.sh --install   # rebuild VibePaste.app and copy it to /Applications
```

`VibePaste.app` executes `Contents/Resources/main.py`, which adds this checkout's
`venv` to `sys.path` by hand and imports from a **hardcoded `PROJECT_ROOT`**. Editing
`src/` therefore changes the installed app immediately — relaunch, don't rebuild.
Rebuild only after changing `tools/launcher.c` or the bundle's own `main.py`. If the
checkout moves, update `PROJECT_ROOT` there and the interpreter path in
`tools/build_app.sh`.

## Prerequisites

Needs a built `whisper.cpp` with **both** `build/bin/whisper-server` (fast path) and
`build/bin/whisper-cli` (fallback), plus the `large-v3-turbo` (English) and `large-v3`
(Bosnian) models. `config.py:find_whisper_cpp()` locates it via `WHISPER_CPP_HOME`, a
parent holding `models/`, or a sibling `whisper.cpp/`.

## Architecture

`VibePaste` in `src/main.py` is the orchestrator; every other module is a collaborator
it wires together. Nothing else holds the app's state.

**The pipeline is deliberately split at the point where audio hits disk.** Hotkey →
`RecordingSession` → audio saved by `RecordingStore` → `TranscriptionWorker` queue →
`Transcriber` → `Paster`. Recording never waits on transcription: a new clip can start
while the previous one is still transcribing, and the worker serialises results so they
paste in the order they were spoken. `RecordingSession.stop()` saves the WAV *before*
anything can fail, so a broken transcription never loses what was said.

Two invariants worth knowing before changing this shape:

- `RecordingSession` is the single owner of `is_recording`. Reintroducing that boolean
  elsewhere is what previously allowed double-stops and racing threads.
- `session.stop()` calls `on_saved` **synchronously**, and only one recording runs at a
  time. Per-recording state can safely be staged on the orchestrator and consumed in
  `_on_recording_saved` (this is how the stray-character count reaches the paste).

### Hotkeys and event-tap constraints

`src/keyboard_listener.py` + `src/hotkey_suppression.py` are the subtlest part of the
codebase, and their module docstrings carry hard-won detail. The rules:

- **Tap callbacks must stay cheap.** They run inside a CoreGraphics event tap; a slow
  callback makes macOS disable the tap, after which key *release* events go missing and
  held-key state is permanently wrong. Touch the in-memory dict and queue the work — no
  disk I/O, no subprocesses, no audio devices.
- **Key order is not the typed order.** macOS delivers Space up to ~38 ms *before* the
  Option modifier, so `_on_press` re-checks the whole held set rather than the key that
  just arrived. Matching only the new key makes the hotkey silently dead.
- **Held keys expire** (`STUCK_KEY_SECONDS`) so a dropped release can't wedge the listener.
- **Suppression is bounded by a deadline, never by a flag** that something must switch
  off. An earlier flag-based version left the space bar dead system-wide whenever a stop
  event went missing.
- **Backspace is only sent on observation, never assumption.** `HotkeySuppressor.was_typed()`
  reports whether a key actually reached the app un-swallowed; the paste path deletes only
  that. A blind Backspace fires when nothing was inserted, and in some apps it navigates
  back or deletes a selection.

Suppression needs an *intercepting* tap (Accessibility). Without it the listener falls
back to a plain listener — hotkeys still work, but nothing is swallowed, which
`hotkey_typed_a_character()` accounts for.

### The tap dies silently, so it is watched

macOS switches a tap off when its callback overruns (`kCGEventTapDisabledByTimeout`),
and pynput calls `CGEventTapEnable` **once, at startup** — it has no handling for the
notification macOS delivers. The tap then stays off forever while nothing looks wrong:
the run loop spins, the thread lives, `is_alive()` and `is_running()` keep saying True.
For months the only cure was restarting the app.

`src/event_tap.py` fixes that, and three things there must not be undone:

- **`RecoverableListener` exists only to keep the tap handle.** pynput leaves it in a
  local variable inside `_run()`. Swap it back for `keyboard.Listener` and there is
  nothing left to re-enable.
- **`is_running()` is not a health check.** It reports the thread, which outlives the
  tap. `is_healthy()` is the one that asks whether events are still being delivered;
  the DIAG line prints both because `running=True` is what made this bug invisible.
- **Recovery only runs on a tap that has been seen working.** Without Input Monitoring
  the tap is created but never enabled, and recovering that means rebuilding a listener
  every few seconds forever, which no amount of retrying can fix. A failed recovery
  also backs off before retrying.

Prevention matters as much as recovery: **nothing in the tap callbacks may write to the
log.** Logging is file I/O, and file I/O in the callback is what gets the tap killed.
Dropped stuck keys are handed to the dispatch worker and logged there.

### CoreAudio can deadlock, so nothing waits on it

CoreAudio's HAL mutex deadlocks. Measured on a live app that looked perfectly
healthy — menu bar responsive, event tap delivering keys:

```
hotkey-dispatch  Pa_OpenStream -> AudioDeviceCreateIOProcID -> HALB_Mutex::Lock
audio-teardown   AudioOutputUnitStop -> StopIOProc          -> HALB_Mutex::Lock
```

Nothing in the process can undo that — the mutex is process-wide, so retrying on a
fresh thread blocks identically. Recording is gone until the app restarts. The rules
that keep it from taking everything else down with it:

- **Every CoreAudio call lives on the `audio-device` thread** (`src/audio_device.py`),
  which nobody waits on. An earlier fix moved only the stream *close* to a background
  thread; the deadlock simply moved to the next *open*, which was still on the dispatch
  thread, and every hotkey died again. Moving one call is not enough — move them all.
- **A recording counts as started at the keypress**, before the microphone opens. A
  healthy open is ~70ms but ~2.5s the first time CoreAudio wakes, and waiting on it puts
  that delay on the hotkey and never returns at all when it wedges. `RecordingSession`
  arms its overlay and timers *before* asking for the device, because the failure can
  arrive before `start()` returns and the abort has to have something to undo.
- **A microphone that never arrives is given up on** (`wedged_seconds`) and reported
  through `on_failed` to the menu bar, not just the log. It is the one failure the app
  cannot work around, so it is the loudest.

`src/hotkey_dispatch.py` is the net for the next such bug: a callback that overruns gets
a replacement worker so the queue drains again. Replacements are **capped** — the stuck
thread is blocked in C and cannot be killed, so each one is leaked.

### Subprocesses

Two long-lived children. **Only one of them dies with the parent** — the difference
matters more than it looks:

- **Overlay** (`src/overlay/ui_app.py`) — AppKit insists on owning the main thread, so
  the visual feedback runs as one persistent process driven by one-line stdin commands
  (`record`/`processing`/`hide`/`quit`). Closing stdin kills it, so it cannot outlive
  the parent. It previously forked a fresh interpreter per show; don't go back to that.

  It shows animated Data reactions from `assets/data/` (`src/overlay/animation.py`).
  macOS decodes animated WebP natively through ImageIO, so there is no decoder to
  bundle. Four things there are deliberate:

  - **The pools are grouped by what is on screen, not by filename** — `starting_3` is a
    smile, `starting_2` a celebration — and **how often you get a smile is `SMILE_CHANCE`,
    not a side effect of list lengths.** An earlier version merged both pools and picked
    at random; with four smiles against three listening clips it smiled more often than
    not, and adding a clip silently changed the feel.
  - **Clips are 220px wide and drawn at 110pt**, i.e. 1:1 on a Retina display. Shrinking
    the files to "save space" reintroduces resampling and softness.
  - **A missing clip is not an error.** `pick()` returning None makes the overlay fall
    back to the drawn dots and spinner, so an absent `assets/data/` costs the animation
    and nothing else.
  - **The processing dots pulse mirror-symmetrically**, outer two in phase, and sit on
    `STRIP_CENTRE_Y`, derived from the progress bar's own geometry. A left-to-right wave
    swings the bright mass ±9pt off centre; measured, not guessed.
- **whisper-server** (`src/whisper_server.py`) — resident per model, started lazily,
  unloaded after `SERVER_IDLE_UNLOAD_SECONDS`. `Transcriber` escalates through three
  *different* mechanisms (resident server → restarted server → `whisper-cli` fork) so a
  wedged server can't yield three identical failures. Timeouts scale with audio duration.

  **It does not read stdin and so it survives its parent.** A crash or `kill -9` leaves
  it reparented to launchd holding ~2.9GB for `large-v3`, with the idle reaper — a
  daemon thread *in the app process* — dead alongside the parent, so it never unloads.
  Nineteen servers started and four stopped in one session once filled a 24GB machine
  and forced a power cycle.

  Three rules prevent that, and none of them may be dropped:

  - **Adopt before spawning** (`src/server_acquisition.py`). A restart reuses a healthy
    server found in the process table rather than loading a second copy of the weights;
    `src/server_discovery.py` identifies servers by the model and `--port` on their
    command line, so **changing the spawn command's shape breaks adoption**.
  - **Reclaim only what we own** (`src/server_ownership.py`). Discovery may look at
    every process; the sweep may only kill pids we recorded. A `whisper-server` a
    developer started by hand must survive us.
  - **Refuse rather than duplicate.** If a live server can be neither adopted nor
    reclaimed, `ensure_started()` returns False and the CLI fallback takes over.

  Cleanup on quit hangs on a single hook: `rumps.events.before_quit` in `src/menubar.py`.
  It is the *only* one that fires — measured, not assumed. `atexit` never runs
  (`NSApp.terminate_()` skips `Py_FinalizeEx`, which is also why the bundle's `finally:`
  never removes the PID file), and a SIGTERM handler never runs either: the AppKit run
  loop parks the main thread, so `kill <pid>` does not even stop the app. Only `kill -9`
  does — and nothing can clean up after that, which is why adoption is the real net.

### macOS bundle identity

The bundle executable is a compiled binary (`tools/launcher.c`), not a `#!` script. When
it was a script, macOS treated *Python* as the application: `Info.plist` went unread and
permissions were attributed to the interpreter. The symptoms were silent — no menu bar
icon, recordings that came out as silence. Relatedly, the bundle must not re-exec into
another binary after launch, or LaunchServices check-in never completes and `NSStatusBar`
hands back a zero-height status item that never draws.

`build_app.sh` ad-hoc signs the bundle so Accessibility, Input Monitoring and Microphone
grants survive a rebuild.

Microphone permission matters more than it looks: without it macOS feeds the app
**silence rather than failing**, and the transcript is whatever the model invented.
Recordings with no captured sound are surfaced as such in the recordings window instead
of being presented as transcripts.

## Conventions

Tests exercise the tap-side callbacks directly (`listener._on_press(...)`) — starting a
real pynput listener would need Input Monitoring and real keypresses. Test names are full
sentences describing the guarantee, and comments explain *why* a rule exists, usually
naming the failure that motivated it. Keep that when editing these files.

`test_keyboard.py` at the repo root is a manual diagnostic script, not part of the suite.
