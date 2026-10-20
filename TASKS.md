# TASKS.md — OptiKinesis

Temporary progress and handoff notes. Update before finishing or handing off.
Durable knowledge belongs in `PROJECT_CONTEXT.md`.

_Last updated: 2026-10-20 by Claude Code._

## Handoff status

- Branch `main`. Everything below dated 2026-10-20 was **committed and pushed
  to `origin/main`** at the user's request (the commit after `be484e2`; see
  `git log`).
- Working tree:
  - Modified: `desktop_overlay.py`, `web_assist.py`, `tests/test_web_assist.py`,
    `README.md`, `OVERLAY_UI_MAP.md`, `AGENTS.md`, `PROJECT_CONTEXT.md`,
    `TASKS.md`.
  - New: `word_suggestions.py`, `tests/test_word_suggestions.py`,
    `speech_input.py`, `tests/test_speech_input.py`. Also modified:
    `requirements.txt`.
  - Generated `.pyc` files in `__pycache__` are also modified. Keep them out
    of commits.
  - `.env` holds the user's TinyFish key. It is gitignored; never commit it.

## Current objective

The user reported Web panel problems and asked for a feature (2026-10-20):

1. **Done:** the Find answers prompt appeared behind the Web panel.
2. **Done:** the keyboard didn't open for Find answers.
3. **Done:** word suggestions while typing on the on-screen keyboard.

## Completed (2026-10-20)

- **Find answers** no longer uses a `QInputDialog`. It shows a question box
  inside the Web panel (`WebPanel._build_ask`, `_start_question`) and opens the
  keyboard in compose mode (`KeyboardOverlayPanel.start_compose`). Enter or
  Search submits; Cancel or closing the keyboard returns to the home view.
  The panel shrinks above the keyboard (`_fit_above_keyboard`).
- **Word suggestions:** `word_suggestions.py` plus a 4-button row above the keys
  (`_refresh_suggestions`, `_accept_suggestion`). Works in normal typing and in
  compose mode; resets on keyboard open and before blink-driven OS clicks.
- **Fixes found while testing:**
  - A stacked-widget minimum height made the panel overlap the keyboard, so
    blinks hit the wrong window. Fixed with an `Ignored` size policy on the
    pages and `SetNoConstraint` on the panel layout.
  - The Web panel was 92% opaque, so text behind it showed through. It is now
    99%, set on `self.shell`.
  - Fetch reported a raw IP as the site name; the reader now uses the
    requested host (found during the live API test).
- Keyboard height grew from 340 px to 400 px for the suggestion row.
- **"Read this page" refused with Brave open** (user report with screenshot).
  Opening a panel from the pill called `activateWindow()`, which after a real
  click made OptiKinesis the foreground app. Removed the call. `WebPanel` now
  tracks the last app used outside OptiKinesis, and `capture_browser_url` brings
  that window back when OptiKinesis is in front and the window is a browser.
  Files: `desktop_overlay.py`, `web_assist.py` (`WindowInfo`,
  `foreground_window`, `activate_window`), `tests/test_web_assist.py`.
  **Not verified with a real click**; simulated clicks can't reproduce it.

- **Voice typing on the keyboard** (user request). New `speech_input.py` and
  `tests/test_speech_input.py`. A Speak button sits at the left of the
  keyboard's suggestion row (`desktop_overlay.py`: `VoiceTypingBridge`,
  `_toggle_voice`, `_insert_dictation`). Installed `SpeechRecognition==3.17.0`
  into `.venv`, and pinned it and `sounddevice==0.5.5` in `requirements.txt`.

## Verification results

| Date | Command / check | Result |
|---|---|---|
| 2026-10-20 | `$env:PYTHONDONTWRITEBYTECODE="1"; .\.venv\Scripts\python.exe -m unittest discover` (in repo, after voice typing) | Ran 79 tests, OK |
| 2026-10-20 | Scratch `speech_e2e.py`: Windows TTS sentence through a fake mic stream, the endpointer and real Google recognition | Stopped on the pause; recognized "please find gentle stretching exercises for people with als" exactly in 1.3–1.5 s; real headset mic opened at 16 kHz (no audio saved) |
| 2026-10-20 | Scratch `live_voice.py`: Speak button on screen with a stubbed recognizer | 13/13 passed |
| 2026-10-20 | Same suite, after the foreground fix | Ran 66 tests, OK |
| 2026-10-20 | Scratch `fg_probe.py`: open the Web panel via the pill button | Foreground unchanged (`code.exe`); panel remembered `code.exe` as the last outside app |
| 2026-10-20 | Scratch `live_keyboard.py`: question box, compose mode, suggestions; PyAutoGUI calls intercepted | 19/19 passed, 3 consecutive runs |
| 2026-10-20 | Scratch `live_web.py`: Web panel with a fake client | 17/17 passed |
| 2026-10-20 | Scratch `smoke_app.py`: real launch, camera stubbed | 12/13. The one failure expects no API key, but `.env` now has one. Expected, not a regression |
| 2026-10-20 | Live `web_assist` calls with the user's key | Search: 5 results in 2.7 s. Fetch: 2 pages in 1.4 s and 3.9 s |

The scratch harnesses are not in the repo. See `PROJECT_CONTEXT.md` for the
approach.

**Not verified:**
- Voice typing with a real person speaking into the microphone. The
  recognition was tested with synthesized speech.
- The new flow under real head tracking and blinks.
- The full Web panel UI against the live API (UI tested with fakes only).
- "Read this page" against a live browser.
- Suggestion accuracy when the user edits text by other means (e.g. physical
  mouse clicks), which don't reset the current word.

## Next steps

1. The user runs `.\.venv\Scripts\python.exe main.py` and tries Web → Find
   answers (typing, suggestions, Search) and normal typing with suggestions.
   Record the results here.
2. Commit when the user authorizes it (exclude `.pyc` and `.env`).
3. Consider replacing the pill Voice button's `QInputDialog` with the same
   in-panel compose pattern, since it may also get hidden behind panels.
