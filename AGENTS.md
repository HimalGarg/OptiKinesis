# AGENTS.md — OptiKinesis

Shared instructions for every coding agent working in this repository
(Claude Code, OpenAI Codex, Google Antigravity, and others).
Durable project knowledge lives in `PROJECT_CONTEXT.md`; current progress
lives in `TASKS.md`. Neither is loaded automatically by any agent, so read
them yourself (see "Operating rules").

## What this is

Hands-free computer control for people with ALS and similar conditions.
A webcam tracks head pose (MediaPipe Face Landmarker) to move the OS cursor;
deliberate blinks click. A PyQt5 always-on-top overlay adds a virtual
keyboard, camera preview, SOS panel, settings, a status pill, and a 5-point
head calibration screen. An optional Flask dashboard (`--web`) mirrors the
controls in a browser.

## Layout

| Path | Role |
|---|---|
| `main.py` | Entry point. Camera/tracking loop, head-pose math, blink handling, mouse-mover thread, global hotkeys, settings persistence, Flask routes, CLI. |
| `desktop_overlay.py` | All PyQt5 UI: status pill, panels, virtual keyboard, calibration screen, cursor ring, blink-click router (`GazeClickBridge`). |
| `head_calibration.py` | Pure-Python 5-point calibration: capture session, validation, per-user head-angle → screen map. |
| `blink_detector.py` | Pure-Python deliberate-blink state machine. |
| `fatigue_monitor.py` | Rolling-window blink-rate monitor. |
| `voice_commands.py` | Regex intent parser and executor (search, read page, email, alarm, reminder). |
| `web_assist.py` | Pure-Python TinyFish Search/Fetch REST client, reader text cleanup, browser-address capture. UI is `WebPanel` in `desktop_overlay.py`. |
| `word_suggestions.py` | Pure-Python word completion for the on-screen keyboard (built-in word list, no learning). |
| `speech_input.py` | Voice typing for the keyboard's Speak button: `sounddevice` recording, energy endpointing, Google recognition via `SpeechRecognition`. |
| `templates/`, `static/` | Web dashboard (`--web` only). `static/preview.html` is a stale legacy page. |
| `tests/` | `unittest` suites. |
| `face_landmarker.task` | MediaPipe model (downloaded automatically if missing). |

## Setup and commands (Windows, PowerShell)

The project venv is `.venv` with Python 3.10.9. `numpy==2.2.6` requires
Python ≥ 3.10, and the installed MediaPipe 0.10.9 wheel is built for CPython 3.10.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe main.py            # native overlay (default)
.\.venv\Scripts\python.exe main.py --web      # browser dashboard on 127.0.0.1:5000
.\.venv\Scripts\python.exe main.py --help     # --host, --port, --no-browser

# Tests (set the env var so tracked .pyc files are not rewritten)
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -m unittest discover -v
```

Running the app opens the webcam and takes control of the OS cursor when a
face is detected. Do not launch it unattended without telling the user.
`tests/test_web_api.py` and `tests/test_head_calibration.py` import `main`,
which reads `settings.json` and appends to `optikinesis.log`.

## Conventions

- Python, 4-space indent, standard library `unittest`. No linter or
  formatter is configured; match the surrounding style.
- Keep logic that can be tested without a camera or Qt in plain modules
  (`blink_detector.py`, `head_calibration.py`, `fatigue_monitor.py`) and
  add `unittest` coverage there.
- PyQt5 classes live inside the `if PYQT5_AVAILABLE:` block of
  `desktop_overlay.py`. Cross-thread calls into Qt go through signal
  bridges (`NotificationBridge`, `CalibrationBridge`, `GazeClickBridge`).
- `main.py` passes callbacks to the overlay through `configure_overlay(...)`;
  the overlay never imports `main`.
- Use `logging.getLogger("optikinesis...")`, not `print`, in new code.
- User-facing text uses US spelling ("center").
- Persist user settings only through `SETTINGS` + `save_settings()` in
  `main.py` (atomic write to `settings.json`).

## Constraints

- Qt must own the main thread. An unhandled Python exception inside a Qt
  slot or timer aborts the whole process (PyQt5 behavior); wrap risky slot
  code in `try/except` and log.
- Overlay windows must keep `WindowDoesNotAcceptFocus`. The virtual keyboard
  types into whatever app has focus; stealing focus breaks typing.
- Blink clicks on overlay buttons are delivered by `GazeClickBridge`
  calling `QAbstractButton.click()`. New overlay controls must be real
  `QAbstractButton`s (or handle `QTest` mouse clicks) to stay blink-clickable.
- Keep `pyautogui.FAILSAFE = True` (mouse into a screen corner pauses the
  system). The mouse mover deliberately passes `_pause=False` to `moveTo`.
- Caregiver hotkeys are global (pynput): F9 re-center, F12 pause/resume,
  Esc/F12 cancel calibration. Keystrokes injected by the on-screen keyboard
  must stay ignored.
- Accessibility first: large targets, no actions that need precise or
  rapid input, safety confirmation on emergency actions.

## Security

- Secrets (Twilio, SMTP, `TINYFISH_API_KEY`) come from `.env` (see
  `.env.example`). Never commit `.env`, credentials, or tokens, and never log
  the TinyFish key.
- Search queries and page URLs go to TinyFish when the Web panel is used.
  Never send page content, keystrokes or other personal data there beyond
  what a feature needs. Tests must use fake sessions, not the live API.
- Voice typing sends the recorded utterance to Google's speech service. Record
  only while the Speak button is active, never save audio, and keep tests on
  fake streams or synthesized audio.
- `settings.json` holds personal data (emergency contact, calibration) and
  is gitignored. Do not commit it or paste its contents into shared files.
- The Flask server has no authentication and can type keystrokes and trigger
  SOS actions. Keep the default `127.0.0.1` bind; never suggest exposing it.
- Never log or persist keystrokes from the global hotkey listener.
- Camera frames are processed in memory only. Do not save frames or
  screenshots into the repository.

## Operating rules (all agents)

1. **Before starting work:** read `AGENTS.md`, `PROJECT_CONTEXT.md` and
   `TASKS.md`; run `git status` and `git log --oneline -5`; inspect the code
   relevant to the task. Treat the code as the source of truth when it
   disagrees with these files, and fix the files.
2. **Before finishing or handing off:** update `PROJECT_CONTEXT.md` (durable
   knowledge that changed) and `TASKS.md` (progress) with meaningful changes.
3. In `TASKS.md`, record the exact files changed, decisions made, commands
   executed, actual test results (counts and pass/fail as observed), unresolved
   issues, and the next actionable step.
4. Never mark incomplete or unverified work as complete. Say what was not run.
5. Keep durable knowledge (`PROJECT_CONTEXT.md`) separate from temporary task
   progress (`TASKS.md`). Prune `TASKS.md` entries once they stop being useful.
6. Do not duplicate what the code already states; point to the file or symbol.
7. Do not overwrite unrelated user changes or discard uncommitted work.
   Check `git status` before editing; never `git checkout --`, `reset --hard`,
   or `clean` without explicit permission.
8. Do not commit, push, or modify Git history unless the user explicitly
   authorizes it for the current task.
