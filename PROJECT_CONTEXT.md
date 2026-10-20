# PROJECT_CONTEXT.md — OptiKinesis

Durable knowledge for any agent. Update when design, architecture or known
issues change; put session progress in `TASKS.md` instead. For product and
pitch material see `README.md` and `PROJECT_DESCRIPTION.md`.

## Purpose

Software-only assistive input for people who cannot use their hands
(ALS/MND first). Built by Team Revolutionaries for Samsung Solve for
Tomorrow 2026. Targets an ordinary laptop webcam on Windows.

## Runtime architecture

- **Threads (native mode):** Qt main thread runs the overlay.
  `face-tracking` runs `gen_frames()` (camera → MediaPipe → pose → blink).
  `mouse-mover` pushes the cursor toward `mouse_target` (~65 updates/s,
  limited by Windows timer resolution). `safety-keys` is a pynput global
  keyboard hook.
- **Web mode (`--web`):** same tracking threads; Flask serves
  `templates/index.html` instead of launching Qt. Blink clicks fall back to
  plain OS clicks; overlay notifications only reach the log.
- **Head pose:** five landmarks (cheeks 234/454, forehead 10, chin 152,
  nose 1) define a head frame; the forward vector is averaged over 12 frames
  and converted to raw yaw/pitch, which sit near 180° at neutral. This is head
  pointing, not eye gaze.
- **Pose → screen:** `map_pose_to_screen()` in `main.py`. With a saved
  calibration it uses `HeadCalibration.map()`; otherwise a fixed linear map
  (±25° yaw, ±18° pitch, scaled by `cursor_scope`, center offset, `INVERT_X`).
  Result is clamped, then EMA-smoothed (`cursor_speed`).
- **Blink → click:** 4-point eye-openness ratio averaged over both eyes vs
  `blink_sensitivity`; `DeliberateBlinkDetector` filters duration (0.15–1.2 s),
  counts blinks in a window, and enforces a post-click lock. The cursor
  freezes while eyes are closed and the click uses the pre-blink position.
  `dispatch_blink_click()` → `GazeClickBridge`: overlay button → `.click()`;
  otherwise an OS click via PyAutoGUI; on Windows an I-beam cursor auto-opens
  the virtual keyboard.
- **Settings:** `DEFAULT_SETTINGS` in `main.py`, persisted atomically to
  `settings.json` (gitignored). `system_paused` is never persisted.

## Design decisions

- **5-point calibration (Oct 2026).** Center plus four corners inset 10%
  from the edges. Map = four affine triangles fanning from the center, exact
  at all five points and continuous across edges; it extrapolates beyond the
  corners. Points are captured automatically after ~1 s of steady pose
  (spread ≤ 1.5°), so no blinks are needed. Readings are rejected if the
  side-to-side or top-to-bottom span is < 6° (`MIN_SPAN_DEG`) or the corners
  don't wind consistently around the center. A consistent mirror is
  accepted, so the map learns left/right itself. Stored in
  `settings.json["head_calibration"]` (versioned). Re-center (F9, Settings
  button, web `/calibrate`) translates the whole map. It auto-starts on first
  native launch once a face is seen (waits up to 60 s). Cursor movement and
  blink clicks are suspended while it runs.
- **Hotkeys:** F9 re-center, F12 pause/resume, Esc/F12 cancel calibration.
  The old global `c` hotkey was removed because typing "c" anywhere,
  including via the on-screen keyboard, recalibrated the cursor. Injected
  keystrokes are ignored.
- **Status pill (`FloatingControlBar`):** fixed-size translucent window. The
  pill is custom painted (QPainter, antialiased) and animates inside it; a
  window mask plus `accepts_gaze_point()` keep the transparent margin
  click-through for OS and blink clicks. It polls the cursor every 40 ms
  instead of using enter/leave events. Buttons are `PillButton`
  (`QAbstractButton`). It shows live tracker state from `get_tracking_status()`.
- **Mouse mover** calls `pyautogui.moveTo(..., _pause=False)`; PyAutoGUI's
  default 0.1 s pause otherwise caps updates at ~9/s.
- **Web panel (TinyFish, Oct 2026).** Answer cards use the TinyFish Search API
  (`GET https://api.search.tinyfish.ai`) and the clean reader uses the Fetch
  API (`POST https://api.fetch.tinyfish.ai`, markdown). Both authenticate with
  `X-API-Key` and have free tiers: Search 30/min and 12,000/day; Fetch 150
  URLs/min and 1,000/day. The client in `web_assist.py` calls REST with
  `requests` because the official `tinyfish` SDK needs Python 3.11+.
  Reader text is cleaned markdown with images and link targets removed, shown
  in a `QTextBrowser` via `setMarkdown`. "Read this page" gets the URL by
  sending Ctrl+L, Ctrl+C, Esc to the foreground window, only when it is a known
  browser process, and restores the clipboard afterwards. Voice searches
  ("search X on Google", "find X", "look up X", questions) show answer cards
  through `VoiceCommandExecutor.set_web_handlers`. Without the overlay or a
  key they fall back to opening Google.
- **No pop-up dialogs for hands-free input (Oct 2026).** Overlay panels re-raise
  themselves every 450 ms, so normal dialogs end up hidden behind them. Find
  answers instead uses an in-panel question box with the keyboard in compose
  mode (`KeyboardOverlayPanel.start_compose`): keys edit the keyboard's hidden
  buffer and call back, and nothing goes to the app in front. The Web panel
  shrinks to sit above the keyboard while a question is typed. The pill's Voice
  button still uses a `QInputDialog`.
- **Voice typing (Oct 2026).** The keyboard's Speak button (`_toggle_voice`)
  records one utterance with `sounddevice` at 16 kHz mono and stops after
  about 1.1 s of silence, 6 s with no speech, 15 s total, or a second press.
  `speech_input.Endpointer` measures room noise from the first 0.3 s, ignoring
  frames that are already speech, and caps the threshold at 1500 RMS so
  immediate talkers aren't cut off. Text comes from Google via
  `SpeechRecognition.recognize_google` (free, no key, needs internet) and goes
  through `insert_text`, so it lands in compose fields or the app in front.
  Chosen over Windows Win+H dictation because the text must come back into
  OptiKinesis for the question box.
- **Word suggestions (Oct 2026).** `word_suggestions.py` uses a built-in
  frequency-ordered list and never learns from typing, because passwords may be
  typed on the keyboard. The current word comes from the keyboard's hidden
  buffer after `_context_start`, which resets when the keyboard opens and before
  every blink-driven OS click. A physical-mouse click elsewhere does not reset
  it.

## Failed approaches and lessons

- Qt stylesheet `border-radius` on translucent top-level windows renders
  jagged edges, and resizing then moving a frameless window makes it visibly
  jump. That is why the pill is painted and its window never resizes.
- `QTimer.singleShot` scheduled before a `QApplication` exists never fires.
  Test harnesses must create the app first (`launch_overlay` reuses it).
- A calibration backdrop at alpha 222 still showed dark-themed screens
  clearly; it is now 240.
- In PowerShell, `git commit -F -` with a here-string failed (the message was
  treated as a pathspec). Write the message to a file and use `-F <file>`.
- Running tests without `PYTHONDONTWRITEBYTECODE=1` rewrites the tracked
  `__pycache__/*.pyc` files.
- `FloatingControlBar.toggle_panel` used to call `panel.activateWindow()`. After
  a real user click, Windows let that make OptiKinesis (`python.exe`) the
  foreground app, so "Read this page" refused with "Open the page in Chrome…"
  even with Brave open. Typing could also go astray. The call is removed, and
  `WebPanel` now remembers the last app used outside OptiKinesis
  (`_track_foreground`) so `capture_browser_url` can switch back to it. A
  simulated click doesn't reproduce this, because Windows only grants
  foreground rights after real input.
- A `QStackedWidget` takes the largest minimum height of all its pages, which
  stopped the Web panel from shrinking above the keyboard. Its pages now use an
  `Ignored` size policy.
- Appending a second `QFrame#panelShell` rule to a panel's stylesheet did not
  override the base rule. Set the style on the shell widget itself.
- An exception inside a test harness's `QTimer` callback aborts the process with
  exit code `-1073740791` and loses buffered output. Log progress to a file.
- To exercise overlay UI without side effects, build widgets directly in a
  throwaway script: create the `QApplication` first, pass fake clients to
  `configure_overlay`, replace `desktop_overlay.capture_browser_url` and
  `GazeClickBridge._fallback_os_click`, and drive blinks with
  `GazeClickBridge._handle_blink_click(x, y)`. Never let a test send real
  keystrokes or clicks to the user's desktop.

## Known issues (open)

- **Docs drift:** README/PROJECT_DESCRIPTION still list deleted
  `eye_blink.py` and `preview_overlay.py` and say the default is two blinks;
  code default is `BLINKS_TO_CLICK = 1`, `BLINK_WINDOW = 1.0`. OVERLAY_UI_MAP's
  workflow references the deleted `preview_overlay.py`.
- **Single-blink default** makes involuntary blinks of 0.15–1.2 s click.
- **SOS reliability:** Twilio call needs `.env` credentials and reports
  "triggered" even on failure; Dial 100/112 open `tel:` links that do nothing
  on most PCs; WhatsApp needs WhatsApp Web logged in, presses Enter blindly
  after `WHATSAPP_SEND_DELAY`, and puts the number in the URL unencoded
  (a leading `+` decodes to a space).
- **Native voice** is a typed text box; speech recognition exists only in web
  mode (browser Web Speech API).
- **Fatigue:** sensitivity reduction is commented out in `gen_frames()`, yet
  the toast says sensitivity was reduced; click blinks count toward fatigue.
- **Reminders/alarms** only notify through the Qt toast, so they are invisible
  in web mode; they can't carry a message and are lost on restart.
- **Virtual keyboard:** CLR sends Ctrl+A, Backspace (wipes the focused
  document); TYPE EXTERNAL retypes text already typed live.
- **Voice parser** misses phrasings such as "remind me in five minutes" and
  spoken email addresses.
- **Unverified:** the native emergency-contact text box probably can't
  receive keyboard input because overlay windows refuse focus.
- **Accuracy factors:** the eye ratio mixes width- and height-normalized
  coordinates (threshold depends on camera aspect ratio); MediaPipe runs in
  IMAGE mode per frame.
- **Repo hygiene:** `__pycache__/*.pyc` files are tracked despite
  `.gitignore`. The venv has both `opencv-python` and `opencv-contrib-python`.
- The calibration and pill have been tested with simulated input and on
  screen, but not yet by a real user in front of the camera.
- **Web panel API:** verified live on 2026-10-20 with a real key (stored only
  in the gitignored `.env`). The bare-host endpoints and documented response
  fields work as parsed. Fetch can report a raw IP as `final_url`, so the
  reader falls back to the requested URL's host for the site name.
- **Web panel limits:** after "Read this page", focus stays in the browser's
  address bar until the user clicks the page. Pages behind a login can't be
  read. Answer cards and the reader exist only in the native overlay, not in
  `--web` mode. URLs with query strings are sent to TinyFish as-is.
