# Overlay UI Code Map — `desktop_overlay.py`

> **Quick reference**: Which line in the code changes what on screen.
> Use with `preview_overlay.py` for live-reload testing.
>
> **Maintenance note (September 2026):** the class and method names in this
> document remain the authoritative navigation points, but the historical line
> numbers below move as the UI evolves. Search for the named symbol rather than
> relying on a numeric line. The current overlay also includes
> `NotificationToastOverlay`, configurable single/double-blink mode, cursor
> scope, post-click lock delay, and Calibrate/Pause/Exit safety controls.
> The full-screen 5-point calibration lives in `HeadCalibrationOverlay`; its
> capture logic and math are in `head_calibration.py`.

---

## 📐 Global Layout Constants (Lines 34–38)

These control the **positioning of everything** on screen.

| Variable | Line | What it controls |
|---|---|---|
| `TOP_MARGIN` | 34 | Gap (px) between top of screen and the floating bar |
| `BAR_IDLE_HEIGHT` | 35 | Height of the **idle** "OVERLAY" rectangle |
| `BAR_EXPANDED_HEIGHT` | 36 | Height of the **expanded** bar (with buttons) |
| `PANEL_TOP_OFFSET` | 38 | Y-position where all panels (keyboard, SOS, etc.) appear — automatically calculated from `TOP_MARGIN + BAR_EXPANDED_HEIGHT + 20` |

> [!TIP]
> If your panels overlap the bar, increase the `+ 20` gap in `PANEL_TOP_OFFSET`.

---

## 🟢 Gaze Cursor Ring — `CursorOverlay` (Lines 92–135)

The green circle that follows the mouse/gaze cursor.

| What to change | Where |
|---|---|
| **Ring size** | Line 95: `radius=80` — increase for bigger ring |
| **Ring color** | Line 132: `(0, 255, 0, 255)` — BGRA format. `(0, 255, 0)` = green |
| **Ring thickness** | Line 132: last arg `10` — stroke width in pixels |
| **Update speed** | Line 118: `self.timer.start(10)` — ms between position updates |
| **Show/hide** | Controlled by `_settings["overlay_enabled"]` (Line 121) |

---

## 🔲 Floating Control Pill — `FloatingControlBar` + `PillButton`

The top-center pill. Collapsed, it shows the brand and the live tracking
state. When the cursor rests on it, it grows smoothly into a toolbar, then
folds back after the cursor leaves unless a panel or the voice prompt is open.

Everything is custom painted with antialiasing; there is no stylesheet. The
window keeps one fixed size and only the painted pill animates inside it, so
the bar never jumps. A window mask keeps the unused transparent area
click-through, and `accepts_gaze_point()` lets blink clicks there fall through
to the app underneath.

### Dimensions and timing (class constants on `FloatingControlBar`)

```python
IDLE_W = 196                      # Collapsed pill width
IDLE_H = BAR_IDLE_HEIGHT          # Collapsed height (global constant, currently 52)
EXPANDED_H = BAR_EXPANDED_HEIGHT  # Expanded height (global constant, currently 84)
BRAND_W = 168                     # Space for the status dot, name and status line
EXPANDED_W                        # Computed from BRAND_W, button size and gaps
EXPAND_MS = 280                   # Expand animation length
COLLAPSE_MS = 220                 # Collapse animation length
COLLAPSE_DELAY_MS = 1200          # Wait after the cursor leaves before folding
HOVER_SLOP = 10                   # Extra px around the expanded pill that still counts as "on it"
```

### Status line

| What to change | Where |
|---|---|
| **State labels and dot colours** | `PILL_STATUS_STYLES` (tracking, no face, paused, mouse off, no camera) |
| **Where the state comes from** | `get_tracking_status()` in `main.py`, passed as `status_provider` |
| **Temporary messages** (voice results) | `show_status_message(text)`; shown for `MESSAGE_MS` |
| **Brand name, fonts** | `_paint_brand()` and the fonts set in `__init__` |

### Buttons

| What to change | Where |
|---|---|
| **Add / remove / rename buttons** | `BUTTON_SPECS` — `(panel key, icon name, label, is_danger)` |
| **Icons** | `_draw_pill_icon()` — 24-unit line icons: mic, keyboard, camera, alert, sliders |
| **Button size, corner radius** | `PillButton.WIDTH`, `HEIGHT`, `RADIUS` |
| **Hover / open-panel / click-flash look** | `PillButton.paintEvent()` |

Button states: pointing at a button shows an accent ring; an open panel shows
a tinted fill with a small bar under the label; every press flashes briefly
so the user can see a blink registered.

### Colours

`PILL_TEXT`, `PILL_TEXT_MUTED`, `PILL_ICON`, `PILL_ACCENT` (cyan) and
`PILL_DANGER` (SOS red) near the top of the pill section. The pill surface
gradient and hairline edge are in `_paint_surface()`, the soft shadow in
`_paint_shadow()`.

### Hover behaviour

The pill polls the cursor every `POLL_MS` instead of relying on enter/leave
events, which is steadier for a head-driven cursor. See `_poll()` and
`_collapse_if_idle()`.

---

## 🎯 5-Point Calibration Screen — `HeadCalibrationOverlay`

Full-screen, nearly opaque screen that shows one target at a time: the
center, then the four corners. Each point is captured automatically once the
head has been steady for a second. The pill, panels, mini camera and cursor
ring are hidden while it runs and restored afterwards.

| What to change | Where |
|---|---|
| **Target positions / corner inset** | `TARGET_INSET` and `default_targets()` in `head_calibration.py` |
| **Timing** (travel, settle, steady time, timeout) | `CalibrationSession.__init__` defaults in `head_calibration.py` |
| **How still the head must be** | `max_spread_deg` (degrees) in `CalibrationSession` |
| **Minimum head movement accepted** | `MIN_SPAN_DEG` in `head_calibration.py` |
| **Instruction and status text** | `_paint_text()` and `_status_lines()` |
| **Target ring, pulse, colors** | `_paint_target()`, `RING_RADIUS`, `AMBER`, `GREEN` |
| **Backdrop darkness** | alpha in `paintEvent()` (currently 240 of 255) |
| **First-run auto start** | `_maybe_auto_calibrate()` and `AUTO_CALIBRATION_WAIT_S` |

---

## ⌨ Keyboard Panel — `KeyboardOverlayPanel` (Lines 304–624)

### Window Size (Lines 307–315)

```python
width=min(1400, int(SCREEN_W * 0.85))   # Panel width
height=min(340, int(SCREEN_H * 0.44))   # Panel height
self.header.hide()  # Line 315 — title bar hidden for compact look
```

### Layout Spacing (Lines 323–343)

| What to change | Where |
|---|---|
| **Gap between keyboard and action buttons** | `wrapper.setSpacing(0)` |
| **Gap between key rows** | `keyboard_rows.setSpacing(0)` |
| **Keyboard vs action column ratio** | `wrapper.addLayout(left, 5)` and `wrapper.addLayout(right, 1)` — change the numbers to rebalance |

### Text Input Area (Lines 335–339)

| What to change | Where |
|---|---|
| **Text area height** | `setFixedHeight(54)`; the widget is hidden and retained as the action-button text buffer |
| **Placeholder text** | Line 338: `"> TYPE WITH GAZE + BLINK"` |

### Key Sizes (Lines 395–401)

Keys use expanding size policies and per-key stretch factors in each
`build_row()` specification. Change the `stretch` value to make a key wider;
row and panel height determine key height.

### Keyboard Stylesheet (Lines 345–393)

| CSS Selector | What it styles |
|---|---|
| `QTextEdit#textArea` | The text input box (background, border, font) |
| `QTextEdit#textArea:focus` | Input box when focused (border color) |
| `QPushButton#keyBtn` | Individual keyboard keys (height, colors, radius) |
| `QPushButton#keyBtn:hover` | Key hover glow |
| `QPushButton#keyBtn[active="true"]` | Active state for Caps Lock / Shift toggle |
| `QPushButton#actionBtn` | Right-side action buttons (SEARCH GOOGLE, etc.) |
| `QPushButton#actionBtn:hover` | Action button hover glow |

### Key Rows (Lines 417–492)

Each `build_row([...])` call defines one row of keys. Edit the dicts to add/remove/rename keys:
```python
{"label": "q", "key": "q"}                    # Regular key
{"label": "Backspace", "key": "Backspace", "size": "xl"}  # Wide key
{"label": "1", "key": "1", "shift": "!"}       # Key with shift symbol
```

### Right-Side Action Buttons (Lines 494–507)

```python
google_btn = QtWidgets.QPushButton("SEARCH\nGOOGLE")   # Line 494 — two-line text
right.addWidget(google_btn, 1)  # stretch factor 1 = equal height with others
```

All three buttons use stretch factor `1`, so they divide the available height equally.

---

## 📷 Camera Panel — `CameraOverlayPanel` (Lines 627–665)

| What to change | Where |
|---|---|
| **Panel size** | Line 631: `width=min(1300, ...)`, `height=min(860, ...)` |
| **Title text** | Line 631: `"CAMERA MODULE"` |
| **Frame refresh rate** | Line 646: `self.timer.start(40)` — 40ms = ~25 FPS |
| **Placeholder text** | Line 633: `"Camera feed initializing..."` |
| **Video area border** | Lines 635–641 (inline stylesheet) |

---

## 🚨 Emergency Panel — `EmergencyOverlayPanel` (Lines 668–753)

| What to change | Where |
|---|---|
| **Panel size** | Line 672: `width=min(1100, ...)`, `height=min(760, ...)` |
| **Title text** | Line 672: `"EMERGENCY MODULE"` |
| **Default contact** | Line 44: `_settings["emergency_contact"]` |
| **Contact input styling** | Lines 678–686 |
| **SOS button labels** | Lines 722–725: `"MSG CONTACT"`, `"CALL CONTACT"`, `"DIAL 100"`, `"DIAL 112"` |
| **SOS button styling** | Lines 712–719 (red themed) |
| **Button grid layout** | Line 705: `grid.setSpacing(12)` |
| **Button min height** | Line 710: `setMinimumHeight(102)` |
| **Status text** | Line 728: `"Emergency actions ready."` |

---

## ⚙ Control/Settings Panel — `ControlOverlayPanel` (Lines 756–828)

| What to change | Where |
|---|---|
| **Panel size** | Line 760: `width=min(1100, ...)`, `height=min(760, ...)` |
| **Title text** | Line 760: `"CONTROL MODULE"` |
| **Slider range** | Line 763: `setRange(3, 15)` |
| **Blink sensitivity range** | Line 767: `setRange(0.15, 0.35)` |
| **Lock delay range** | Line 772: `setRange(0.5, 5.0)` |
| **Setting label style** | Line 785 |
| **Button labels** | Lines 792, 796, 800 |
| **Button styling** | Lines 806–814 |

---

## 🏗 Panel Base — `OverlayPanelBase` (Lines 224–301)

**Shared base class** used by Keyboard, Camera, Emergency, and Control panels. Changes here affect **ALL panels**.

| What to change | Where |
|---|---|
| **Background color** | Line 270: `rgba(10, 16, 26, 236)` |
| **Border** | Line 271: `border: 6px solid #2f4b69` |
| **Corner rounding** | Line 272: `border-radius: 18px` |
| **Header background** | Line 275 |
| **Title color/size** | Lines 281–284 |
| **Close button (✕)** | Lines 253–257: size, text |
| **Close button styling** | Lines 286–297 |
| **Body padding** | Line 262: `setContentsMargins(10, 8, 10, 10)` |
| **Body spacing** | Line 263: `setSpacing(6)` |
| **Panel position** | Line 300: centered using `PANEL_TOP_OFFSET` |

> [!IMPORTANT]
> To hide the title bar on a specific panel (like the Keyboard does), add `self.header.hide()` in that panel's `__init__`.

---

## 🐛 Blink Debug HUD — `BlinkDebugOverlay` (Lines 138–190)

Only visible when `DEBUG_BLINK_HUD = True` (Line 40).

| What to change | Where |
|---|---|
| **HUD size** | Lines 154–155 |
| **Border color** | Line 165: `#ff8b57` (orange) |
| **Title** | Line 175: `"BLINK DEBUG HUD"` |
| **Font** | Line 183: `font-family: 'Consolas'` |

---

## 🔗 Quick CSS Color Reference

| Color | Hex | Used for |
|---|---|---|
| Cyan accent | `#2dd4ff` | Hover glows, active states, titles |
| Green accent | `#3dda9b` | Status dots, brand label |
| Dark background | `rgba(8–20, 14–26, 24–48, 230–245)` | Bar/panel/button backgrounds |
| Light text | `#e0f0ff` / `#eef7ff` / `#ffffff` | General text and labels |
| Muted text | `#7a99b8` / `#9fb6cf` | Status labels, placeholders |
| Border blue | `#2d4a6a` / `#2f4b69` / `#365472` | Panel/button borders |
| SOS red bg | `rgba(80–108, 20–32, 20–32)` | Emergency button backgrounds |
| SOS red border | `#7c3f3f` / `#8b3a3a` | Emergency button borders |
| SOS red text | `#ffd4d4` / `#ffdcdc` | Emergency button text |
| SOS red hover | `#ff6161` | SOS hover accent |

---

## 🧪 Testing Workflow

```
1.  Run:  py preview_overlay.py
2.  Edit desktop_overlay.py in your editor
3.  Save → UI auto-reloads in ~1 second
4.  Ctrl+C to stop
```
