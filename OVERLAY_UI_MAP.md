# Overlay UI Code Map — `desktop_overlay.py`

> **Quick reference**: Which line in the code changes what on screen.
> Use with `preview_overlay.py` for live-reload testing.

---

## 📐 Global Layout Constants (Lines 34–38)

These control the **positioning of everything** on screen.

| Variable | Line | What it controls |
|---|---|---|
| `TOP_MARGIN` | 34 | Gap (px) between top of screen and the floating bar |
| `BAR_IDLE_HEIGHT` | 35 | Height of the **idle** "OVERLAY" rectangle |
| `BAR_EXPANDED_HEIGHT` | 36 | Height of the **expanded** bar (with buttons) |
| `PANEL_TOP_OFFSET` | 38 | Y-position where all panels (keyboard, SOS, etc.) appear — automatically calculated from `TOP_MARGIN + BAR_EXPANDED_HEIGHT + 14` |

> [!TIP]
> If your panels overlap the bar, increase the `+ 14` gap in `PANEL_TOP_OFFSET`.

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

## 🔲 Floating Control Bar — `FloatingControlBar` (Lines 831–1097)

The main top-center bar with two states: **idle** and **expanded**.

### Bar Dimensions (Lines 839–843)

```python
IDLE_W = 280          # Width of idle "OVERLAY" rectangle
IDLE_H = BAR_IDLE_HEIGHT   # Height (uses global constant, currently 58)
EXPANDED_W = 780      # Width of expanded button bar
EXPANDED_H = BAR_EXPANDED_HEIGHT  # Height (uses global constant, currently 100)
COLLAPSE_DELAY_MS = 1500   # How long (ms) before bar auto-collapses after cursor leaves
```

### Idle State — "OVERLAY" Rectangle (Lines 869–883)

This is the small pill shown when nothing is hovered.

| What to change | Where |
|---|---|
| **"OVERLAY" text** | Line 879: `QtWidgets.QLabel("OVERLAY")` |
| **Green dot** | Line 875: `QtWidgets.QLabel("●")` |
| **Inner padding** | Line 872: `setContentsMargins(16, 0, 16, 0)` |

**Idle text styling** (Lines 945–956 inside stylesheet):
```css
QLabel#idleDot    → green dot color, font-size, font-weight
QLabel#idleLabel  → "OVERLAY" text color, font-size, font-weight, letter-spacing
```

### Expanded State — Button Bar (Lines 885–929)

Shown when the cursor hovers over the idle bar.

| What to change | Where |
|---|---|
| **Brand text** | Line 892: `"●  OPTIKINESIS"` |
| **Inner padding** | Line 888: `setContentsMargins(18, 8, 18, 8)` |
| **Button spacing** | Line 889: `setSpacing(10)` |
| **Separator line** | Lines 897–901 |

### Action Buttons (Lines 903–920)

The 5 clickable buttons in the expanded bar.

| What to change | Where |
|---|---|
| **Button labels** | Lines 905–909: `("🎤", "Voice", ...)` — first arg = icon, second = label |
| **Button size** | Line 916: `btn.setFixedSize(110, 64)` — width × height in px |
| **Add/remove buttons** | Edit the `buttons_spec` list (Lines 904–910) |
| **SOS flag** | 4th element in tuple — `True` gives it the red SOS styling |

### Status Badge (Lines 924–927)

The "Ready" text on the right side of the expanded bar.

| What to change | Where |
|---|---|
| **Default text** | Line 925: `"Ready"` |
| **Updated programmatically** | via `self.status.setText(...)` throughout the class |

### Bar Stylesheet (Lines 935–996)

All visual styling for the floating bar is in one stylesheet block:

| CSS Selector | What it styles |
|---|---|
| `QFrame#barShell` | The bar's **background gradient, border, border-radius** |
| `QLabel#idleDot` | Green dot in idle state |
| `QLabel#idleLabel` | "OVERLAY" text in idle state |
| `QLabel#brandLabel` | "● OPTIKINESIS" text in expanded state |
| `QPushButton#barBtn` | Normal action buttons (Voice, Keys, Cam, Config) |
| `QPushButton#barBtn:hover` | Hover glow effect on normal buttons |
| `QPushButton#sosBarBtn` | SOS button (red themed) |
| `QPushButton#sosBarBtn:hover` | Hover glow on SOS button |
| `QLabel#barStatus` | "Ready" status badge |

### Hover Behavior (Lines 1001–1025)

| What to change | Where |
|---|---|
| **Expand on hover** | `enterEvent()` at Line 1003 |
| **Collapse delay** | `COLLAPSE_DELAY_MS = 1500` at Line 843 |
| **Keep open when panel visible** | `leaveEvent()` at Line 1007 — checks `any(p.isVisible() ...)` |

---

## ⌨ Keyboard Panel — `KeyboardOverlayPanel` (Lines 304–624)

### Window Size (Lines 307–315)

```python
width=min(1420, int(SCREEN_W * 0.82))   # Panel width
height=min(540, int(SCREEN_H * 0.58))   # Panel height
self.header.hide()  # Line 315 — title bar hidden for compact look
```

### Layout Spacing (Lines 323–343)

| What to change | Where |
|---|---|
| **Gap between keyboard and action buttons** | Line 324: `wrapper.setSpacing(8)` |
| **Gap between key rows** | Line 342: `keyboard_rows.setSpacing(4)` |
| **Keyboard vs action column ratio** | Lines 329 / 333: `wrapper.addLayout(left, 6)` and `wrapper.addLayout(right, 1)` — change the numbers to rebalance |

### Text Input Area (Lines 335–339)

| What to change | Where |
|---|---|
| **Text area height** | Line 337: `setFixedHeight(68)` |
| **Placeholder text** | Line 338: `"> TYPE WITH GAZE + BLINK"` |

### Key Sizes (Lines 395–401)

```python
width_map = {
    "sm": 58,       # Small keys (Win, Alt, arrows)
    "default": 64,  # Regular letter keys
    "lg": 96,       # Medium keys (Tab, Ctrl, CLR)
    "xl": 120,      # Large keys (Caps, Shift, Enter, Backspace)
    "space": 300,   # Spacebar
}
```

**Key height**: Line 520 inside `_build_key_button()` → `btn.setFixedHeight(42)`

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
