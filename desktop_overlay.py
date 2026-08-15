"""
Desktop Overlay UI Module for Intentix / SenseWay

This module contains the PyQt5 floating overlay interface:
- CursorOverlay (gaze ring indicator)
- BlinkDebugOverlay (HUD debug info)
- OverlayPanelBase & DraggableOverlayWidget (frameless window bases)
- KeyboardOverlayPanel (on-screen QWERTY keyboard)
- CameraOverlayPanel (live tracking video feed)
- EmergencyOverlayPanel (SOS message/calls)
- ControlOverlayPanel (cursor speed & sensitivity settings)
- FloatingControlBar (main pill-shaped control bar)
- GazeClickBridge & dispatch_blink_click (Qt click routing)
"""

import sys
import time
import math
import numpy as np
import cv2
import pyautogui
import ctypes
from ctypes import wintypes

try:
    from PyQt5 import QtWidgets, QtGui, QtCore, QtTest
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False

# Global state references provided by main.py or initialized with defaults
SCREEN_W, SCREEN_H = pyautogui.size()
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

TOP_MARGIN = 5
BAR_IDLE_HEIGHT = 58
BAR_EXPANDED_HEIGHT = 100
BAR_HEIGHT = BAR_EXPANDED_HEIGHT
PANEL_TOP_OFFSET = TOP_MARGIN + BAR_EXPANDED_HEIGHT + 14
LOCK_DELAY = 2
DEBUG_BLINK_HUD = False

# Callbacks and data providers set by main.py
_settings = {
    "emergency_contact": "+919354139640",
    "cursor_scope": 1.0,
    "blink_sensitivity": 0.2,
    "cursor_speed": 0.15,
    "overlay_enabled": True,
    "mouse_control_enabled": True
}
_action_executor = None
_whatsapp_sender = None
_frame_provider = None
_voice_executor = None

gaze_click_bridge = None
cursor_overlay_widget = None
blink_debug_overlay_widget = None
keyboard_panel_widget = None
mini_camera_widget = None


def configure_overlay(settings=None, action_executor=None, whatsapp_sender=None, 
                      frame_provider=None, voice_executor=None, lock_delay=1.5, debug_hud=False):
    """Configure external callbacks and settings references from main application."""
    global _settings, _action_executor, _whatsapp_sender, _frame_provider, _voice_executor, LOCK_DELAY, DEBUG_BLINK_HUD
    if settings is not None:
        _settings = settings
    if action_executor is not None:
        _action_executor = action_executor
    if whatsapp_sender is not None:
        _whatsapp_sender = whatsapp_sender
    if frame_provider is not None:
        _frame_provider = frame_provider
    if voice_executor is not None:
        _voice_executor = voice_executor
    LOCK_DELAY = lock_delay
    DEBUG_BLINK_HUD = debug_hud


def _perform_action(action, text=""):
    if _action_executor:
        return _action_executor(action, text)
    return {"status": "no_executor"}


def _send_whatsapp(number, message):
    if _whatsapp_sender:
        import threading
        threading.Thread(target=_whatsapp_sender, args=(number, message), daemon=True).start()


if PYQT5_AVAILABLE:
    class CursorOverlay(QtWidgets.QWidget):
        """Visual circle ring overlay that follows mouse cursor without taking focus or mouse events."""

        def __init__(self, radius=80):
            super().__init__()
            self.radius = radius
            self.diameter = 2 * self.radius + 4
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint |
                QtCore.Qt.WindowStaysOnTopHint |
                QtCore.Qt.Tool |
                QtCore.Qt.X11BypassWindowManagerHint |
                QtCore.Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            if hasattr(QtCore.Qt, "WindowTransparentForInput"):
                self.setWindowFlag(QtCore.Qt.WindowTransparentForInput, True)
            self.setFixedSize(self.diameter, self.diameter)

            self.label = QtWidgets.QLabel(self)
            self.label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.label.setGeometry(0, 0, self.diameter, self.diameter)

            self.timer = QtCore.QTimer()
            self.timer.timeout.connect(self.update_position)
            self.timer.start(10)

        def update_position(self):
            if _settings.get("overlay_enabled", True):
                x, y = pyautogui.position()
                self.move(x - self.radius, y - self.radius)
                self.draw_circle()
                self.show()
                self.raise_()
            else:
                self.hide()

        def draw_circle(self):
            img = np.zeros((self.diameter, self.diameter, 4), dtype=np.uint8)
            cv2.circle(img, (self.radius + 2, self.radius + 2), self.radius - 5, (0, 255, 0, 255), 10)
            qimg = QtGui.QImage(img.data, self.diameter, self.diameter, QtGui.QImage.Format_RGBA8888)
            pixmap = QtGui.QPixmap.fromImage(qimg)
            self.label.setPixmap(pixmap)


    class BlinkDebugOverlay(QtWidgets.QWidget):
        """Small always-on-top HUD to inspect blink click routing in real time."""

        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint |
                QtCore.Qt.WindowStaysOnTopHint |
                QtCore.Qt.Tool |
                QtCore.Qt.X11BypassWindowManagerHint |
                QtCore.Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            if hasattr(QtCore.Qt, "WindowTransparentForInput"):
                self.setWindowFlag(QtCore.Qt.WindowTransparentForInput, True)

            width = min(760, max(420, SCREEN_W - 40))
            height = 176
            self.setFixedSize(width, height)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)

            shell = QtWidgets.QFrame(self)
            shell.setStyleSheet("""
                QFrame {
                    background: rgba(10, 16, 26, 225);
                    border: 2px solid #ff8b57;
                    border-radius: 12px;
                }
            """)
            root.addWidget(shell)

            shell_layout = QtWidgets.QVBoxLayout(shell)
            shell_layout.setContentsMargins(12, 10, 12, 10)
            shell_layout.setSpacing(4)

            title = QtWidgets.QLabel("BLINK DEBUG HUD", shell)
            title.setStyleSheet("color:#ffb38f; font-size:16px; font-weight:700; letter-spacing:1px;")
            shell_layout.addWidget(title)

            self.label = QtWidgets.QLabel("Waiting for blink events...", shell)
            self.label.setWordWrap(True)
            self.label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            self.label.setStyleSheet(
                "color:#e9f5ff; font-size:13px; font-family:'Consolas'; line-height:1.3em;"
            )
            shell_layout.addWidget(self.label, 1)

            self.move(20, TOP_MARGIN + BAR_HEIGHT + 20)

        def set_message(self, text):
            self.label.setText(text)


    class DraggableOverlayWidget(QtWidgets.QWidget):
        """Frameless always-on-top overlay base widget pinned to fixed coordinates."""

        def __init__(self, width, height):
            super().__init__()
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint |
                QtCore.Qt.WindowStaysOnTopHint |
                QtCore.Qt.Tool |
                QtCore.Qt.X11BypassWindowManagerHint |
                QtCore.Qt.WindowDoesNotAcceptFocus
            )
            self.setWindowState(QtCore.Qt.WindowNoState)
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            self.setFixedSize(width, height)
            self._fixed_position = None

            self.pin_timer = QtCore.QTimer(self)
            self.pin_timer.timeout.connect(self._enforce_pin_and_topmost)
            self.pin_timer.start(450)

        def set_fixed_position(self, x, y):
            self._fixed_position = QtCore.QPoint(int(x), int(y))
            self.move(self._fixed_position)

        def _enforce_pin_and_topmost(self):
            if self._fixed_position is not None and self.pos() != self._fixed_position:
                self.move(self._fixed_position)
            if self.isVisible():
                self.raise_()


    class OverlayPanelBase(DraggableOverlayWidget):
        """Floating module window with title and close button."""

        def __init__(self, title, width=None, height=None):
            panel_w = width or min(1800, int(SCREEN_W * 0.94))
            panel_h = height or min(1060, int(SCREEN_H * 0.9))
            super().__init__(panel_w, panel_h)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)

            self.shell = QtWidgets.QFrame(self)
            self.shell.setObjectName("panelShell")
            root.addWidget(self.shell)

            shell_layout = QtWidgets.QVBoxLayout(self.shell)
            shell_layout.setContentsMargins(0, 0, 0, 0)
            shell_layout.setSpacing(0)

            self.header = QtWidgets.QFrame(self.shell)
            self.header.setObjectName("panelHeader")
            header_layout = QtWidgets.QHBoxLayout(self.header)
            header_layout.setContentsMargins(16, 10, 10, 10)

            self.title_label = QtWidgets.QLabel(title, self.header)
            self.title_label.setObjectName("panelTitle")
            header_layout.addWidget(self.title_label)
            header_layout.addStretch(1)

            close_btn = QtWidgets.QPushButton("✕", self.header)
            close_btn.setObjectName("closeBtn")
            close_btn.setFixedSize(48, 48)
            close_btn.clicked.connect(self.hide)
            header_layout.addWidget(close_btn)

            self.body = QtWidgets.QFrame(self.shell)
            self.body.setObjectName("panelBody")
            self.body_layout = QtWidgets.QVBoxLayout(self.body)
            self.body_layout.setContentsMargins(10, 8, 10, 10)
            self.body_layout.setSpacing(6)

            shell_layout.addWidget(self.header)
            shell_layout.addWidget(self.body, 1)

            self.setStyleSheet("""
                QFrame#panelShell {
                    background-color: rgba(10, 16, 26, 236);
                    border: 6px solid #2f4b69;
                    border-radius: 18px;
                }
                QFrame#panelHeader {
                    background-color: rgba(8, 13, 21, 245);
                    border-bottom: 2px solid #2f4b69;
                    border-top-left-radius: 16px;
                    border-top-right-radius: 16px;
                }
                QLabel#panelTitle {
                    color: #2dd4ff;
                    font-size: 24px;
                    font-weight: 700;
                    letter-spacing: 1px;
                }
                QPushButton#closeBtn {
                    background: rgba(33, 49, 69, 230);
                    color: #e7f3ff;
                    border: 2px solid #365270;
                    border-radius: 24px;
                    font-size: 20px;
                    font-weight: 700;
                }
                QPushButton#closeBtn:hover {
                    color: #2dd4ff;
                    border-color: #2dd4ff;
                }
            """)

            self.set_fixed_position((SCREEN_W - self.width()) // 2, PANEL_TOP_OFFSET)
            self.hide()


    class KeyboardOverlayPanel(OverlayPanelBase):
        """Virtual QWERTY keyboard overlay panel."""

        def __init__(self):
            super().__init__(
                "",
                width=min(1400, int(SCREEN_W * 0.85)),
                height=min(340, int(SCREEN_H * 0.44)),
            )

            # Hide the header entirely for a compact borderless look
            self.header.hide()

            self.caps_lock = False
            self.shift = False
            self.caps_btn = None
            self.shift_buttons = []
            self.dynamic_buttons = []

            wrapper = QtWidgets.QHBoxLayout()
            wrapper.setContentsMargins(0, 0, 0, 0)
            wrapper.setSpacing(0)
            self.body_layout.addLayout(wrapper, 1)

            left = QtWidgets.QVBoxLayout()
            left.setContentsMargins(0, 0, 0, 0)
            left.setSpacing(0)
            wrapper.addLayout(left, 5)

            right = QtWidgets.QVBoxLayout()
            right.setContentsMargins(0, 0, 0, 0)
            right.setSpacing(0)
            wrapper.addLayout(right, 1)

            self.text_area = QtWidgets.QTextEdit(self.body)
            self.text_area.setObjectName("textArea")
            self.text_area.setFixedHeight(54)
            self.text_area.setPlaceholderText("> TYPE WITH GAZE + BLINK")
            left.addWidget(self.text_area)
            self.text_area.hide()  # Disabled to type directly to the OS

            keyboard_rows = QtWidgets.QVBoxLayout()
            keyboard_rows.setContentsMargins(0, 0, 0, 0)
            keyboard_rows.setSpacing(0)
            left.addLayout(keyboard_rows, 1)

            self.setStyleSheet(self.styleSheet() + """
                QFrame#panelBody {
                    padding: 0px;
                }
                QTextEdit#textArea {
                    background: rgba(3, 10, 16, 235);
                    color: #dff6ff;
                    border: 2px solid #2f4b69;
                    border-radius: 0px;
                    font-family: 'Consolas';
                    font-size: 18px;
                    font-weight: bold;
                    padding: 4px 8px;
                }
                QTextEdit#textArea:focus {
                    border-color: #2dd4ff;
                }
            """)

            def build_row(specs):
                row_layout = QtWidgets.QHBoxLayout()
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(0)
                for spec in specs:
                    btn = self._build_key_button(
                        label=spec["label"],
                        key=spec["key"],
                        shift_symbol=spec.get("shift"),
                    )
                    btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                    row_layout.addWidget(btn, spec.get("stretch", 6))
                keyboard_rows.addLayout(row_layout, 1)

            build_row([
                {"label": "`", "key": "`", "shift": "~"},
                {"label": "1", "key": "1", "shift": "!"},
                {"label": "2", "key": "2", "shift": "@"},
                {"label": "3", "key": "3", "shift": "#"},
                {"label": "4", "key": "4", "shift": "$"},
                {"label": "5", "key": "5", "shift": "%"},
                {"label": "6", "key": "6", "shift": "^"},
                {"label": "7", "key": "7", "shift": "&"},
                {"label": "8", "key": "8", "shift": "*"},
                {"label": "9", "key": "9", "shift": "("},
                {"label": "0", "key": "0", "shift": ")"},
                {"label": "-", "key": "-", "shift": "_"},
                {"label": "=", "key": "=", "shift": "+"},
                {"label": "Backspace", "key": "Backspace", "stretch": 12},
            ])

            build_row([
                {"label": "Tab", "key": "Tab", "stretch": 12},
                {"label": "q", "key": "q"},
                {"label": "w", "key": "w"},
                {"label": "e", "key": "e"},
                {"label": "r", "key": "r"},
                {"label": "t", "key": "t"},
                {"label": "y", "key": "y"},
                {"label": "u", "key": "u"},
                {"label": "i", "key": "i"},
                {"label": "o", "key": "o"},
                {"label": "p", "key": "p"},
                {"label": "[", "key": "[", "shift": "{"},
                {"label": "]", "key": "]", "shift": "}"},
                {"label": "\\", "key": "\\", "shift": "|"},
            ])

            build_row([
                {"label": "Caps", "key": "Caps", "stretch": 12},
                {"label": "a", "key": "a"},
                {"label": "s", "key": "s"},
                {"label": "d", "key": "d"},
                {"label": "f", "key": "f"},
                {"label": "g", "key": "g"},
                {"label": "h", "key": "h"},
                {"label": "j", "key": "j"},
                {"label": "k", "key": "k"},
                {"label": "l", "key": "l"},
                {"label": ";", "key": ";", "shift": ":"},
                {"label": "'", "key": "'", "shift": "\""},
                {"label": "Enter", "key": "Enter", "stretch": 12},
            ])

            build_row([
                {"label": "Shift", "key": "Shift", "stretch": 15},
                {"label": "z", "key": "z"},
                {"label": "x", "key": "x"},
                {"label": "c", "key": "c"},
                {"label": "v", "key": "v"},
                {"label": "b", "key": "b"},
                {"label": "n", "key": "n"},
                {"label": "m", "key": "m"},
                {"label": ",", "key": ",", "shift": "<"},
                {"label": ".", "key": ".", "shift": ">"},
                {"label": "/", "key": "/", "shift": "?"},
                {"label": "Shift", "key": "Shift", "stretch": 15},
            ])

            build_row([
                {"label": "Ctrl", "key": "Ctrl", "stretch": 10},
                {"label": "Win", "key": "Win"},
                {"label": "Alt", "key": "Alt"},
                {"label": "Space", "key": "Space", "stretch": 40},
                {"label": "Alt", "key": "Alt"},
                {"label": "Menu", "key": "Menu", "stretch": 5},
                {"label": "←", "key": "Left", "stretch": 5},
                {"label": "→", "key": "Right", "stretch": 5},
                {"label": "CLR", "key": "Clear", "stretch": 7},
            ])

            google_btn = QtWidgets.QPushButton("SEARCH\nGOOGLE")
            google_btn.setObjectName("actionBtn")
            google_btn.clicked.connect(lambda: _perform_action("google", self.text_value()))
            right.addWidget(google_btn, 1)

            yt_btn = QtWidgets.QPushButton("SEARCH\nYOUTUBE")
            yt_btn.setObjectName("actionBtn")
            yt_btn.clicked.connect(lambda: _perform_action("youtube", self.text_value()))
            right.addWidget(yt_btn, 1)

            ext_btn = QtWidgets.QPushButton("TYPE\nEXTERNAL")
            ext_btn.setObjectName("actionBtn")
            ext_btn.clicked.connect(lambda: _perform_action("type_external", self.text_value()))
            right.addWidget(ext_btn, 1)

            close_key_btn = QtWidgets.QPushButton("✕\nCLOSE")
            close_key_btn.setObjectName("closeKeyBtn")
            close_key_btn.clicked.connect(self.hide)
            right.addWidget(close_key_btn, 1)

            action_css = """
                QPushButton {
                    background-color: #121e30;
                    color: #2dd4ff;
                    border: 1px solid #365472;
                    border-radius: 0px;
                    font-size: 15px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    border-color: #2dd4ff;
                    color: #ffffff;
                    background-color: rgba(45, 212, 255, 60);
                }
            """
            close_css = """
                QPushButton {
                    background-color: rgba(120, 24, 24, 245);
                    color: #ffd4d4;
                    border: 1px solid #a83232;
                    border-radius: 0px;
                    font-size: 15px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(180, 40, 40, 255);
                    color: #ffffff;
                    border-color: #ff6666;
                }
            """
            for btn in (google_btn, yt_btn, ext_btn):
                btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                btn.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Bold))
                btn.setStyleSheet(action_css)

            close_key_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            close_key_btn.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Bold))
            close_key_btn.setStyleSheet(close_css)

            self._refresh_key_labels()
            self._refresh_modifier_visuals()

            # Pin keyboard panel to bottom center of screen
            self.set_fixed_position((SCREEN_W - self.width()) // 2, SCREEN_H - self.height() - 25)

        def _build_key_button(self, label, key, shift_symbol=None):
            btn = QtWidgets.QPushButton(label)
            btn.setObjectName("keyBtn")
            btn.setFont(QtGui.QFont("Arial", 26, QtGui.QFont.Bold))
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #121e30;
                    color: #ffffff;
                    border: 1px solid #365472;
                    border-radius: 0px;
                    font-size: 26px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    border-color: #2dd4ff;
                    color: #2dd4ff;
                    background-color: rgba(45, 212, 255, 50);
                }
                QPushButton[active="true"] {
                    border-color: #2dd4ff;
                    color: #ffffff;
                    background-color: rgba(45, 212, 255, 80);
                }
            """)
            btn.setFocusPolicy(QtCore.Qt.NoFocus)
            btn.clicked.connect(lambda checked, value=key, alt=shift_symbol: self._on_key_press(value, alt))

            if key == "Caps":
                self.caps_btn = btn
            if key == "Shift":
                self.shift_buttons.append(btn)

            if (len(key) == 1 and key.isprintable()) or shift_symbol is not None:
                self.dynamic_buttons.append((btn, key, shift_symbol))

            return btn

        def _display_for_key(self, key, shift_symbol=None):
            if len(key) == 1 and key.isalpha():
                return key.upper() if (self.caps_lock or self.shift) else key.lower()
            if self.shift and shift_symbol:
                return shift_symbol
            return key

        def _resolve_char(self, key, shift_symbol=None):
            if len(key) == 1 and key.isalpha():
                return key.upper() if (self.caps_lock ^ self.shift) else key.lower()
            if self.shift and shift_symbol:
                return shift_symbol
            return key

        def _refresh_key_labels(self):
            for btn, key, shift_symbol in self.dynamic_buttons:
                btn.setText(self._display_for_key(key, shift_symbol))

        def _refresh_modifier_visuals(self):
            if self.caps_btn is not None:
                self.caps_btn.setProperty("active", self.caps_lock)
                self.caps_btn.style().unpolish(self.caps_btn)
                self.caps_btn.style().polish(self.caps_btn)
            for btn in self.shift_buttons:
                btn.setProperty("active", self.shift)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

        def _on_key_press(self, key, shift_symbol=None):
            if key == "Backspace":
                self.backspace()
                return
            if key == "Tab":
                self.insert_text("\t")
                return
            if key == "Enter":
                self.insert_text("\n")
                return
            if key == "Space":
                self.insert_text(" ")
                if self.shift:
                    self.shift = False
                    self._refresh_modifier_visuals()
                    self._refresh_key_labels()
                return
            if key == "Caps":
                self.caps_lock = not self.caps_lock
                self._refresh_modifier_visuals()
                self._refresh_key_labels()
                return
            if key == "Shift":
                self.shift = not self.shift
                self._refresh_modifier_visuals()
                self._refresh_key_labels()
                return
            if key == "Clear":
                self.clear_text()
                return
            if key == "Left":
                self.move_cursor(-1)
                return
            if key == "Right":
                self.move_cursor(1)
                return
            if key in {"Ctrl", "Alt", "Win", "Menu"}:
                return

            self.insert_text(self._resolve_char(key, shift_symbol))
            if self.shift:
                self.shift = False
                self._refresh_modifier_visuals()
                self._refresh_key_labels()

        def text_value(self):
            return self.text_area.toPlainText()

        def insert_text(self, value):
            try:
                pyautogui.write(value)
            except Exception:
                pass

        def backspace(self):
            try:
                pyautogui.press('backspace')
            except Exception:
                pass

        def clear_text(self):
            try:
                pyautogui.hotkey('ctrl', 'a')
                pyautogui.press('backspace')
            except Exception:
                pass

        def move_cursor(self, delta):
            try:
                if delta < 0:
                    pyautogui.press('left', presses=abs(delta))
                else:
                    pyautogui.press('right', presses=delta)
            except Exception:
                pass


    class CameraOverlayPanel(OverlayPanelBase):
        """Camera feed preview overlay panel."""

        def __init__(self):
            super().__init__("CAMERA MODULE", width=min(1300, int(SCREEN_W * 0.82)), height=min(860, int(SCREEN_H * 0.82)))

            self.video_label = QtWidgets.QLabel("Camera feed initializing...", self.body)
            self.video_label.setAlignment(QtCore.Qt.AlignCenter)
            self.video_label.setStyleSheet("""
                background: rgba(5, 9, 14, 235);
                border: 2px solid #2f4b69;
                border-radius: 12px;
                color: #9fb6cf;
                font-size: 20px;
            """)
            self.body_layout.addWidget(self.video_label, 1)

            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.refresh_frame)
            self.timer.start(40)

        def refresh_frame(self):
            frame = None
            if _frame_provider:
                frame = _frame_provider()
            if frame is None:
                return

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, c = rgb.shape
            qimg = QtGui.QImage(rgb.data, w, h, c * w, QtGui.QImage.Format_RGB888)
            pixmap = QtGui.QPixmap.fromImage(qimg).scaled(
                self.video_label.width(),
                self.video_label.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )
            self.video_label.setPixmap(pixmap)


    class MiniCameraOverlay(DraggableOverlayWidget):
        """Always-visible small camera feed in the bottom right corner."""
        def __init__(self, width=240, height=180):
            super().__init__(width, height)
            
            # Allow click-through and no focus
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowTransparentForInput | QtCore.Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            
            # Position at bottom right
            margin = 20
            self.set_fixed_position(SCREEN_W - width - margin, SCREEN_H - height - margin)
            
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            
            self.video_label = QtWidgets.QLabel(self)
            self.video_label.setAlignment(QtCore.Qt.AlignCenter)
            self.video_label.setStyleSheet("""
                background: rgba(5, 9, 14, 235);
                border: 2px solid #ff8b57;
                border-radius: 12px;
            """)
            layout.addWidget(self.video_label)
            
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.refresh_frame)
            self.timer.start(40)
            
        def refresh_frame(self):
            frame = None
            if _frame_provider:
                frame = _frame_provider()
            if frame is None:
                return

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, c = rgb.shape
            qimg = QtGui.QImage(rgb.data, w, h, c * w, QtGui.QImage.Format_RGB888)
            pixmap = QtGui.QPixmap.fromImage(qimg).scaled(
                self.video_label.width(),
                self.video_label.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )
            self.video_label.setPixmap(pixmap)


    class EmergencyOverlayPanel(OverlayPanelBase):
        """Emergency contact and SOS actions module panel."""

        def __init__(self):
            super().__init__("EMERGENCY MODULE", width=min(1100, int(SCREEN_W * 0.72)), height=min(760, int(SCREEN_H * 0.72)))

            contact_layout = QtWidgets.QHBoxLayout()
            self.contact_input = QtWidgets.QLineEdit(self.body)
            self.contact_input.setPlaceholderText("Enter emergency contact (+countrycode)")
            self.contact_input.setText(_settings.get("emergency_contact", ""))
            self.contact_input.setStyleSheet("""
                min-height: 58px;
                border: 2px solid #355173;
                border-radius: 10px;
                background: rgba(8, 13, 20, 240);
                color: #eef8ff;
                font-size: 20px;
                padding: 0 12px;
            """)
            contact_layout.addWidget(self.contact_input, 1)

            save_btn = QtWidgets.QPushButton("SET CONTACT")
            save_btn.setFixedHeight(58)
            save_btn.clicked.connect(self.save_contact)
            save_btn.setStyleSheet("""
                border: 2px solid #355173;
                border-radius: 10px;
                background: rgba(29, 43, 63, 235);
                color: #eef8ff;
                font-size: 18px;
                font-weight: 700;
                padding: 0 18px;
            """)
            contact_layout.addWidget(save_btn)
            self.body_layout.addLayout(contact_layout)

            grid = QtWidgets.QGridLayout()
            grid.setSpacing(12)
            self.body_layout.addLayout(grid, 1)

            def emergency_btn(label, callback, row, col):
                btn = QtWidgets.QPushButton(label)
                btn.setMinimumHeight(102)
                btn.clicked.connect(callback)
                btn.setStyleSheet("""
                    border: 2px solid #7c3f3f;
                    border-radius: 12px;
                    background: rgba(108, 32, 32, 230);
                    color: #ffdcdc;
                    font-size: 22px;
                    font-weight: 700;
                """)
                grid.addWidget(btn, row, col)

            emergency_btn("MSG CONTACT", self.msg_contact, 0, 0)
            emergency_btn("CALL CONTACT", self.call_contact, 0, 1)
            emergency_btn("DIAL 100", lambda: _perform_action("emergency_police"), 1, 0)
            emergency_btn("DIAL 112", lambda: _perform_action("emergency_ambulance"), 1, 1)

            self.status = QtWidgets.QLabel("Emergency actions ready.", self.body)
            self.status.setStyleSheet("color:#9fb6cf; font-size:18px;")
            self.body_layout.addWidget(self.status)

        def save_contact(self):
            value = self.contact_input.text().strip()
            if not value:
                self.status.setText("Enter a valid contact number.")
                return
            _settings["emergency_contact"] = value
            self.status.setText(f"Emergency contact saved: {value}")

        def msg_contact(self):
            self.save_contact()
            if not _settings.get("emergency_contact"):
                self.status.setText("Set contact before sending message.")
                return
            _send_whatsapp(_settings["emergency_contact"], "SOS! I need help. Sent via Intentix.")
            self.status.setText("Emergency message flow started.")

        def call_contact(self):
            self.save_contact()
            if not _settings.get("emergency_contact"):
                self.status.setText("Set contact before calling.")
                return
            _perform_action("emergency_call")
            self.status.setText("Emergency call triggered.")


    class ControlOverlayPanel(OverlayPanelBase):
        """Settings control module panel."""

        def __init__(self):
            super().__init__("CONTROL MODULE", width=min(1100, int(SCREEN_W * 0.72)), height=min(760, int(SCREEN_H * 0.72)))

            self.speed_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self.body)
            self.speed_slider.setRange(3, 15)
            self.speed_slider.setValue(int(_settings.get("cursor_speed", 0.5) * 10))

            self.blink_spin = QtWidgets.QDoubleSpinBox(self.body)
            self.blink_spin.setRange(0.15, 0.35)
            self.blink_spin.setSingleStep(0.01)
            self.blink_spin.setValue(float(_settings.get("blink_sensitivity", 0.2)))

            self.lock_delay_spin = QtWidgets.QDoubleSpinBox(self.body)
            self.lock_delay_spin.setRange(0.5, 5.0)
            self.lock_delay_spin.setSingleStep(0.1)
            self.lock_delay_spin.setValue(LOCK_DELAY)

            self.status = QtWidgets.QLabel("Control settings ready.", self.body)
            self.status.setStyleSheet("color:#9fb6cf; font-size:18px;")

            for label_text, widget in [
                ("Cursor Speed", self.speed_slider),
                ("Blink Sensitivity", self.blink_spin),
                ("Lock Delay (seconds)", self.lock_delay_spin),
            ]:
                label = QtWidgets.QLabel(label_text, self.body)
                label.setStyleSheet("color:#d8e8f9; font-size:20px; font-weight:600;")
                self.body_layout.addWidget(label)
                self.body_layout.addWidget(widget)

            btn_row = QtWidgets.QHBoxLayout()
            self.body_layout.addLayout(btn_row)

            save_btn = QtWidgets.QPushButton("SAVE SETTINGS")
            save_btn.clicked.connect(self.save_settings)
            btn_row.addWidget(save_btn)

            overlay_btn = QtWidgets.QPushButton("TOGGLE CURSOR OVERLAY")
            overlay_btn.clicked.connect(lambda: self.toggle_setting("overlay_enabled"))
            btn_row.addWidget(overlay_btn)

            mouse_btn = QtWidgets.QPushButton("TOGGLE MOUSE CONTROL")
            mouse_btn.clicked.connect(lambda: self.toggle_setting("mouse_control_enabled"))
            btn_row.addWidget(mouse_btn)

            for btn in (save_btn, overlay_btn, mouse_btn):
                btn.setMinimumHeight(62)
                btn.setStyleSheet("""
                    border: 2px solid #355173;
                    border-radius: 12px;
                    background: rgba(29, 43, 63, 235);
                    color: #eef8ff;
                    font-size: 18px;
                    font-weight: 700;
                    padding: 0 12px;
                """)

            self.body_layout.addWidget(self.status)

        def save_settings(self):
            global LOCK_DELAY
            _settings["cursor_speed"] = float(self.speed_slider.value()) / 10.0
            _settings["blink_sensitivity"] = float(self.blink_spin.value())
            LOCK_DELAY = float(self.lock_delay_spin.value())
            self.status.setText("Control settings saved.")

        def toggle_setting(self, key):
            _settings[key] = not _settings.get(key, True)
            state = "ON" if _settings[key] else "OFF"
            self.status.setText(f"{key} is now {state}.")


    class FloatingControlBar(DraggableOverlayWidget):
        """Floating control bar with idle/expanded hover states, pinned at top center.

        Idle: small rectangle showing 'OVERLAY' text.
        Expanded (on hover): full bar with gaze-optimized icon buttons.
        Auto-collapses when the gaze leaves (unless a panel is open).
        """

        IDLE_W = 280
        IDLE_H = BAR_IDLE_HEIGHT
        EXPANDED_W = 780
        EXPANDED_H = BAR_EXPANDED_HEIGHT
        COLLAPSE_DELAY_MS = 1500

        def __init__(self, panels):
            super().__init__(width=self.IDLE_W, height=self.IDLE_H)
            self.panels = panels
            self.voice_enabled = False
            self._is_expanded = False

            # Timer for auto-collapse after cursor leaves
            self._collapse_timer = QtCore.QTimer(self)
            self._collapse_timer.setSingleShot(True)
            self._collapse_timer.timeout.connect(self._do_collapse)

            # Root layout
            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)

            # Shell frame (the visible bar background)
            self.shell = QtWidgets.QFrame(self)
            self.shell.setObjectName("barShell")
            root.addWidget(self.shell)

            # Stacked layout to switch between idle and expanded views
            self.stack = QtWidgets.QStackedLayout(self.shell)
            self.stack.setContentsMargins(0, 0, 0, 0)

            # ─── IDLE PAGE ────────────────────────────────────────────────────
            idle_page = QtWidgets.QWidget()
            idle_layout = QtWidgets.QHBoxLayout(idle_page)
            idle_layout.setContentsMargins(16, 0, 16, 0)
            idle_layout.setAlignment(QtCore.Qt.AlignCenter)

            dot = QtWidgets.QLabel("●")
            dot.setObjectName("idleDot")
            idle_layout.addWidget(dot)

            idle_label = QtWidgets.QLabel("OVERLAY")
            idle_label.setObjectName("idleLabel")
            idle_layout.addWidget(idle_label)

            self.stack.addWidget(idle_page)  # index 0

            # ─── EXPANDED PAGE ────────────────────────────────────────────────
            expanded_page = QtWidgets.QWidget()
            exp_layout = QtWidgets.QHBoxLayout(expanded_page)
            exp_layout.setContentsMargins(18, 8, 18, 8)
            exp_layout.setSpacing(10)

            # Brand label
            brand = QtWidgets.QLabel("●  INTENTIX")
            brand.setObjectName("brandLabel")
            exp_layout.addWidget(brand)

            # Vertical separator
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.VLine)
            sep.setFixedWidth(2)
            sep.setStyleSheet("color: rgba(45, 80, 120, 180);")
            exp_layout.addWidget(sep)

            # Action buttons
            buttons_spec = [
                ("🎤", "Voice",  self.toggle_voice,                        False),
                ("⌨",  "Keys",   lambda: self.toggle_panel("keyboard"),    False),
                ("📷", "Cam",    lambda: self.toggle_panel("camera"),      False),
                ("🚨", "SOS",    lambda: self.toggle_panel("emergency"),   True),
                ("⚙",  "Config", lambda: self.toggle_panel("control"),     False),
            ]

            self._action_buttons = []
            for icon, label, callback, is_sos in buttons_spec:
                btn = QtWidgets.QPushButton(f"{icon}  {label}")
                btn.setObjectName("sosBarBtn" if is_sos else "barBtn")
                btn.setFixedSize(110, 64)
                btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
                btn.clicked.connect(callback)
                exp_layout.addWidget(btn)
                self._action_buttons.append(btn)

            exp_layout.addStretch(1)

            # Status badge
            self.status = QtWidgets.QLabel("Ready")
            self.status.setObjectName("barStatus")
            exp_layout.addWidget(self.status)

            self.stack.addWidget(expanded_page)  # index 1

            # Start on idle
            self.stack.setCurrentIndex(0)

            # ─── STYLESHEET ──────────────────────────────────────────────────
            self.setStyleSheet("""
                QFrame#barShell {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 rgba(8, 14, 24, 240),
                        stop:1 rgba(14, 22, 36, 240)
                    );
                    border: 1.5px solid rgba(45, 212, 255, 60);
                    border-radius: 26px;
                }
                QLabel#idleDot {
                    color: #3dda9b;
                    font-size: 16px;
                    font-weight: 900;
                    padding-right: 4px;
                }
                QLabel#idleLabel {
                    color: #ffffff;
                    font-size: 20px;
                    font-weight: 900;
                    letter-spacing: 5px;
                }
                QLabel#brandLabel {
                    color: #3dda9b;
                    font-size: 17px;
                    font-weight: 900;
                    letter-spacing: 2px;
                    padding-right: 4px;
                }
                QPushButton#barBtn {
                    background: rgba(18, 30, 48, 245);
                    border: 2px solid #2d4a6a;
                    border-radius: 16px;
                    color: #e0f0ff;
                    font-size: 16px;
                    font-weight: 800;
                }
                QPushButton#barBtn:hover {
                    border-color: #2dd4ff;
                    color: #2dd4ff;
                    background: rgba(45, 212, 255, 25);
                }
                QPushButton#sosBarBtn {
                    background: rgba(80, 20, 20, 245);
                    border: 2px solid #8b3a3a;
                    border-radius: 16px;
                    color: #ffd4d4;
                    font-size: 16px;
                    font-weight: 800;
                }
                QPushButton#sosBarBtn:hover {
                    border-color: #ff6161;
                    color: #ff6161;
                    background: rgba(255, 97, 97, 35);
                }
                QLabel#barStatus {
                    color: #7a99b8;
                    font-size: 16px;
                    font-weight: 700;
                    padding-right: 10px;
                }
            """)

            # Position idle bar at top center
            self.set_fixed_position((SCREEN_W - self.IDLE_W) // 2, TOP_MARGIN)

        # ─── HOVER EXPAND / COLLAPSE ─────────────────────────────────────

        def enterEvent(self, event):
            self._collapse_timer.stop()
            self._do_expand()
            super().enterEvent(event)

        def leaveEvent(self, event):
            # Keep expanded if any panel is currently open
            if any(p.isVisible() for p in self.panels.values()):
                super().leaveEvent(event)
                return
            self._collapse_timer.start(self.COLLAPSE_DELAY_MS)
            super().leaveEvent(event)

        def _do_expand(self):
            if self._is_expanded:
                return
            self._is_expanded = True
            self.setFixedSize(self.EXPANDED_W, self.EXPANDED_H)
            self.set_fixed_position((SCREEN_W - self.EXPANDED_W) // 2, TOP_MARGIN)
            self.stack.setCurrentIndex(1)

        def _do_collapse(self):
            if not self._is_expanded:
                return
            # Don't collapse while a panel is visible
            if any(p.isVisible() for p in self.panels.values()):
                return
            self._is_expanded = False
            self.stack.setCurrentIndex(0)
            self.setFixedSize(self.IDLE_W, self.IDLE_H)
            self.set_fixed_position((SCREEN_W - self.IDLE_W) // 2, TOP_MARGIN)

        # ─── PANEL TOGGLE ────────────────────────────────────────────────

        def toggle_panel(self, panel_name):
            panel = self.panels.get(panel_name)
            if panel is None:
                return

            was_visible = panel.isVisible()
            for p in self.panels.values():
                p.hide()

            if not was_visible:
                panel.show()
                panel.raise_()
                panel.activateWindow()
                # Keep bar expanded while panel is open
                self._collapse_timer.stop()
            else:
                # Panel closed — schedule collapse
                self._collapse_timer.start(self.COLLAPSE_DELAY_MS)

        # ─── VOICE INPUT ─────────────────────────────────────────────────

        def toggle_voice(self):
            self.voice_enabled = not self.voice_enabled
            if not self.voice_enabled:
                self.status.setText("Voice idle")
                return

            if _voice_executor is None:
                text, ok = QtWidgets.QInputDialog.getText(
                    self, "Voice Command", "Enter voice command text:", QtWidgets.QLineEdit.Normal, ""
                )
                if not ok or not text.strip():
                    self.voice_enabled = False
                    self.status.setText("Canceled")
                    return
                self.status.setText(f"Entered: {text}")
                self.voice_enabled = False
                return

            text, ok = QtWidgets.QInputDialog.getText(
                self, "Voice Command", "Enter voice command text:", QtWidgets.QLineEdit.Normal, ""
            )
            if not ok or not text.strip():
                self.voice_enabled = False
                self.status.setText("Canceled")
                return

            result = _voice_executor.process_command(text.strip())
            if result.get("status") == "pending_confirmation":
                confirm = QtWidgets.QMessageBox.question(
                    self,
                    "Confirm Action",
                    result.get("description", "Confirm pending action?"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                if confirm == QtWidgets.QMessageBox.Yes:
                    result = _voice_executor.confirm_pending()
                else:
                    result = _voice_executor.cancel_pending()

            self.status.setText(result.get("message", result.get("status", "Done")))
            self.voice_enabled = False


    class GazeClickBridge(QtCore.QObject):
        """Dispatch blink clicks to the widget under gaze in the Qt main thread."""

        blink_click_requested = QtCore.pyqtSignal(int, int)

        def __init__(self):
            super().__init__()
            self.blink_sequence = 0
            self.blink_click_requested.connect(
                self._handle_blink_click,
                QtCore.Qt.QueuedConnection
            )

        @QtCore.pyqtSlot(int, int)
        def _handle_blink_click(self, x, y):
            self.blink_sequence += 1
            app = QtWidgets.QApplication.instance()
            if app is None:
                self._publish_debug([
                    f"Blink #{self.blink_sequence}",
                    "path: fallback-os (no QApplication)",
                ])
                self._fallback_os_click(x, y)
                return

            global_point, source = self._point_from_signal(x, y)
            raw_hit, hit_widget, cursor_overlay_bypassed = self._widget_at_gaze(app, global_point)
            target = self._resolve_click_target(hit_widget)

            mode = "unresolved"
            try:
                if target is None:
                    mode = "fallback-os (no target)"
                    self._fallback_os_click(global_point.x(), global_point.y())
                elif isinstance(target, QtWidgets.QAbstractButton):
                    target.click()
                    mode = "qt-button-click"
                else:
                    local_point = target.mapFromGlobal(global_point)
                    QtTest.QTest.mouseClick(target, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, local_point)
                    mode = "qt-mouse-click"
            except Exception as exc:
                mode = f"fallback-os ({type(exc).__name__})"
                self._fallback_os_click(global_point.x(), global_point.y())

            self._publish_debug([
                f"Blink #{self.blink_sequence}",
                f"cursor: {global_point.x()},{global_point.y()} ({source})",
                f"raw-hit: {self._widget_label(raw_hit)}",
                f"hit-after-bypass: {self._widget_label(hit_widget)}",
                f"target: {self._widget_label(target)}",
                f"cursor-overlay-bypassed: {'yes' if cursor_overlay_bypassed else 'no'}",
                f"path: {mode}",
            ])

        def _point_from_signal(self, x, y):
            if int(x) < 0 or int(y) < 0:
                point = QtGui.QCursor.pos()
                return QtCore.QPoint(int(point.x()), int(point.y())), "QtGui.QCursor"
            return QtCore.QPoint(int(x), int(y)), "signal-coordinates"

        def _belongs_to_overlay(self, widget):
            if widget is None:
                return False
            current = widget
            while current is not None:
                if isinstance(current, (CursorOverlay, BlinkDebugOverlay)):
                    return True
                current = current.parentWidget()
            return False

        def _widget_at_gaze(self, app, global_point):
            raw_hit = app.widgetAt(global_point)
            if not self._belongs_to_overlay(raw_hit):
                return raw_hit, raw_hit, False

            overlay = cursor_overlay_widget
            debug_hud = blink_debug_overlay_widget
            hidden = []
            timer_stopped = False

            if overlay is not None:
                if hasattr(overlay, 'timer') and overlay.timer.isActive():
                    overlay.timer.stop()
                    timer_stopped = True
                if overlay.isVisible():
                    overlay.hide()
                    hidden.append(overlay)

            if debug_hud is not None and debug_hud.isVisible():
                debug_hud.hide()
                hidden.append(debug_hud)

            if hidden:
                app.processEvents(QtCore.QEventLoop.AllEvents, 15)

            try:
                return raw_hit, app.widgetAt(global_point), True
            finally:
                for w in hidden:
                    w.show()
                    w.raise_()
                if timer_stopped and overlay is not None:
                    overlay.timer.start(10)

        def _resolve_click_target(self, widget):
            if widget is None:
                return None

            current = widget
            fallback = None
            while current is not None:
                if isinstance(current, (CursorOverlay, BlinkDebugOverlay)):
                    return None

                if not current.isVisible() or not current.isEnabled():
                    current = current.parentWidget()
                    continue

                if isinstance(current, QtWidgets.QAbstractButton):
                    return current
                if isinstance(
                    current,
                    (
                        QtWidgets.QAbstractSlider,
                        QtWidgets.QAbstractSpinBox,
                        QtWidgets.QLineEdit,
                        QtWidgets.QTextEdit,
                        QtWidgets.QPlainTextEdit,
                        QtWidgets.QComboBox,
                    ),
                ):
                    return current

                if fallback is None and current.focusPolicy() != QtCore.Qt.NoFocus:
                    fallback = current
                current = current.parentWidget()
            return fallback or widget

        def _fallback_os_click(self, x, y):
            overlay = cursor_overlay_widget
            debug_hud = blink_debug_overlay_widget

            hidden = []
            timer_stopped = False

            if overlay is not None:
                if hasattr(overlay, 'timer') and overlay.timer.isActive():
                    overlay.timer.stop()
                    timer_stopped = True
                if overlay.isVisible():
                    overlay.hide()
                    hidden.append(overlay)

            if debug_hud is not None and debug_hud.isVisible():
                debug_hud.hide()
                hidden.append(debug_hud)

            qt_app = QtWidgets.QApplication.instance()
            if hidden and qt_app:
                qt_app.processEvents(QtCore.QEventLoop.AllEvents, 15)
            time.sleep(0.05)

            is_text_box = False
            try:
                class CURSORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]
                info = CURSORINFO()
                info.cbSize = ctypes.sizeof(CURSORINFO)
                if ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)):
                    h_ibeam = ctypes.windll.user32.LoadCursorW(None, 32513)
                    if info.hCursor == h_ibeam:
                        is_text_box = True
            except Exception:
                pass

            try:
                pyautogui.click(x=int(x), y=int(y))
            except Exception:
                pass
            finally:
                for w in hidden:
                    if _settings.get("overlay_enabled", True) or not isinstance(w, CursorOverlay):
                        w.show()
                        w.raise_()
                if timer_stopped and overlay is not None:
                    overlay.timer.start(10)
                
                # Auto-open keyboard if text box was clicked
                if is_text_box and keyboard_panel_widget is not None and not keyboard_panel_widget.isVisible():
                    QtCore.QTimer.singleShot(200, keyboard_panel_widget.show)

        def _widget_label(self, widget):
            if widget is None:
                return "None"
            class_name = widget.__class__.__name__
            object_name = ""
            try:
                object_name = widget.objectName() or "-"
            except Exception:
                object_name = "-"

            text_value = ""
            try:
                if hasattr(widget, "text"):
                    raw_text = widget.text()
                    text_value = str(raw_text).strip().replace("\n", " ")
            except Exception:
                text_value = ""

            if text_value and len(text_value) > 26:
                text_value = text_value[:26] + "..."

            if text_value:
                return f"{class_name}(name={object_name}, text={text_value})"
            return f"{class_name}(name={object_name})"

        def _publish_debug(self, lines):
            if not DEBUG_BLINK_HUD:
                return
            message = "\n".join(lines)
            hud = blink_debug_overlay_widget
            if hud is not None:
                hud.set_message(message)
            else:
                print("[BlinkDebug] " + message.replace("\n", " | "))


def dispatch_blink_click(x=None, y=None):
    """Route blink clicks to native overlay widgets when available."""
    if PYQT5_AVAILABLE and gaze_click_bridge is not None:
        if x is None or y is None:
            gaze_click_bridge.blink_click_requested.emit(-1, -1)
        else:
            gaze_click_bridge.blink_click_requested.emit(int(x), int(y))
        return

    if x is None or y is None:
        try:
            pos = pyautogui.position()
            x = int(pos.x)
            y = int(pos.y)
        except Exception:
            x, y = CENTER_X, CENTER_Y
    try:
        pyautogui.click(x=int(x), y=int(y))
    except Exception:
        pass


def launch_overlay():
    """Initialize and launch the PyQt5 overlay application."""
    global gaze_click_bridge, cursor_overlay_widget, blink_debug_overlay_widget, keyboard_panel_widget, mini_camera_widget

    if not PYQT5_AVAILABLE:
        print("ERROR: PyQt5 is required for system-level overlay mode.")
        print("Install with: pip install PyQt5")
        sys.exit(1)

    qt_app = QtWidgets.QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    gaze_click_bridge = GazeClickBridge()

    cursor_overlay_widget = CursorOverlay(radius=80)
    cursor_overlay_widget.show()

    if DEBUG_BLINK_HUD:
        blink_debug_overlay_widget = BlinkDebugOverlay()
        blink_debug_overlay_widget.set_message(
            "Blink debug active.\n"
            "Waiting for blink events...\n"
            "Fields will show raw hit, resolved target, and click path."
        )
        blink_debug_overlay_widget.show()

    mini_camera_widget = MiniCameraOverlay()
    mini_camera_widget.show()

    global keyboard_panel_widget
    keyboard_panel = KeyboardOverlayPanel()
    keyboard_panel_widget = keyboard_panel
    camera_panel = CameraOverlayPanel()
    emergency_panel = EmergencyOverlayPanel()
    control_panel = ControlOverlayPanel()

    floating_bar = FloatingControlBar({
        "keyboard": keyboard_panel,
        "camera": camera_panel,
        "emergency": emergency_panel,
        "control": control_panel,
    })
    floating_bar.show()

    print("[Overlay] Native floating overlay launched (always-on-top, frameless, transparent).")
    qt_app.exec_()
