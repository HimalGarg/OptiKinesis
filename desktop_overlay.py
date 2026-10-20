"""
Desktop Overlay UI Module for OptiKinesis

This module contains the PyQt5 floating overlay interface:
- CursorOverlay (gaze ring indicator)
- BlinkDebugOverlay (HUD debug info)
- OverlayPanelBase & DraggableOverlayWidget (frameless window bases)
- KeyboardOverlayPanel (on-screen QWERTY keyboard)
- CameraOverlayPanel (live tracking video feed)
- EmergencyOverlayPanel (SOS message/calls)
- ControlOverlayPanel (cursor speed & sensitivity settings)
- FloatingControlBar (main pill-shaped control bar) & PillButton (its painted buttons)
- GazeClickBridge & dispatch_blink_click (Qt click routing)
"""

import os
import sys
import time
import math
import html
import threading
import urllib.parse
import webbrowser
import numpy as np
import cv2
import pyautogui
import ctypes
import logging
try:
    from ctypes import wintypes
except ImportError:
    wintypes = None

from head_calibration import POINT_LABELS, CalibrationSession
from web_assist import MISSING_KEY_MESSAGE, WebAssistError, capture_browser_url, foreground_window
from word_suggestions import MAX_SUGGESTIONS, completion_suffix, current_word, suggest
from speech_input import SpeechInputError, listen_and_transcribe

logger = logging.getLogger("optikinesis.overlay")

try:
    from PyQt5 import QtWidgets, QtGui, QtCore, QtTest
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False

# Global state references provided by main.py or initialized with defaults
SCREEN_W, SCREEN_H = pyautogui.size()
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

TOP_MARGIN = 10
BAR_IDLE_HEIGHT = 52
BAR_EXPANDED_HEIGHT = 84
BAR_HEIGHT = BAR_EXPANDED_HEIGHT
PANEL_TOP_OFFSET = TOP_MARGIN + BAR_EXPANDED_HEIGHT + 20
LOCK_DELAY = 0.5
DEBUG_BLINK_HUD = False

# Callbacks and data providers set by main.py
_settings = {
    "emergency_contact": "",
    "cursor_scope": 1.0,
    "blink_sensitivity": 0.2,
    "cursor_speed": 0.5,
    "overlay_enabled": True,
    "mouse_control_enabled": True
}
_action_executor = None
_whatsapp_sender = None
_frame_provider = None
_voice_executor = None
_fatigue_provider = None
_shutdown_callback = None
_status_provider = None
_pose_provider = None
_calibration_start = None
_calibration_finish = None
_calibration_needed = None
_calibration_running = False
_web_client = None  # web_assist.TinyFishClient, or None without an API key

gaze_click_bridge = None
cursor_overlay_widget = None
blink_debug_overlay_widget = None
keyboard_panel_widget = None
mini_camera_widget = None
notification_overlay_widget = None
notification_bridge = None
floating_bar_widget = None
overlay_panel_widgets = {}
calibration_overlay_widget = None
calibration_bridge = None
web_panel_widget = None


def configure_overlay(settings=None, action_executor=None, whatsapp_sender=None, 
                      frame_provider=None, voice_executor=None, fatigue_provider=None,
                      shutdown_callback=None, lock_delay=0.5, debug_hud=False,
                      status_provider=None, pose_provider=None, calibration_start=None,
                      calibration_finish=None, calibration_needed=None, web_client=None):
    """Configure external callbacks and settings references from main application."""
    global _settings, _action_executor, _whatsapp_sender, _frame_provider, _voice_executor
    global _fatigue_provider, _shutdown_callback, _status_provider, LOCK_DELAY, DEBUG_BLINK_HUD
    global _pose_provider, _calibration_start, _calibration_finish, _calibration_needed
    global _web_client
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
    if fatigue_provider is not None:
        _fatigue_provider = fatigue_provider
    if shutdown_callback is not None:
        _shutdown_callback = shutdown_callback
    if status_provider is not None:
        _status_provider = status_provider
    if pose_provider is not None:
        _pose_provider = pose_provider
    if calibration_start is not None:
        _calibration_start = calibration_start
    if calibration_finish is not None:
        _calibration_finish = calibration_finish
    if calibration_needed is not None:
        _calibration_needed = calibration_needed
    if web_client is not None:
        _web_client = web_client
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


def request_exit():
    """Request Qt shutdown safely from callbacks or worker threads."""
    if not PYQT5_AVAILABLE:
        return
    app = QtWidgets.QApplication.instance()
    if app is not None:
        QtCore.QTimer.singleShot(0, app.quit)


def dispatch_notification(message):
    """Show a non-focus-stealing overlay notification from any thread."""
    if PYQT5_AVAILABLE and notification_bridge is not None:
        notification_bridge.message_requested.emit(str(message))
    else:
        logger.info("Notification: %s", message)


def start_head_calibration():
    """Open the 5-point calibration screen.  Call from the Qt thread."""
    if calibration_overlay_widget is None:
        logger.warning("Calibration screen is not available")
        return False
    return calibration_overlay_widget.begin()


def cancel_calibration():
    """Cancel a running calibration from any thread (caregiver Esc/F12)."""
    if PYQT5_AVAILABLE and calibration_bridge is not None:
        calibration_bridge.cancel_requested.emit()


def is_calibration_running():
    return _calibration_running


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
            if _settings.get("overlay_enabled", True) and not _calibration_running:
                x, y = pyautogui.position()
                self.move(x - self.radius, y - self.radius)
                self.draw_circle()
                self.show()
                self.raise_()
            else:
                self.hide()

        def draw_circle(self):
            img = np.zeros((self.diameter, self.diameter, 4), dtype=np.uint8)
            color = (255, 97, 97, 255) if _settings.get("system_paused", False) else (0, 255, 0, 255)
            cv2.circle(img, (self.radius + 2, self.radius + 2), self.radius - 5, color, 10)
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


    class NotificationToastOverlay(QtWidgets.QWidget):
        """Transient reminder/fatigue notification that never takes focus."""

        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
                | QtCore.Qt.Tool
                | QtCore.Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            if hasattr(QtCore.Qt, "WindowTransparentForInput"):
                self.setWindowFlag(QtCore.Qt.WindowTransparentForInput, True)

            self.setFixedSize(min(680, SCREEN_W - 40), 94)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.label = QtWidgets.QLabel(self)
            self.label.setAlignment(QtCore.Qt.AlignCenter)
            self.label.setWordWrap(True)
            self.label.setStyleSheet("""
                QLabel {
                    background: rgba(45, 35, 14, 242);
                    border: 2px solid #ffbc63;
                    border-radius: 14px;
                    color: #ffe4b8;
                    font-size: 18px;
                    font-weight: 700;
                    padding: 12px;
                }
            """)
            layout.addWidget(self.label)
            self.move((SCREEN_W - self.width()) // 2, TOP_MARGIN + BAR_HEIGHT + 16)

            self.hide_timer = QtCore.QTimer(self)
            self.hide_timer.setSingleShot(True)
            self.hide_timer.timeout.connect(self.hide)

            self._was_fatigued = False
            self.fatigue_timer = QtCore.QTimer(self)
            self.fatigue_timer.timeout.connect(self._poll_fatigue)
            self.fatigue_timer.start(500)
            self.hide()

        @QtCore.pyqtSlot(str)
        def show_message(self, message):
            self.label.setText(message)
            self.show()
            self.raise_()
            self.hide_timer.start(5000)

        def _poll_fatigue(self):
            if _fatigue_provider is None:
                return
            try:
                state = _fatigue_provider() or {}
                is_fatigued = bool(state.get("is_fatigued"))
                if is_fatigued and not self._was_fatigued:
                    self.show_message(
                        "Fatigue detected: blink control is less sensitive. "
                        "Please rest your eyes when it is safe."
                    )
                self._was_fatigued = is_fatigued
            except Exception as exc:
                logger.debug("Fatigue status check failed: %s", exc)


    class NotificationBridge(QtCore.QObject):
        message_requested = QtCore.pyqtSignal(str)

        def __init__(self, toast):
            super().__init__()
            self.message_requested.connect(toast.show_message, QtCore.Qt.QueuedConnection)


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


    class VoiceTypingBridge(QtCore.QObject):
        """Delivers voice-typing progress from the recording thread to the Qt thread."""

        recognizing = QtCore.pyqtSignal(int)
        finished = QtCore.pyqtSignal(int, str, str)  # token, text, error


    class KeyboardOverlayPanel(OverlayPanelBase):
        """Virtual QWERTY keyboard overlay panel."""

        def __init__(self):
            super().__init__(
                "",
                width=min(1400, int(SCREEN_W * 0.85)),
                height=min(400, int(SCREEN_H * 0.5)),
            )

            # Hide the header entirely for a compact borderless look
            self.header.hide()

            self.caps_lock = False
            self.shift = False
            self.caps_btn = None
            self.shift_buttons = []
            self.dynamic_buttons = []
            self.active_modifiers = set()
            self.modifier_buttons = {}

            # Compose mode: keys type into an overlay field (e.g. the Web
            # panel's question box) instead of the app in front.
            self._compose = None
            # Word suggestions only consider text typed after this position,
            # so a click elsewhere on screen starts a fresh word.
            self._context_start = 0
            self.suggestion_buttons = []
            self._suggested_words = []

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

            suggestion_row = QtWidgets.QHBoxLayout()
            suggestion_row.setContentsMargins(0, 0, 0, 0)
            suggestion_row.setSpacing(0)

            # Voice typing: speak instead of typing.
            self._voice_token = 0
            self._voice_stop = None
            self._voice_state = "idle"
            self._voice_bridge = VoiceTypingBridge()
            self._voice_bridge.recognizing.connect(self._on_voice_recognizing, QtCore.Qt.QueuedConnection)
            self._voice_bridge.finished.connect(self._on_voice_finished, QtCore.Qt.QueuedConnection)
            self._voice_message_timer = QtCore.QTimer(self)
            self._voice_message_timer.setSingleShot(True)
            self._voice_message_timer.timeout.connect(lambda: self._set_voice_state("idle"))
            self.mic_btn = QtWidgets.QPushButton("")
            self.mic_btn.setObjectName("micBtn")
            self.mic_btn.setFocusPolicy(QtCore.Qt.NoFocus)
            self.mic_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.mic_btn.setIconSize(QtCore.QSize(26, 26))
            self.mic_btn.setStyleSheet("""
                QPushButton {
                    background-color: #0f2f45;
                    color: #e0f2fe;
                    border: 1px solid #2d6a8f;
                    border-radius: 0px;
                    font-size: 20px;
                    font-weight: 700;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    border-color: #2dd4ff;
                    background-color: rgba(45, 212, 255, 70);
                }
                QPushButton[voice="listening"] {
                    background-color: #7f1d1d;
                    border-color: #f87171;
                    color: #ffffff;
                }
                QPushButton[voice="recognizing"] {
                    background-color: #3b2f0b;
                    border-color: #fbbf24;
                    color: #fde68a;
                }
                QPushButton[voice="message"] {
                    color: #fbbf24;
                    font-size: 15px;
                }
            """)
            self.mic_btn.clicked.connect(lambda _checked=False: self._toggle_voice())
            suggestion_row.addWidget(self.mic_btn, 1)
            self._set_voice_state("idle")

            for index in range(MAX_SUGGESTIONS):
                btn = QtWidgets.QPushButton("")
                btn.setObjectName("suggestBtn")
                btn.setFocusPolicy(QtCore.Qt.NoFocus)
                btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #0b2233;
                        color: #7dd3fc;
                        border: 1px solid #1f4f6e;
                        border-radius: 0px;
                        font-size: 24px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        border-color: #2dd4ff;
                        color: #ffffff;
                        background-color: rgba(45, 212, 255, 70);
                    }
                    QPushButton:disabled {
                        color: #4b6a82;
                        font-size: 16px;
                        font-weight: 400;
                    }
                """)
                btn.clicked.connect(lambda _checked=False, i=index: self._accept_suggestion(i))
                suggestion_row.addWidget(btn, 1)
                self.suggestion_buttons.append(btn)
            keyboard_rows.addLayout(suggestion_row, 1)

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
            self.external_action_buttons = (google_btn, yt_btn, ext_btn)
            for btn in self.external_action_buttons:
                btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                btn.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Bold))
                btn.setStyleSheet(action_css + "QPushButton:disabled { color: #3d566e; }")

            close_key_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            close_key_btn.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Bold))
            close_key_btn.setStyleSheet(close_css)

            self._refresh_key_labels()
            self._refresh_modifier_visuals()
            self._refresh_suggestions()

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
            if key in {"Ctrl", "Alt", "Win"}:
                self.modifier_buttons[key] = btn

            if (len(key) == 1 and key.isprintable()) or shift_symbol is not None:
                self.dynamic_buttons.append((btn, key, shift_symbol))

            return btn

        def _display_for_key(self, key, shift_symbol=None):
            if len(key) == 1 and key.isalpha():
                return key.upper() if (self.caps_lock ^ self.shift) else key.lower()
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
            for key, btn in self.modifier_buttons.items():
                btn.setProperty("active", key in self.active_modifiers)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

        # ─── compose mode (typing into an overlay field) ─────────────────

        @property
        def is_composing(self):
            return self._compose is not None

        def start_compose(self, on_change, on_submit, on_cancel=None):
            """Route typing to an overlay field until stop_compose().

            on_change(text, cursor_pos) runs after every edit, on_submit(text)
            when Enter is pressed, on_cancel() if the keyboard is closed.
            """
            self._compose = {"change": on_change, "submit": on_submit, "cancel": on_cancel}
            self.text_area.clear()
            self._context_start = 0
            self.active_modifiers.clear()
            self._refresh_modifier_visuals()
            for btn in self.external_action_buttons:
                btn.setEnabled(False)
            self._after_edit()
            self.show()
            self.raise_()

        def stop_compose(self):
            """Leave compose mode without notifying the field owner."""
            self._compose = None
            self.text_area.clear()
            self._context_start = 0
            for btn in self.external_action_buttons:
                btn.setEnabled(True)
            self._refresh_suggestions()

        def hideEvent(self, event):
            if hasattr(self, "mic_btn"):
                self._stop_voice()
            if self._compose is not None:
                on_cancel = self._compose.get("cancel")
                self.stop_compose()
                if on_cancel is not None:
                    try:
                        on_cancel()
                    except Exception:
                        logger.exception("Compose cancel handler failed")
            super().hideEvent(event)

        def showEvent(self, event):
            if self._compose is None:
                # Start word suggestions fresh for whatever field is now in use.
                self.note_external_click()
            super().showEvent(event)

        def note_external_click(self):
            """The user clicked elsewhere: the next word starts from scratch."""
            if self._compose is None:
                self._context_start = len(self.text_area.toPlainText())
                self._refresh_suggestions()

        def _after_edit(self):
            self._refresh_suggestions()
            if self._compose is not None:
                try:
                    cursor = self.text_area.textCursor()
                    self._compose["change"](self.text_value(), cursor.position())
                except Exception:
                    logger.exception("Compose change handler failed")

        # ─── word suggestions ────────────────────────────────────────────

        def _current_prefix(self):
            cursor = self.text_area.textCursor()
            text = self.text_area.toPlainText()
            position = cursor.position()
            if position < self._context_start:
                return ""
            return current_word(text[self._context_start:position])

        def _refresh_suggestions(self):
            prefix = self._current_prefix()
            words = suggest(prefix, MAX_SUGGESTIONS) if prefix else []
            self._suggested_words = words
            for index, btn in enumerate(self.suggestion_buttons):
                if index < len(words):
                    btn.setText(words[index])
                    btn.setEnabled(True)
                else:
                    btn.setText("Word suggestions appear as you type" if index == 0 and not words else "")
                    btn.setEnabled(False)

        def _accept_suggestion(self, index):
            if index >= len(self._suggested_words):
                return
            word = self._suggested_words[index]
            prefix = self._current_prefix()
            self.insert_text(completion_suffix(prefix, word) + " ")
            if self.shift:
                self.shift = False
                self._refresh_modifier_visuals()
                self._refresh_key_labels()

        # ─── voice typing ────────────────────────────────────────────────

        @staticmethod
        def _mic_icon(color):
            pixmap = QtGui.QPixmap(28, 28)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            _draw_pill_icon(painter, "mic", QtCore.QRectF(2, 2, 24, 24), color)
            painter.end()
            return QtGui.QIcon(pixmap)

        def _set_voice_state(self, state, message=""):
            self._voice_state = state
            labels = {
                "idle": ("Speak", QtGui.QColor(224, 242, 254)),
                "listening": ("Listening… (press to stop)", QtGui.QColor(255, 255, 255)),
                "recognizing": ("Recognizing…", QtGui.QColor(253, 230, 138)),
                "message": (message, QtGui.QColor(251, 191, 36)),
            }
            text, color = labels.get(state, labels["idle"])
            self.mic_btn.setText("  " + text)
            self.mic_btn.setIcon(self._mic_icon(color))
            self.mic_btn.setProperty("voice", state)
            self.mic_btn.style().unpolish(self.mic_btn)
            self.mic_btn.style().polish(self.mic_btn)

        def _toggle_voice(self):
            if self._voice_state == "listening":
                if self._voice_stop is not None:
                    self._voice_stop.set()  # finish now and recognize what was said
                return
            if self._voice_state == "recognizing":
                return
            self._voice_message_timer.stop()
            self._voice_token += 1
            token = self._voice_token
            stop = threading.Event()
            self._voice_stop = stop
            self._set_voice_state("listening")
            bridge = self._voice_bridge

            def worker():
                text, error = "", ""
                try:
                    text = listen_and_transcribe(
                        stop, on_recognizing=lambda: bridge.recognizing.emit(token)
                    )
                except SpeechInputError as exc:
                    error = exc.message
                except Exception:
                    logger.exception("Voice typing failed")
                    error = "Voice typing didn't work. Please try again."
                bridge.finished.emit(token, text, error)

            threading.Thread(target=worker, name="voice-typing", daemon=True).start()

        def _on_voice_recognizing(self, token):
            if token == self._voice_token and self._voice_state == "listening":
                self._set_voice_state("recognizing")

        def _on_voice_finished(self, token, text, error):
            if token != self._voice_token:
                return  # cancelled (keyboard closed) or superseded
            self._voice_stop = None
            if error:
                self._set_voice_state("message", error)
                self._voice_message_timer.start(3500)
                return
            self._set_voice_state("idle")
            self._insert_dictation(text)

        def _insert_dictation(self, text):
            text = (text or "").strip()
            if not text:
                return
            cursor = self.text_area.textCursor()
            before = self.text_area.toPlainText()[self._context_start:cursor.position()]
            if before and not before[-1].isspace():
                text = " " + text
            self.insert_text(text + " ")

        def _stop_voice(self):
            if self._voice_stop is not None:
                self._voice_stop.set()
                self._voice_stop = None
            self._voice_token += 1  # ignore any late result
            self._voice_message_timer.stop()
            self._set_voice_state("idle")

        # ─── key handling ────────────────────────────────────────────────

        def _consume_modifiers(self, key):
            if not self.active_modifiers:
                return False
            if self._compose is not None:
                # Shortcuts would act on another app; drop them while composing.
                self.active_modifiers.clear()
                self._refresh_modifier_visuals()
                return True
            key_map = {
                "Backspace": "backspace", "Tab": "tab", "Enter": "enter",
                "Space": "space", "Left": "left", "Right": "right",
            }
            target = key_map.get(key, str(key).lower())
            modifiers = [name.lower() for name in ("Ctrl", "Alt", "Win") if name in self.active_modifiers]
            if self.shift:
                modifiers.append("shift")
            try:
                pyautogui.hotkey(*modifiers, target)
            except Exception as exc:
                logger.warning("Virtual keyboard shortcut failed: %s", exc)
            self.active_modifiers.clear()
            self.shift = False
            self._refresh_modifier_visuals()
            self._refresh_key_labels()
            return True

        def _on_key_press(self, key, shift_symbol=None):
            if key in {"Ctrl", "Alt", "Win"}:
                if key in self.active_modifiers:
                    self.active_modifiers.remove(key)
                else:
                    self.active_modifiers.add(key)
                self._refresh_modifier_visuals()
                return
            if key == "Menu":
                if self._compose is not None:
                    return
                try:
                    pyautogui.press("apps")
                except Exception as exc:
                    logger.warning("Virtual keyboard menu key failed: %s", exc)
                return
            if key not in {"Caps", "Shift", "Clear"} and self._consume_modifiers(key):
                return
            if key == "Backspace":
                self.backspace()
                return
            if key == "Tab":
                if self._compose is None:
                    self.insert_text("\t")
                return
            if key == "Enter":
                if self._compose is not None:
                    try:
                        self._compose["submit"](self.text_value())
                    except Exception:
                        logger.exception("Compose submit handler failed")
                    return
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
            self.insert_text(self._resolve_char(key, shift_symbol))
            if self.shift:
                self.shift = False
                self._refresh_modifier_visuals()
                self._refresh_key_labels()

        def text_value(self):
            return self.text_area.toPlainText()

        def insert_text(self, value):
            try:
                cursor = self.text_area.textCursor()
                cursor.insertText(value)
                self.text_area.setTextCursor(cursor)
                if self._compose is None:
                    if value == "\n":
                        pyautogui.press("enter")
                    elif value == "\t":
                        pyautogui.press("tab")
                    else:
                        pyautogui.write(value)
            except Exception as exc:
                logger.warning("Virtual keyboard typing failed: %s", exc)
            self._after_edit()

        def backspace(self):
            try:
                cursor = self.text_area.textCursor()
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                elif cursor.position() > 0:
                    cursor.deletePreviousChar()
                self.text_area.setTextCursor(cursor)
                if cursor.position() < self._context_start:
                    self._context_start = cursor.position()
                if self._compose is None:
                    pyautogui.press('backspace')
            except Exception as exc:
                logger.warning("Virtual keyboard backspace failed: %s", exc)
            self._after_edit()

        def clear_text(self):
            try:
                self.text_area.clear()
                self._context_start = 0
                if self._compose is None:
                    pyautogui.hotkey('ctrl', 'a')
                    pyautogui.press('backspace')
            except Exception as exc:
                logger.warning("Virtual keyboard clear failed: %s", exc)
            self._after_edit()

        def move_cursor(self, delta):
            try:
                cursor = self.text_area.textCursor()
                if delta < 0:
                    cursor.movePosition(QtGui.QTextCursor.Left, n=abs(delta))
                    if self._compose is None:
                        pyautogui.press('left', presses=abs(delta))
                else:
                    cursor.movePosition(QtGui.QTextCursor.Right, n=delta)
                    if self._compose is None:
                        pyautogui.press('right', presses=delta)
                self.text_area.setTextCursor(cursor)
            except Exception as exc:
                logger.warning("Virtual keyboard cursor move failed: %s", exc)
            self._after_edit()


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
            emergency_btn(
                "DIAL 100",
                lambda: self.confirm_emergency(
                    "police", lambda: _perform_action("emergency_police")
                ),
                1,
                0,
            )
            emergency_btn(
                "DIAL 112",
                lambda: self.confirm_emergency(
                    "ambulance", lambda: _perform_action("emergency_ambulance")
                ),
                1,
                1,
            )

            self.status = QtWidgets.QLabel("Emergency actions ready.", self.body)
            self.status.setStyleSheet("color:#9fb6cf; font-size:18px;")
            self.body_layout.addWidget(self.status)
            self._pending_emergency = None
            self._pending_until = 0.0

        def confirm_emergency(self, action_name, callback):
            now = time.time()
            if self._pending_emergency != action_name or now > self._pending_until:
                self._pending_emergency = action_name
                self._pending_until = now + 5.0
                self.status.setText(
                    f"Safety check: activate {action_name.upper()} again within 5 seconds."
                )
                return
            self._pending_emergency = None
            self._pending_until = 0.0
            callback()
            self.status.setText(f"Emergency {action_name} action triggered.")

        def save_contact(self):
            value = self.contact_input.text().strip()
            if not value:
                self.status.setText("Enter a valid contact number.")
                return
            _settings["emergency_contact"] = value
            _perform_action("save_settings")
            self.status.setText(f"Emergency contact saved: {value}")

        def msg_contact(self):
            self.save_contact()
            if not _settings.get("emergency_contact"):
                self.status.setText("Set contact before sending message.")
                return
            self.confirm_emergency(
                "message",
                lambda: _send_whatsapp(
                    _settings["emergency_contact"],
                    "SOS! I need help. Sent via OptiKinesis.",
                ),
            )

        def call_contact(self):
            self.save_contact()
            if not _settings.get("emergency_contact"):
                self.status.setText("Set contact before calling.")
                return
            self.confirm_emergency(
                "contact call", lambda: _perform_action("emergency_call")
            )


    class ControlOverlayPanel(OverlayPanelBase):
        """Settings control module panel."""

        def __init__(self):
            super().__init__("CONTROL MODULE", width=min(1100, int(SCREEN_W * 0.72)), height=min(760, int(SCREEN_H * 0.72)))

            self.speed_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self.body)
            self.speed_slider.setRange(1, 15)
            self.speed_slider.setValue(int(_settings.get("cursor_speed", 0.5) * 10))

            self.scope_spin = QtWidgets.QDoubleSpinBox(self.body)
            self.scope_spin.setRange(0.35, 2.0)
            self.scope_spin.setSingleStep(0.05)
            self.scope_spin.setValue(float(_settings.get("cursor_scope", 1.0)))

            self.blink_spin = QtWidgets.QDoubleSpinBox(self.body)
            self.blink_spin.setRange(0.15, 0.35)
            self.blink_spin.setSingleStep(0.01)
            self.blink_spin.setValue(float(_settings.get("blink_sensitivity", 0.2)))

            self.blinks_required_spin = QtWidgets.QSpinBox(self.body)
            self.blinks_required_spin.setRange(1, 2)
            self.blinks_required_spin.setValue(int(_settings.get("blinks_to_click", 2)))

            self.blink_window_spin = QtWidgets.QDoubleSpinBox(self.body)
            self.blink_window_spin.setRange(0.5, 3.0)
            self.blink_window_spin.setSingleStep(0.1)
            self.blink_window_spin.setValue(float(_settings.get("blink_window", 1.5)))

            self.lock_delay_spin = QtWidgets.QDoubleSpinBox(self.body)
            self.lock_delay_spin.setRange(0.5, 5.0)
            self.lock_delay_spin.setSingleStep(0.1)
            self.lock_delay_spin.setValue(float(_settings.get("lock_delay", LOCK_DELAY)))

            self.status = QtWidgets.QLabel("Control settings ready.", self.body)
            self.status.setStyleSheet("color:#9fb6cf; font-size:18px;")

            for label_text, widget in [
                ("Cursor Speed", self.speed_slider),
                ("Cursor Scope (only used until 5-point calibration is done)", self.scope_spin),
                ("Blink Sensitivity", self.blink_spin),
                ("Deliberate Blinks Per Click", self.blinks_required_spin),
                ("Blink Sequence Window (seconds)", self.blink_window_spin),
                ("Post-click Lock Delay (seconds)", self.lock_delay_spin),
            ]:
                label = QtWidgets.QLabel(label_text, self.body)
                label.setStyleSheet("color:#d8e8f9; font-size:16px; font-weight:600;")
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

            safety_row = QtWidgets.QHBoxLayout()
            self.body_layout.addLayout(safety_row)

            head_calibration_btn = QtWidgets.QPushButton("5-POINT CALIBRATION")
            head_calibration_btn.clicked.connect(self.start_calibration)
            safety_row.addWidget(head_calibration_btn)

            calibrate_btn = QtWidgets.QPushButton("RE-CENTER (F9)")
            calibrate_btn.clicked.connect(self.calibrate)
            safety_row.addWidget(calibrate_btn)

            pause_btn = QtWidgets.QPushButton("PAUSE / RESUME (F12)")
            pause_btn.clicked.connect(self.toggle_pause)
            safety_row.addWidget(pause_btn)

            exit_btn = QtWidgets.QPushButton("EXIT OPTIKINESIS")
            exit_btn.clicked.connect(self.exit_application)
            safety_row.addWidget(exit_btn)

            for btn in (save_btn, overlay_btn, mouse_btn, head_calibration_btn,
                        calibrate_btn, pause_btn, exit_btn):
                btn.setMinimumHeight(52)
                btn.setStyleSheet("""
                    border: 2px solid #355173;
                    border-radius: 12px;
                    background: rgba(29, 43, 63, 235);
                    color: #eef8ff;
                    font-size: 18px;
                    font-weight: 700;
                    padding: 0 12px;
                """)
            exit_btn.setStyleSheet(exit_btn.styleSheet() + "QPushButton { border-color:#8b3a3a; color:#ffd4d4; }")

            self.body_layout.addWidget(self.status)

        def save_settings(self):
            global LOCK_DELAY
            _settings["cursor_speed"] = float(self.speed_slider.value()) / 10.0
            _settings["cursor_scope"] = float(self.scope_spin.value())
            _settings["blink_sensitivity"] = float(self.blink_spin.value())
            _settings["blinks_to_click"] = int(self.blinks_required_spin.value())
            _settings["blink_window"] = float(self.blink_window_spin.value())
            LOCK_DELAY = float(self.lock_delay_spin.value())
            _settings["lock_delay"] = LOCK_DELAY
            result = _perform_action("save_settings")
            self.status.setText(
                "Control settings saved." if result.get("status") == "saved" else "Could not save settings."
            )

        def toggle_setting(self, key):
            _settings[key] = not _settings.get(key, True)
            _perform_action("save_settings")
            state = "ON" if _settings[key] else "OFF"
            self.status.setText(f"{key} is now {state}.")

        def calibrate(self):
            result = _perform_action("calibrate")
            self.status.setText(result.get("message", result.get("status", "Re-center requested")))

        def start_calibration(self):
            if start_head_calibration():
                self.status.setText("5-point calibration started.")
            else:
                self.status.setText("Calibration needs the camera to be running.")

        def toggle_pause(self):
            result = _perform_action("toggle_pause")
            paused = result.get("system_paused", False)
            self.status.setText("SYSTEM PAUSED" if paused else "System resumed.")

        def exit_application(self):
            _perform_action("exit_application")
            request_exit()


    # ─── FLOATING CONTROL PILL ───────────────────────────────────────────
    #
    # The pill is fully custom painted with antialiasing.  Qt stylesheet
    # rounded corners render jagged on translucent windows, and resizing a
    # frameless window makes it visibly jump, so the window keeps one fixed
    # size and only the painted pill animates inside it.  A window mask keeps
    # the unused transparent area click-through.

    PILL_FONT_FAMILY = "Segoe UI"
    PILL_TEXT = QtGui.QColor(241, 245, 249)
    PILL_TEXT_MUTED = QtGui.QColor(148, 160, 178)
    PILL_ICON = QtGui.QColor(226, 232, 240)
    PILL_ACCENT = QtGui.QColor(56, 189, 248)
    PILL_DANGER = QtGui.QColor(248, 113, 113)
    PILL_DANGER_TEXT = QtGui.QColor(252, 165, 165)
    PILL_DANGER_BRIGHT = QtGui.QColor(255, 228, 228)

    # Tracker state -> (status line text, indicator colour)
    PILL_STATUS_STYLES = {
        "tracking": ("Tracking", QtGui.QColor(52, 211, 153)),
        "no_face": ("Face not detected", QtGui.QColor(251, 191, 36)),
        "paused": ("Paused · F12 resumes", QtGui.QColor(248, 113, 113)),
        "mouse_off": ("Mouse control off", QtGui.QColor(148, 163, 184)),
        "no_camera": ("No camera found", QtGui.QColor(248, 113, 113)),
        "ready": ("Ready", QtGui.QColor(52, 211, 153)),
    }

    def _pill_font(pixel_size, weight=QtGui.QFont.Normal):
        font = QtGui.QFont(PILL_FONT_FAMILY)
        font.setPixelSize(pixel_size)
        font.setWeight(weight)
        font.setStyleStrategy(QtGui.QFont.PreferAntialias)
        return font

    def _with_alpha(color, alpha):
        result = QtGui.QColor(color)
        result.setAlpha(max(0, min(255, int(alpha))))
        return result

    def _mix_colors(start, end, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return QtGui.QColor(
            int(start.red() + (end.red() - start.red()) * amount),
            int(start.green() + (end.green() - start.green()) * amount),
            int(start.blue() + (end.blue() - start.blue()) * amount),
            int(start.alpha() + (end.alpha() - start.alpha()) * amount),
        )

    def _point_toward(origin, target, distance):
        dx = target.x() - origin.x()
        dy = target.y() - origin.y()
        length = math.hypot(dx, dy) or 1.0
        step = min(distance, length / 2.0) / length
        return QtCore.QPointF(origin.x() + dx * step, origin.y() + dy * step)

    def _rounded_polygon_path(points, radius):
        """Closed polygon path whose corners are softened with quadratic curves."""
        path = QtGui.QPainterPath()
        count = len(points)
        for index, corner in enumerate(points):
            entry = _point_toward(corner, points[index - 1], radius)
            leave = _point_toward(corner, points[(index + 1) % count], radius)
            if index == 0:
                path.moveTo(entry)
            else:
                path.lineTo(entry)
            path.quadTo(corner, leave)
        path.closeSubpath()
        return path

    def _draw_pill_icon(painter, name, rect, color):
        """Draw a 24-unit line icon scaled into rect (replaces uneven emoji glyphs)."""
        point = QtCore.QPointF
        painter.save()
        painter.translate(rect.topLeft())
        painter.scale(rect.width() / 24.0, rect.height() / 24.0)
        pen = QtGui.QPen(color, 2.0)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)

        def dot(x, y):
            painter.save()
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(point(x, y), 1.15, 1.15)
            painter.restore()

        if name == "mic":
            painter.drawRoundedRect(QtCore.QRectF(9, 2, 6, 13), 3, 3)
            arc = QtGui.QPainterPath(point(19, 10))
            arc.lineTo(19, 12)
            arc.arcTo(QtCore.QRectF(5, 5, 14, 14), 0, -180)
            arc.lineTo(5, 10)
            painter.drawPath(arc)
            painter.drawLine(point(12, 19), point(12, 22))
        elif name == "keyboard":
            painter.drawRoundedRect(QtCore.QRectF(2, 4, 20, 16), 2.5, 2.5)
            for x in (6, 10, 14, 18):
                dot(x, 8)
            for x in (8, 12, 16):
                dot(x, 12)
            painter.drawLine(point(7, 16), point(17, 16))
        elif name == "camera":
            painter.drawRoundedRect(QtCore.QRectF(2, 6, 14, 12), 2.5, 2.5)
            painter.drawPolyline(QtGui.QPolygonF([
                point(16, 10.5), point(21.5, 7.2), point(21.5, 16.8), point(16, 13.5),
            ]))
        elif name == "alert":
            painter.drawPath(_rounded_polygon_path(
                [point(12, 3), point(22, 20.5), point(2, 20.5)], 2.4
            ))
            painter.drawLine(point(12, 9.5), point(12, 13.5))
            dot(12, 17)
        elif name == "sliders":
            for x1, x2, y in ((21, 14, 4), (10, 3, 4), (21, 12, 12),
                              (8, 3, 12), (21, 16, 20), (12, 3, 20)):
                painter.drawLine(point(x1, y), point(x2, y))
            for x, y in ((14, 4), (8, 12), (16, 20)):
                painter.drawLine(point(x, y - 2), point(x, y + 2))
        elif name == "globe":
            painter.drawEllipse(QtCore.QRectF(2, 2, 20, 20))
            painter.drawEllipse(QtCore.QRectF(7.5, 2, 9, 20))
            painter.drawLine(point(2, 12), point(22, 12))
        elif name == "back":
            painter.drawPolyline(QtGui.QPolygonF([point(14, 5), point(7, 12), point(14, 19)]))
        elif name == "up":
            painter.drawPolyline(QtGui.QPolygonF([point(5, 15), point(12, 8), point(19, 15)]))
        elif name == "down":
            painter.drawPolyline(QtGui.QPolygonF([point(5, 9), point(12, 16), point(19, 9)]))
        elif name == "external":
            painter.drawPolyline(QtGui.QPolygonF([
                point(18, 13), point(18, 20), point(4, 20), point(4, 6), point(11, 6),
            ]))
            painter.drawLine(point(14, 3), point(21, 3))
            painter.drawLine(point(21, 3), point(21, 10))
            painter.drawLine(point(21, 3), point(11, 13))
        elif name == "page":
            painter.drawRoundedRect(QtCore.QRectF(5, 2, 14, 20), 2.0, 2.0)
            for y in (8, 12, 16):
                painter.drawLine(point(8.5, y), point(15.5, y))
        elif name == "search":
            painter.drawEllipse(QtCore.QRectF(3, 3, 13, 13))
            painter.drawLine(point(14.5, 14.5), point(21, 21))
        painter.restore()


    class PillButton(QtWidgets.QAbstractButton):
        """Large painted icon button with animated hover, active and click feedback.

        It stays a real QAbstractButton so GazeClickBridge can still press it
        directly on a blink.
        """

        WIDTH = 88
        HEIGHT = 66
        RADIUS = 16.0

        def __init__(self, icon_name, label, parent=None, danger=False):
            super().__init__(parent)
            self.icon_name = icon_name
            self.danger = danger
            self.setText(label)
            self.setAccessibleName(label)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.setFixedSize(self.WIDTH, self.HEIGHT)

            self._hovered = False
            self._active = False
            self._hover_level = 0.0
            self._flash_level = 0.0
            self._label_font = _pill_font(12, QtGui.QFont.DemiBold)

            self._hover_anim = QtCore.QVariantAnimation(self)
            self._hover_anim.setDuration(160)
            self._hover_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            self._hover_anim.valueChanged.connect(self._on_hover_value)

            # A short glow after each press confirms that a blink registered.
            self._flash_anim = QtCore.QVariantAnimation(self)
            self._flash_anim.setDuration(480)
            self._flash_anim.setStartValue(1.0)
            self._flash_anim.setEndValue(0.0)
            self._flash_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            self._flash_anim.valueChanged.connect(self._on_flash_value)
            self.clicked.connect(lambda _checked=False: self._start_flash())

        def sizeHint(self):
            return QtCore.QSize(self.WIDTH, self.HEIGHT)

        def set_hovered(self, hovered):
            hovered = bool(hovered)
            if hovered == self._hovered:
                return
            self._hovered = hovered
            self._hover_anim.stop()
            self._hover_anim.setStartValue(self._hover_level)
            self._hover_anim.setEndValue(1.0 if hovered else 0.0)
            self._hover_anim.start()

        def set_active(self, active):
            active = bool(active)
            if active != self._active:
                self._active = active
                self.update()

        def _start_flash(self):
            self._flash_anim.stop()
            self._flash_anim.start()

        def _on_hover_value(self, value):
            self._hover_level = float(value)
            self.update()

        def _on_flash_value(self, value):
            self._flash_level = float(value)
            self.update()

        def paintEvent(self, event):
            bar = self.parentWidget()
            reveal = float(getattr(bar, "content_reveal", 1.0))
            if reveal <= 0.0:
                return

            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
            if hasattr(bar, "pill_clip_for"):
                painter.setClipPath(bar.pill_clip_for(self))
            painter.setOpacity(reveal)
            painter.translate(0.0, (1.0 - reveal) * 6.0)

            tone = PILL_DANGER if self.danger else PILL_ACCENT
            emphasis = max(self._hover_level, 1.0 if self._active else 0.0)
            card = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

            # Open panel: tinted fill plus an indicator bar under the label.
            # Pointing at the button: a soft lift and an accent ring.
            painter.setPen(QtCore.Qt.NoPen)
            if self._active:
                painter.setBrush(_with_alpha(tone, 34))
                painter.drawRoundedRect(card, self.RADIUS, self.RADIUS)
            if self._hover_level > 0.0:
                painter.setBrush(_with_alpha(QtGui.QColor(255, 255, 255), 18 * self._hover_level))
                painter.drawRoundedRect(card, self.RADIUS, self.RADIUS)
            if self._flash_level > 0.0:
                painter.setBrush(_with_alpha(tone, 130 * self._flash_level))
                painter.drawRoundedRect(card, self.RADIUS, self.RADIUS)
            if self._active:
                painter.setBrush(tone)
                painter.drawRoundedRect(
                    QtCore.QRectF(self.width() / 2.0 - 8.0, self.height() - 7.0, 16.0, 3.0), 1.5, 1.5
                )

            if self._hover_level > 0.0:
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(_with_alpha(tone, 130 * self._hover_level), 1.2))
                painter.drawRoundedRect(card, self.RADIUS, self.RADIUS)

            if self.danger:
                icon_color = _mix_colors(PILL_DANGER_TEXT, PILL_DANGER_BRIGHT, emphasis)
                label_color = icon_color
            else:
                icon_color = _mix_colors(PILL_ICON, tone, emphasis)
                label_color = _mix_colors(PILL_TEXT_MUTED, PILL_TEXT, emphasis)

            icon_size = 24.0
            icon_rect = QtCore.QRectF((self.width() - icon_size) / 2.0, 11.0, icon_size, icon_size)
            _draw_pill_icon(painter, self.icon_name, icon_rect, icon_color)

            painter.setFont(self._label_font)
            painter.setPen(label_color)
            painter.drawText(
                QtCore.QRectF(0.0, 40.0, float(self.width()), 18.0),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                self.text(),
            )
            painter.end()


    class FloatingControlBar(DraggableOverlayWidget):
        """Floating status pill pinned at top center.

        Collapsed, it shows the brand and the live tracking state.  When the
        cursor rests on it, it grows smoothly into a toolbar of large
        gaze-friendly buttons, and it folds back shortly after the cursor
        leaves unless a panel or the voice prompt is open.
        """

        IDLE_W = 196
        IDLE_H = BAR_IDLE_HEIGHT
        EXPANDED_H = BAR_EXPANDED_HEIGHT
        PAD = 14
        BRAND_W = 168
        DIVIDER_GAP = 12
        BUTTON_GAP = 6
        BUTTON_OFFSET = PAD + BRAND_W + 2 * DIVIDER_GAP + 1

        # (panel key, icon, label, is_danger); "voice" opens the voice prompt,
        # every other key toggles the overlay panel with that name.
        BUTTON_SPECS = (
            ("voice", "mic", "Voice", False),
            ("keyboard", "keyboard", "Keyboard", False),
            ("camera", "camera", "Camera", False),
            ("web", "globe", "Web", False),
            ("emergency", "alert", "SOS", True),
            ("control", "sliders", "Settings", False),
        )
        EXPANDED_W = (
            BUTTON_OFFSET
            + len(BUTTON_SPECS) * PillButton.WIDTH
            + (len(BUTTON_SPECS) - 1) * BUTTON_GAP
            + PAD
        )

        SHADOW_X = 18
        SHADOW_TOP = 8
        SHADOW_BOTTOM = 20
        SHADOW_SPREAD = 12
        SHADOW_DROP = 4

        EXPAND_MS = 280
        COLLAPSE_MS = 220
        COLLAPSE_DELAY_MS = 1200
        HOVER_SLOP = 10
        POLL_MS = 40
        MESSAGE_MS = 4500

        def __init__(self, panels):
            window_w = self.EXPANDED_W + 2 * self.SHADOW_X
            window_h = self.EXPANDED_H + self.SHADOW_TOP + self.SHADOW_BOTTOM
            super().__init__(width=window_w, height=window_h)
            self.panels = panels
            self.voice_enabled = False

            self._progress = 0.0
            self._expanded = False
            self._status_key = "ready"
            self._message = ""
            self._title_font = _pill_font(15, QtGui.QFont.DemiBold)
            self._title_font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0.2)
            self._status_font = _pill_font(12)

            self._anim = QtCore.QVariantAnimation(self)
            self._anim.valueChanged.connect(self._on_progress)
            self._anim.finished.connect(self._on_animation_finished)

            self._collapse_timer = QtCore.QTimer(self)
            self._collapse_timer.setSingleShot(True)
            self._collapse_timer.timeout.connect(self._collapse_if_idle)

            self._message_timer = QtCore.QTimer(self)
            self._message_timer.setSingleShot(True)
            self._message_timer.timeout.connect(self._clear_message)

            self.buttons = {}
            for key, icon, label, danger in self.BUTTON_SPECS:
                button = PillButton(icon, label, self, danger=danger)
                button.hide()
                self.buttons[key] = button
            self.buttons["voice"].clicked.connect(self.toggle_voice)
            for key in self.buttons:
                if key == "voice":
                    continue
                self.buttons[key].clicked.connect(
                    lambda _checked=False, name=key: self.toggle_panel(name)
                )
            self._layout_buttons()

            self.set_fixed_position((SCREEN_W - window_w) // 2, TOP_MARGIN - self.SHADOW_TOP)
            self._apply_mask(0.0)
            self._refresh_status()

            # Polling the cursor is steadier than enter/leave events for a
            # head-driven cursor that moves in small jumps.
            self._poll_timer = QtCore.QTimer(self)
            self._poll_timer.timeout.connect(self._poll)
            self._poll_timer.start(self.POLL_MS)

        # ─── GEOMETRY ────────────────────────────────────────────────────

        @property
        def content_reveal(self):
            """0 while collapsed, rising to 1 over the last half of the expansion."""
            return max(0.0, min(1.0, (self._progress - 0.45) / 0.55))

        def _pill_rect(self, progress=None):
            progress = self._progress if progress is None else progress
            width = self.IDLE_W + (self.EXPANDED_W - self.IDLE_W) * progress
            height = self.IDLE_H + (self.EXPANDED_H - self.IDLE_H) * progress
            left = (self.width() - width) / 2.0
            return QtCore.QRectF(left, float(self.SHADOW_TOP), width, height)

        def _pill_path(self):
            rect = self._pill_rect()
            radius = rect.height() / 2.0
            path = QtGui.QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            return path

        def pill_clip_for(self, child):
            """Current pill outline in a child's coordinates, used to clip its paint."""
            return self._pill_path().translated(-child.x(), -child.y())

        def accepts_gaze_point(self, global_point):
            """True when a global point lies on the visible pill, not the clear margin."""
            local = self.mapFromGlobal(global_point)
            return self._pill_rect().contains(QtCore.QPointF(local))

        def _layout_buttons(self):
            rect = self._pill_rect()
            x = rect.left() + self.BUTTON_OFFSET
            y = rect.top() + (rect.height() - PillButton.HEIGHT) / 2.0
            for key, _icon, _label, _danger in self.BUTTON_SPECS:
                self.buttons[key].move(int(round(x)), int(round(y)))
                x += PillButton.WIDTH + self.BUTTON_GAP

        def _apply_mask(self, progress):
            """Limit the window's clickable area to the pill and its shadow."""
            area = self._pill_rect(progress).adjusted(
                -self.SHADOW_X, -self.SHADOW_TOP, self.SHADOW_X, self.SHADOW_BOTTOM
            )
            self.setMask(QtGui.QRegion(area.toAlignedRect().intersected(self.rect())))

        # ─── EXPAND / COLLAPSE ───────────────────────────────────────────

        def expand(self):
            self._collapse_timer.stop()
            if self._expanded:
                return
            self._expanded = True
            self._animate_to(1.0)

        def collapse(self):
            if not self._expanded:
                return
            self._expanded = False
            self._animate_to(0.0)

        def _animate_to(self, target):
            self._anim.stop()
            distance = abs(target - self._progress)
            if distance < 0.001:
                self._on_progress(target)
                self._on_animation_finished()
                return
            expanding = target > self._progress
            if expanding:
                self._apply_mask(1.0)
                for button in self.buttons.values():
                    button.show()
            duration = self.EXPAND_MS if expanding else self.COLLAPSE_MS
            self._anim.setStartValue(float(self._progress))
            self._anim.setEndValue(float(target))
            self._anim.setDuration(max(90, int(duration * distance)))
            self._anim.setEasingCurve(
                QtCore.QEasingCurve.OutCubic if expanding else QtCore.QEasingCurve.InOutCubic
            )
            self._anim.start()

        def _on_progress(self, value):
            self._progress = max(0.0, min(1.0, float(value)))
            self._layout_buttons()
            self.update()

        def _on_animation_finished(self):
            if not self._expanded and self._progress <= 0.001:
                self._progress = 0.0
                for button in self.buttons.values():
                    button.set_hovered(False)
                    button.hide()
                self._apply_mask(0.0)
            self.update()

        def _cursor_over_pill(self, slop):
            local = QtCore.QPointF(self.mapFromGlobal(QtGui.QCursor.pos()))
            return self._pill_rect().adjusted(-slop, -slop, slop, slop).contains(local)

        def _any_panel_visible(self):
            return any(panel.isVisible() for panel in self.panels.values())

        def _poll(self):
            cursor = self.mapFromGlobal(QtGui.QCursor.pos())
            slop = self.HOVER_SLOP if self._expanded else 2
            over_pill = self._pill_rect().adjusted(-slop, -slop, slop, slop).contains(
                QtCore.QPointF(cursor)
            )

            if over_pill:
                self.expand()
            elif self._expanded:
                if self._any_panel_visible() or self.voice_enabled:
                    self._collapse_timer.stop()
                elif not self._collapse_timer.isActive():
                    self._collapse_timer.start(self.COLLAPSE_DELAY_MS)

            interactive = self.content_reveal > 0.85
            for button in self.buttons.values():
                button.set_hovered(
                    interactive and button.isVisible() and button.geometry().contains(cursor)
                )
            self._sync_active_states()
            self._refresh_status()

        def _collapse_if_idle(self):
            if self._cursor_over_pill(self.HOVER_SLOP):
                return
            if self._any_panel_visible() or self.voice_enabled:
                return
            self.collapse()

        def mousePressEvent(self, event):
            if self._pill_rect().contains(QtCore.QPointF(event.pos())):
                self.expand()
                event.accept()
                return
            event.ignore()

        # ─── STATUS ──────────────────────────────────────────────────────

        def _refresh_status(self):
            key = "ready"
            if _status_provider is not None:
                try:
                    key = _status_provider() or "ready"
                except Exception as exc:
                    logger.debug("Status provider failed: %s", exc)
            elif _settings.get("system_paused", False):
                key = "paused"
            if key not in PILL_STATUS_STYLES:
                key = "ready"
            if key != self._status_key:
                self._status_key = key
                self.update()

        def show_status_message(self, text):
            """Show a short-lived message in the status line, then fall back to state."""
            self._message = str(text or "").strip()
            self._message_timer.start(self.MESSAGE_MS)
            self.update()

        def _clear_message(self):
            self._message = ""
            self.update()

        def _sync_active_states(self):
            for name, button in self.buttons.items():
                if name == "voice":
                    button.set_active(self.voice_enabled)
                else:
                    panel = self.panels.get(name)
                    button.set_active(panel is not None and panel.isVisible())

        # ─── PAINTING ────────────────────────────────────────────────────

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
            rect = self._pill_rect()
            radius = rect.height() / 2.0
            self._paint_shadow(painter, rect, radius)
            self._paint_surface(painter, rect, radius)
            self._paint_brand(painter, rect)
            self._paint_divider(painter, rect)
            painter.end()

        def _paint_shadow(self, painter, rect, radius):
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 9))
            base = rect.translated(0.0, self.SHADOW_DROP)
            layers = 8
            for index in range(layers, 0, -1):
                spread = self.SHADOW_SPREAD * index / layers
                painter.drawRoundedRect(
                    base.adjusted(-spread, -spread, spread, spread),
                    radius + spread,
                    radius + spread,
                )

        def _paint_surface(self, painter, rect, radius):
            fill = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
            fill.setColorAt(0.0, QtGui.QColor(30, 35, 46, 242))
            fill.setColorAt(1.0, QtGui.QColor(15, 18, 26, 246))
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(fill))
            painter.drawRoundedRect(rect, radius, radius)

            # Hairline edge, brighter along the top, for a glassy finish.
            edge = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
            edge.setColorAt(0.0, QtGui.QColor(255, 255, 255, 48))
            edge.setColorAt(0.5, QtGui.QColor(255, 255, 255, 18))
            edge.setColorAt(1.0, QtGui.QColor(255, 255, 255, 12))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QBrush(edge), 1.0))
            inner = rect.adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(inner, radius - 0.5, radius - 0.5)

        def _paint_brand(self, painter, rect):
            label, color = PILL_STATUS_STYLES.get(self._status_key, PILL_STATUS_STYLES["ready"])
            center_y = rect.center().y()

            dot_center = QtCore.QPointF(rect.left() + self.PAD + 7.0, center_y)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_with_alpha(color, 48))
            painter.drawEllipse(dot_center, 8.0, 8.0)
            painter.setBrush(color)
            painter.drawEllipse(dot_center, 4.0, 4.0)

            text_left = dot_center.x() + 16.0
            available = max(
                0.0,
                min(
                    rect.left() + self.PAD + self.BRAND_W - text_left,
                    rect.right() - self.PAD - text_left,
                ),
            )
            title_metrics = QtGui.QFontMetricsF(self._title_font)
            status_metrics = QtGui.QFontMetricsF(self._status_font)
            block_h = title_metrics.height() + status_metrics.height()
            top = center_y - block_h / 2.0

            painter.setFont(self._title_font)
            painter.setPen(PILL_TEXT)
            painter.drawText(
                QtCore.QRectF(text_left, top, available, title_metrics.height()),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                "OptiKinesis",
            )

            status_text = self._message or label
            status_color = PILL_TEXT if self._message else _mix_colors(PILL_TEXT_MUTED, color, 0.45)
            painter.setFont(self._status_font)
            painter.setPen(status_color)
            painter.drawText(
                QtCore.QRectF(text_left, top + title_metrics.height(), available, status_metrics.height()),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                status_metrics.elidedText(status_text, QtCore.Qt.ElideRight, available),
            )

        def _paint_divider(self, painter, rect):
            reveal = self.content_reveal
            if reveal <= 0.0:
                return
            x = rect.left() + self.PAD + self.BRAND_W + self.DIVIDER_GAP + 0.5
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, int(28 * reveal)), 1.0))
            painter.drawLine(
                QtCore.QPointF(x, rect.top() + 20.0),
                QtCore.QPointF(x, rect.bottom() - 20.0),
            )

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
                # No activateWindow(): overlay windows must never take the
                # foreground, or typing and "Read this page" target OptiKinesis.
                self._collapse_timer.stop()
            self._sync_active_states()

        # ─── VOICE INPUT ─────────────────────────────────────────────────

        def toggle_voice(self):
            if self.voice_enabled:
                return  # A voice prompt is already open.
            self.voice_enabled = True
            self._sync_active_states()
            try:
                text, ok = QtWidgets.QInputDialog.getText(
                    self, "Voice Command", "Enter voice command text:", QtWidgets.QLineEdit.Normal, ""
                )
                text = text.strip()
                if not ok or not text:
                    self.show_status_message("Voice command cancelled")
                    return
                if _voice_executor is None:
                    self.show_status_message(f"Entered: {text}")
                    return

                result = _voice_executor.process_command(text)
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
                self.show_status_message(result.get("message", result.get("status", "Done")))
            finally:
                self.voice_enabled = False
                self._sync_active_states()


    # ─── 5-POINT HEAD CALIBRATION SCREEN ─────────────────────────────────

    class CalibrationBridge(QtCore.QObject):
        """Delivers cancel requests from the hotkey thread to the Qt thread."""

        cancel_requested = QtCore.pyqtSignal()

        def __init__(self, overlay):
            super().__init__()
            self.cancel_requested.connect(overlay.cancel, QtCore.Qt.QueuedConnection)


    class HeadCalibrationOverlay(QtWidgets.QWidget):
        """Full-screen 5-point head calibration.

        Shows one target at a time: the center, then the four corners.  The
        user turns their head comfortably toward the dot and holds still; each
        point is captured automatically once the head is steady, so no blinks
        are needed.  Cursor movement and blink clicks are suspended while it
        runs.  A caregiver can cancel with Esc or F12.
        """

        TICK_MS = 16
        DONE_HOLD_S = 1.1
        RING_RADIUS = 34.0
        AMBER = QtGui.QColor(251, 191, 36)
        GREEN = QtGui.QColor(52, 211, 153)

        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
                | QtCore.Qt.Tool
                | QtCore.Qt.WindowDoesNotAcceptFocus
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            self.setGeometry(0, 0, SCREEN_W, SCREEN_H)

            self.session = None
            self._hidden_widgets = []
            self._done_since = None
            self._last_state = None
            self._last_target_rect = QtCore.QRect()
            self._ticks = 0

            self._title_font = _pill_font(30, QtGui.QFont.DemiBold)
            self._body_font = _pill_font(18)
            self._step_font = _pill_font(15, QtGui.QFont.DemiBold)
            self._status_font = _pill_font(16, QtGui.QFont.DemiBold)
            self._hint_font = _pill_font(13)

            self._timer = QtCore.QTimer(self)
            self._timer.setTimerType(QtCore.Qt.PreciseTimer)
            self._timer.timeout.connect(self._tick)
            self.hide()

        @property
        def running(self):
            return self.session is not None

        # ─── lifecycle ───────────────────────────────────────────────────

        def begin(self):
            global _calibration_running
            if self.running:
                return True
            if _pose_provider is None:
                return False
            self.session = CalibrationSession()
            self.session.start(time.monotonic())
            self._done_since = None
            self._last_state = None
            self._hide_other_overlays()
            _calibration_running = True
            if _calibration_start is not None:
                try:
                    _calibration_start()
                except Exception as exc:
                    logger.warning("Calibration start hook failed: %s", exc)
            self.show()
            self.raise_()
            self._timer.start(self.TICK_MS)
            return True

        def cancel(self):
            if self.session is not None:
                self.session.cancel("Calibration cancelled.")

        def _finish(self):
            global _calibration_running
            session = self.session
            self._timer.stop()
            self.session = None
            self.hide()
            _calibration_running = False
            self._restore_other_overlays()

            model = None
            if session is not None and session.phase == CalibrationSession.DONE:
                model = session.result
            saved = False
            if _calibration_finish is not None:
                try:
                    saved = bool(_calibration_finish(model))
                except Exception as exc:
                    logger.error("Applying calibration failed: %s", exc)
            if model is not None:
                message = (
                    "Calibration saved. Press F9 any time to re-center."
                    if saved
                    else "Calibration applied for this session but could not be saved."
                )
            else:
                reason = session.error if session is not None and session.error else "Calibration stopped."
                message = f"{reason} Previous settings kept."
            dispatch_notification(message)

        def _hide_other_overlays(self):
            candidates = [floating_bar_widget, mini_camera_widget, blink_debug_overlay_widget]
            candidates.extend(overlay_panel_widgets.values())
            self._hidden_widgets = [w for w in candidates if w is not None and w.isVisible()]
            for widget in self._hidden_widgets:
                widget.hide()
            if cursor_overlay_widget is not None:
                cursor_overlay_widget.hide()

        def _restore_other_overlays(self):
            for widget in self._hidden_widgets:
                widget.show()
            self._hidden_widgets = []

        # ─── animation tick ──────────────────────────────────────────────

        def _tick(self):
            session = self.session
            if session is None:
                self._timer.stop()
                return
            try:
                now = time.monotonic()
                pose = _pose_provider() if _pose_provider is not None else None
                session.update(now, pose)

                if session.phase == CalibrationSession.FAILED:
                    self._finish()
                    return
                if session.phase == CalibrationSession.DONE:
                    if self._done_since is None:
                        self._done_since = now
                    elif now - self._done_since >= self.DONE_HOLD_S:
                        self._finish()
                        return

                # Repaint everything when the text or markers change; otherwise
                # only the small area around the moving, pulsing target.
                state = (session.phase, session.index, session.face_visible, len(session.readings))
                target_rect = self._target_rect(session, now)
                if state != self._last_state:
                    self._last_state = state
                    self.update()
                else:
                    self.update(target_rect.united(self._last_target_rect))
                self._last_target_rect = target_rect

                self._ticks += 1
                if self._ticks % 30 == 0:
                    self.raise_()
            except Exception as exc:
                logger.exception("Calibration screen error: %s", exc)
                if self.session is not None:
                    self.session.cancel("Calibration stopped because of an error.")
                self._finish()

        def _target_point(self, session, now):
            nx, ny = session.target_position(now)
            return QtCore.QPointF(nx * self.width(), ny * self.height())

        def _target_rect(self, session, now):
            point = self._target_point(session, now)
            reach = int(self.RING_RADIUS + 24)
            return QtCore.QRect(int(point.x()) - reach, int(point.y()) - reach, 2 * reach, 2 * reach)

        # ─── painting ────────────────────────────────────────────────────

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
            # Nearly opaque so background content doesn't pull the eye away.
            painter.fillRect(event.rect(), QtGui.QColor(7, 10, 17, 240))
            session = self.session
            if session is not None:
                now = time.monotonic()
                self._paint_text(painter, session)
                self._paint_completed(painter, session)
                self._paint_target(painter, session, now)
            painter.end()

        def _status_lines(self, session):
            if session.phase == CalibrationSession.DONE:
                return "All 5 points captured", "Calibration complete", self.GREEN
            label = POINT_LABELS[session.point_name]
            step = f"Point {session.index + 1} of {session.point_count}  ·  {label}"
            if not session.face_visible:
                return step, "Face not detected. Look toward the screen.", self.AMBER
            if session.phase == CalibrationSession.TRAVEL:
                return step, "Follow the dot", PILL_ACCENT
            return step, "Hold still…", PILL_ACCENT

        def _paint_text(self, painter, session):
            width = float(self.width())
            # Keep the 180 px text block clear of the center target on short screens.
            top = min(self.height() * 0.22, self.height() * 0.5 - self.RING_RADIUS - 24 - 180)
            center = QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter

            painter.setFont(self._title_font)
            painter.setPen(PILL_TEXT)
            painter.drawText(QtCore.QRectF(0, top, width, 44), center, "Head calibration")

            painter.setFont(self._body_font)
            painter.setPen(QtGui.QColor(203, 213, 225))
            painter.drawText(
                QtCore.QRectF(0, top + 50, width, 28),
                center,
                "Turn your head comfortably toward the dot and hold still.",
            )

            step, status, color = self._status_lines(session)
            painter.setFont(self._step_font)
            painter.setPen(PILL_TEXT_MUTED)
            painter.drawText(QtCore.QRectF(0, top + 92, width, 24), center, step)

            painter.setFont(self._status_font)
            painter.setPen(color)
            painter.drawText(QtCore.QRectF(0, top + 122, width, 26), center, status)

            painter.setFont(self._hint_font)
            painter.setPen(_with_alpha(PILL_TEXT_MUTED, 190))
            painter.drawText(
                QtCore.QRectF(0, top + 158, width, 22),
                center,
                "Caregiver: press Esc or F12 to cancel",
            )

        def _paint_completed(self, painter, session):
            painter.setPen(QtCore.Qt.NoPen)
            for name in session.completed_points():
                nx, ny = session.targets[name]
                point = QtCore.QPointF(nx * self.width(), ny * self.height())
                painter.setBrush(_with_alpha(self.GREEN, 50))
                painter.drawEllipse(point, 12.0, 12.0)
                painter.setBrush(self.GREEN)
                painter.drawEllipse(point, 6.0, 6.0)

        def _paint_target(self, painter, session, now):
            point = self._target_point(session, now)
            if session.phase == CalibrationSession.DONE:
                tone = self.GREEN
            elif not session.face_visible:
                tone = self.AMBER
            else:
                tone = PILL_ACCENT

            pulse = 0.5 + 0.5 * math.sin(now * 4.2)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_with_alpha(tone, 36 + 34 * pulse))
            halo = 16.0 + 5.0 * pulse
            painter.drawEllipse(point, halo, halo)

            if session.phase != CalibrationSession.TRAVEL:
                radius = self.RING_RADIUS
                ring = QtCore.QRectF(point.x() - radius, point.y() - radius, 2 * radius, 2 * radius)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 48), 4.0))
                painter.drawEllipse(ring)
                progress = session.progress
                if progress > 0.0:
                    pen = QtGui.QPen(tone, 4.0)
                    pen.setCapStyle(QtCore.Qt.RoundCap)
                    painter.setPen(pen)
                    painter.drawArc(ring, 90 * 16, -int(round(360 * 16 * progress)))

            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255))
            painter.drawEllipse(point, 7.0, 7.0)
            painter.setBrush(tone)
            painter.drawEllipse(point, 3.0, 3.0)


    # ─── WEB PANEL: ANSWER CARDS + CLEAN READER (TinyFish) ───────────────

    WEB_TEXT_SOFT = QtGui.QColor(203, 213, 225)
    WEB_AMBER = QtGui.QColor(251, 191, 36)
    READER_FONT_MIN = 18
    READER_FONT_MAX = 40
    READER_FONT_STEP = 3

    def _wrap_text_lines(text, metrics, width, max_lines):
        """Greedy word wrap into at most max_lines; elides when text is cut off."""
        words = (text or "").split()
        lines = []
        current = ""
        index = 0
        while index < len(words):
            candidate = f"{current} {words[index]}" if current else words[index]
            if metrics.horizontalAdvance(candidate) <= width or not current:
                current = candidate
                index += 1
            else:
                lines.append(current)
                current = ""
                if len(lines) == max_lines:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)
        if not lines:
            return []
        if index < len(words):
            lines[-1] = " ".join([lines[-1]] + words[index:])
        return [metrics.elidedText(line, QtCore.Qt.ElideRight, int(width)) for line in lines]


    class _FlashMixin:
        """Hover tracking and a short glow after each press (blink feedback)."""

        def _init_feedback(self):
            self._hovered = False
            self._flash_level = 0.0
            self._flash_anim = QtCore.QVariantAnimation(self)
            self._flash_anim.setDuration(480)
            self._flash_anim.setStartValue(1.0)
            self._flash_anim.setEndValue(0.0)
            self._flash_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            self._flash_anim.valueChanged.connect(self._on_flash)
            self.clicked.connect(lambda _checked=False: self._flash_anim.start())

        def _on_flash(self, value):
            self._flash_level = float(value)
            self.update()

        def enterEvent(self, event):
            self._hovered = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event):
            self._hovered = False
            self.update()
            super().leaveEvent(event)

        def _paint_card(self, painter, rect, radius, tone):
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 26 if self._hovered else 13))
            painter.drawRoundedRect(rect, radius, radius)
            if self._flash_level > 0.0:
                painter.setBrush(_with_alpha(tone, 130 * self._flash_level))
                painter.drawRoundedRect(rect, radius, radius)
            painter.setBrush(QtCore.Qt.NoBrush)
            if self._hovered:
                painter.setPen(QtGui.QPen(_with_alpha(tone, 170), 1.6))
            else:
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1.0))
            painter.drawRoundedRect(rect, radius, radius)


    class WebButton(_FlashMixin, QtWidgets.QAbstractButton):
        """Large painted button: icon + title (+ optional description line)."""

        def __init__(self, icon_name, label, description="", parent=None, min_height=72):
            super().__init__(parent)
            self.icon_name = icon_name
            self.description = description
            self.setText(label)
            self.setAccessibleName(label)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.setMinimumHeight(min_height)
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self._title_font = _pill_font(30 if description else 16, QtGui.QFont.DemiBold)
            self._desc_font = _pill_font(19)
            self._init_feedback()

        def sizeHint(self):
            return QtCore.QSize(160, self.minimumHeight())

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
            if not self.isEnabled():
                painter.setOpacity(0.35)
            rect = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            self._paint_card(painter, rect, 18.0, PILL_ACCENT)
            icon_color = PILL_ACCENT if self._hovered else PILL_ICON

            if self.description:
                icon_rect = QtCore.QRectF(40, rect.center().y() - 30, 60, 60)
                if self.icon_name:
                    _draw_pill_icon(painter, self.icon_name, icon_rect, icon_color)
                text_left = 132.0
                width = rect.width() - text_left - 24
                painter.setFont(self._title_font)
                painter.setPen(PILL_TEXT)
                title_h = QtGui.QFontMetricsF(self._title_font).height()
                desc_metrics = QtGui.QFontMetrics(self._desc_font)
                lines = _wrap_text_lines(self.description, desc_metrics, width, 2)
                block = title_h + 10 + len(lines) * desc_metrics.height()
                top = rect.center().y() - block / 2
                painter.drawText(QtCore.QRectF(text_left, top, width, title_h),
                                 QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.text())
                painter.setFont(self._desc_font)
                painter.setPen(WEB_TEXT_SOFT)
                y = top + title_h + 10
                for line in lines:
                    painter.drawText(QtCore.QRectF(text_left, y, width, desc_metrics.height()),
                                     QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, line)
                    y += desc_metrics.height()
            elif self.icon_name:
                icon_rect = QtCore.QRectF(rect.center().x() - 13, rect.center().y() - 24, 26, 26)
                _draw_pill_icon(painter, self.icon_name, icon_rect, icon_color)
                painter.setFont(self._title_font)
                painter.setPen(PILL_TEXT)
                painter.drawText(QtCore.QRectF(rect.left(), rect.center().y() + 6, rect.width(), 24),
                                 QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, self.text())
            else:
                font = _pill_font(24, QtGui.QFont.DemiBold)
                painter.setFont(font)
                painter.setPen(PILL_TEXT)
                painter.drawText(rect, QtCore.Qt.AlignCenter, self.text())
            painter.end()


    class AnswerCard(_FlashMixin, QtWidgets.QAbstractButton):
        """One search result as a large card; a blink on it opens the page in the reader."""

        def __init__(self, result, parent=None):
            super().__init__(parent)
            self.result = result
            self.setText(result.title)
            self.setAccessibleName(result.title)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.setMinimumHeight(150)
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self._title_font = _pill_font(19, QtGui.QFont.DemiBold)
            self._site_font = _pill_font(14, QtGui.QFont.DemiBold)
            self._snippet_font = _pill_font(15)
            self._init_feedback()

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
            rect = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            self._paint_card(painter, rect, 18.0, PILL_ACCENT)

            left = rect.left() + 22
            width = rect.width() - 44
            y = rect.top() + 16
            site = self.result.site_name + (f"  ·  {self.result.date}" if self.result.date else "")
            site_metrics = QtGui.QFontMetrics(self._site_font)
            painter.setFont(self._site_font)
            painter.setPen(PILL_ACCENT)
            painter.drawText(QtCore.QRectF(left, y, width, site_metrics.height()),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             site_metrics.elidedText(site, QtCore.Qt.ElideRight, int(width)))
            y += site_metrics.height() + 6

            title_metrics = QtGui.QFontMetrics(self._title_font)
            painter.setFont(self._title_font)
            painter.setPen(PILL_TEXT)
            for line in _wrap_text_lines(self.result.title, title_metrics, width, 2):
                painter.drawText(QtCore.QRectF(left, y, width, title_metrics.height()),
                                 QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, line)
                y += title_metrics.height()
            y += 6

            snippet_metrics = QtGui.QFontMetrics(self._snippet_font)
            room = int((rect.bottom() - 12 - y) // snippet_metrics.height())
            if room > 0 and self.result.snippet:
                painter.setFont(self._snippet_font)
                painter.setPen(WEB_TEXT_SOFT)
                for line in _wrap_text_lines(self.result.snippet, snippet_metrics, width, min(3, room)):
                    painter.drawText(QtCore.QRectF(left, y, width, snippet_metrics.height()),
                                     QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, line)
                    y += snippet_metrics.height()
            painter.end()


    class WebTaskBridge(QtCore.QObject):
        """Delivers background web results to the Qt thread."""

        done = QtCore.pyqtSignal(int, str, object, object)
        answers_requested = QtCore.pyqtSignal(str)
        read_page_requested = QtCore.pyqtSignal()


    class WebPanel(OverlayPanelBase):
        """Answer cards and a large-print clean reader, powered by TinyFish."""

        def __init__(self):
            super().__init__("WEB MODULE", width=min(1280, int(SCREEN_W * 0.8)),
                             height=min(880, int(SCREEN_H * 0.84)))
            # Reading panel: keep text behind the panel from showing through.
            # Set on the shell itself so it outranks the shared panel style.
            self.shell.setStyleSheet("""
                QFrame#panelShell {
                    background-color: rgba(10, 16, 26, 252);
                    border: 6px solid #2f4b69;
                    border-radius: 18px;
                }
            """)
            self._token = 0
            self._return_page = None
            self._reader_return_page = None
            self._last_query = ""
            self._article = None
            self._cards = []

            self.bridge = WebTaskBridge()
            self.bridge.done.connect(self._on_task_done, QtCore.Qt.QueuedConnection)
            self.bridge.answers_requested.connect(self.find_answers, QtCore.Qt.QueuedConnection)
            self.bridge.read_page_requested.connect(self.read_current_page, QtCore.Qt.QueuedConnection)

            self.stack = QtWidgets.QStackedWidget(self.body)
            self.body_layout.addWidget(self.stack, 1)
            self.status = QtWidgets.QLabel("", self.body)
            self.status.setWordWrap(True)
            self.status.setStyleSheet("font-size: 17px; padding: 2px 6px;")
            self.body_layout.addWidget(self.status)

            self.home_page = self._build_home()
            self.ask_page = self._build_ask()
            self.busy_page = self._build_busy()
            self.answers_page = self._build_answers()
            self.reader_page = self._build_reader()
            for page in (self.home_page, self.ask_page, self.busy_page,
                         self.answers_page, self.reader_page):
                # Hidden views must not force a minimum height, so the panel
                # can shrink to sit above the keyboard while a question is typed.
                page.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
                self.stack.addWidget(page)
            self.layout().setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)
            self._full_size = QtCore.QSize(self.width(), self.height())
            self._asking = False

            # Remember the last app used outside OptiKinesis, so "Read this
            # page" can find the browser even if OptiKinesis is in front.
            self._last_external = None
            self._own_pid = os.getpid()
            self._foreground_timer = QtCore.QTimer(self)
            self._foreground_timer.timeout.connect(self._track_foreground)
            self._foreground_timer.start(400)
            self._show_home()

        # ─── pages ───────────────────────────────────────────────────────

        def _build_home(self):
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 8)
            layout.setSpacing(18)
            read_btn = WebButton(
                "page", "Read this page",
                "Shows the page open in your browser as large, clean text without ads or pop-ups.",
                min_height=150,
            )
            read_btn.clicked.connect(self.read_current_page)
            find_btn = WebButton(
                "search", "Find answers",
                "Type or say a question and see the best results as big cards.",
                min_height=150,
            )
            find_btn.clicked.connect(lambda _checked=False: self.find_answers(None))
            layout.addWidget(read_btn, 1)
            layout.addWidget(find_btn, 1)
            self.home_note = QtWidgets.QLabel("", page)
            self.home_note.setWordWrap(True)
            self.home_note.setStyleSheet("font-size: 15px; color: #94a0b2; padding: 4px;")
            layout.addWidget(self.home_note)
            return page

        def _build_ask(self):
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.setContentsMargins(16, 10, 16, 6)
            layout.setSpacing(12)
            prompt = QtWidgets.QLabel("What would you like to find?", page)
            prompt.setStyleSheet("font-size: 26px; font-weight: 600; color: #f1f5f9;")
            layout.addWidget(prompt)
            self.question_display = QtWidgets.QLabel(page)
            self.question_display.setTextFormat(QtCore.Qt.RichText)
            self.question_display.setWordWrap(True)
            self.question_display.setMinimumHeight(96)
            self.question_display.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            self.question_display.setStyleSheet("""
                QLabel {
                    background: rgba(9, 13, 21, 245);
                    color: #f1f5f9;
                    border: 2px solid #38bdf8;
                    border-radius: 14px;
                    font-size: 30px;
                    padding: 10px 18px;
                }
            """)
            layout.addWidget(self.question_display, 1)
            hint = QtWidgets.QLabel(
                "Type with the keyboard below. Pick a suggested word to finish it in one blink.", page
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 16px; color: #94a0b2;")
            layout.addWidget(hint)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(14)
            cancel = WebButton("back", "Cancel", min_height=80)
            cancel.clicked.connect(self._cancel_question)
            search = WebButton("search", "Search", min_height=80)
            search.clicked.connect(self._search_typed_question)
            row.addWidget(cancel, 1)
            row.addWidget(search, 2)
            layout.addLayout(row)
            return page

        def _build_busy(self):
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.addStretch(1)
            self.busy_label = QtWidgets.QLabel("", page)
            self.busy_label.setAlignment(QtCore.Qt.AlignCenter)
            self.busy_label.setWordWrap(True)
            self.busy_label.setStyleSheet("font-size: 26px; font-weight: 600; color: #f1f5f9;")
            layout.addWidget(self.busy_label)
            self._busy_dots = 0
            self._busy_text = ""
            self._busy_timer = QtCore.QTimer(self)
            self._busy_timer.timeout.connect(self._tick_busy)
            row = QtWidgets.QHBoxLayout()
            row.addStretch(1)
            cancel = WebButton("back", "Cancel", min_height=76)
            cancel.setFixedWidth(200)
            cancel.clicked.connect(self._cancel_task)
            row.addWidget(cancel)
            row.addStretch(1)
            layout.addSpacing(24)
            layout.addLayout(row)
            layout.addStretch(1)
            return page

        def _build_answers(self):
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.setContentsMargins(8, 8, 8, 0)
            layout.setSpacing(12)
            top = QtWidgets.QHBoxLayout()
            back = WebButton("back", "Back", min_height=76)
            back.setFixedWidth(150)
            back.clicked.connect(self._show_home)
            top.addWidget(back)
            self.answers_title = QtWidgets.QLabel("", page)
            self.answers_title.setStyleSheet("font-size: 22px; font-weight: 600; color: #f1f5f9; padding-left: 10px;")
            top.addWidget(self.answers_title, 1)
            google = WebButton("external", "Open in Google", min_height=76)
            google.setFixedWidth(210)
            google.clicked.connect(self._open_query_in_google)
            top.addWidget(google)
            layout.addLayout(top)
            self.cards_grid = QtWidgets.QGridLayout()
            self.cards_grid.setSpacing(14)
            layout.addLayout(self.cards_grid, 1)
            return page

        def _build_reader(self):
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.setContentsMargins(8, 8, 8, 0)
            layout.setSpacing(10)
            top = QtWidgets.QHBoxLayout()
            back = WebButton("back", "Back", min_height=76)
            back.setFixedWidth(150)
            back.clicked.connect(self._reader_back)
            top.addWidget(back)
            heading = QtWidgets.QVBoxLayout()
            self.reader_title = QtWidgets.QLabel("", page)
            self.reader_title.setStyleSheet("font-size: 22px; font-weight: 600; color: #f1f5f9; padding-left: 10px;")
            self.reader_meta = QtWidgets.QLabel("", page)
            self.reader_meta.setStyleSheet("font-size: 15px; color: #38bdf8; padding-left: 10px;")
            heading.addWidget(self.reader_title)
            heading.addWidget(self.reader_meta)
            top.addLayout(heading, 1)
            layout.addLayout(top)

            main = QtWidgets.QHBoxLayout()
            main.setSpacing(12)
            self.reader_text = QtWidgets.QTextBrowser(page)
            self.reader_text.setOpenLinks(False)
            self.reader_text.setOpenExternalLinks(False)
            self.reader_text.setFocusPolicy(QtCore.Qt.NoFocus)
            self.reader_text.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.reader_text.setStyleSheet("""
                QTextBrowser {
                    background: rgba(9, 13, 21, 245);
                    color: #e6edf5;
                    border: 1px solid #253a52;
                    border-radius: 14px;
                }
                QScrollBar:vertical { width: 14px; background: transparent; margin: 8px 2px; }
                QScrollBar::handle:vertical { background: #2f4b69; border-radius: 6px; min-height: 40px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            """)
            main.addWidget(self.reader_text, 1)

            side = QtWidgets.QVBoxLayout()
            side.setSpacing(10)
            self.up_btn = WebButton("up", "Up", min_height=90)
            self.up_btn.clicked.connect(lambda _checked=False: self._scroll_reader(-1))
            self.down_btn = WebButton("down", "Down", min_height=90)
            self.down_btn.clicked.connect(lambda _checked=False: self._scroll_reader(1))
            bigger = WebButton("", "A+", min_height=70)
            bigger.clicked.connect(lambda _checked=False: self._change_font(READER_FONT_STEP))
            smaller = WebButton("", "A−", min_height=70)
            smaller.clicked.connect(lambda _checked=False: self._change_font(-READER_FONT_STEP))
            browser = WebButton("external", "Browser", min_height=90)
            browser.clicked.connect(self._open_article_in_browser)
            for widget, stretch in ((self.up_btn, 3), (self.down_btn, 3), (bigger, 2),
                                    (smaller, 2), (browser, 2)):
                widget.setFixedWidth(150)
                side.addWidget(widget, stretch)
            main.addLayout(side)
            layout.addLayout(main, 1)

            bar = self.reader_text.verticalScrollBar()
            self._scroll_anim = QtCore.QPropertyAnimation(bar, b"value", self)
            self._scroll_anim.setDuration(280)
            self._scroll_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            bar.valueChanged.connect(self._update_scroll_buttons)
            bar.rangeChanged.connect(lambda *_args: self._update_scroll_buttons())
            return page

        # ─── navigation ──────────────────────────────────────────────────

        def _set_status(self, text, warning=False):
            color = "#fbbf24" if warning else "#9fb6cf"
            self.status.setStyleSheet(f"font-size: 17px; padding: 2px 6px; color: {color};")
            self.status.setText(text)

        def _show_home(self):
            self._token += 1  # drop any late result
            self._busy_timer.stop()
            if _web_client is None:
                self.home_note.setStyleSheet("font-size: 15px; color: #fbbf24; padding: 4px;")
                self.home_note.setText(MISSING_KEY_MESSAGE)
            else:
                self.home_note.setStyleSheet("font-size: 15px; color: #94a0b2; padding: 4px;")
                self.home_note.setText("Searches and pages are loaded through TinyFish.")
            self.stack.setCurrentWidget(self.home_page)

        def open(self):
            """Show this panel on its own, like the pill buttons do."""
            for panel in overlay_panel_widgets.values():
                if panel is not self:
                    panel.hide()
            self.show()
            self.raise_()

        def _reader_back(self):
            if self._reader_return_page is self.answers_page and self._cards:
                self.stack.setCurrentWidget(self.answers_page)
            else:
                self._show_home()

        def _cancel_task(self):
            self._token += 1
            self._busy_timer.stop()
            self.stack.setCurrentWidget(self._return_page or self.home_page)
            self._set_status("Cancelled.")

        def _tick_busy(self):
            self._busy_dots = (self._busy_dots + 1) % 4
            self.busy_label.setText(self._busy_text + "." * self._busy_dots)

        # ─── background tasks ────────────────────────────────────────────

        def _run(self, kind, busy_text, func):
            self._token += 1
            token = self._token
            current = self.stack.currentWidget()
            self._return_page = current if current is not self.busy_page else self.home_page
            self._busy_text = busy_text
            self._busy_dots = 0
            self.busy_label.setText(busy_text)
            self._busy_timer.start(450)
            self._set_status("")
            self.stack.setCurrentWidget(self.busy_page)
            bridge = self.bridge

            def worker():
                result, error = None, None
                try:
                    result = func()
                except WebAssistError as exc:
                    error = exc.message
                except Exception:
                    logger.exception("Web task %s failed", kind)
                    error = "Something went wrong. Please try again."
                bridge.done.emit(token, kind, result, error)

            threading.Thread(target=worker, name=f"web-{kind}", daemon=True).start()

        def _on_task_done(self, token, kind, result, error):
            if token != self._token:
                return  # cancelled or superseded
            self._busy_timer.stop()
            try:
                if error:
                    self.stack.setCurrentWidget(self._return_page or self.home_page)
                    self._set_status(error, warning=True)
                elif kind == "search":
                    self._show_answers(result)
                elif kind == "read":
                    self._show_article(result)
            except Exception:
                logger.exception("Showing web result failed")
                self.stack.setCurrentWidget(self.home_page)
                self._set_status("Something went wrong showing that result.", warning=True)

        # ─── answer cards ────────────────────────────────────────────────

        def find_answers(self, query=None):
            self.open()
            if _web_client is None:
                self._show_home()
                self._set_status(MISSING_KEY_MESSAGE, warning=True)
                return
            if not query:
                self._start_question()
                return
            self._end_question()
            self._last_query = query.strip()
            client = _web_client
            self._run("search", f"Finding answers for “{self._last_query}”",
                      lambda: client.search(self._last_query))

        # ─── typing a question with the on-screen keyboard ───────────────

        def _start_question(self):
            keyboard = keyboard_panel_widget
            if keyboard is None:
                self._set_status("The on-screen keyboard isn't available.", warning=True)
                return
            self._asking = True
            self._set_status("")
            self.stack.setCurrentWidget(self.ask_page)
            self._show_question("", 0)
            self._fit_above_keyboard(True)
            keyboard.start_compose(
                on_change=self._show_question,
                on_submit=self._submit_question,
                on_cancel=self._question_cancelled,
            )

        def _show_question(self, text, cursor_pos):
            caret = '<span style="color:#38bdf8;">│</span>'
            if not text:
                self.question_display.setText(
                    caret + '<span style="color:#64748b;"> Start typing…</span>'
                )
                return
            before = html.escape(text[:cursor_pos])
            after = html.escape(text[cursor_pos:])
            self.question_display.setText(before + caret + after)

        def _submit_question(self, text):
            question = (text or "").strip()
            if not question:
                self._set_status("Type a question first.", warning=True)
                return
            self.find_answers(question)

        def _search_typed_question(self):
            keyboard = keyboard_panel_widget
            self._submit_question(keyboard.text_value() if keyboard is not None else "")

        def _cancel_question(self):
            self._end_question()
            self._show_home()

        def _question_cancelled(self):
            """The keyboard was closed while typing the question."""
            if self._asking:
                self._asking = False
                self._fit_above_keyboard(False)
                if self.stack.currentWidget() is self.ask_page:
                    self._show_home()

        def _end_question(self):
            if not self._asking:
                return
            self._asking = False
            keyboard = keyboard_panel_widget
            if keyboard is not None:
                keyboard.stop_compose()
                keyboard.hide()
            self._fit_above_keyboard(False)

        def _fit_above_keyboard(self, fit):
            """Shrink the panel to sit above the keyboard while typing, or restore it."""
            height = self._full_size.height()
            keyboard = keyboard_panel_widget
            if fit and keyboard is not None:
                room = keyboard.geometry().top() - PANEL_TOP_OFFSET - 14
                height = max(300, min(height, room))
            if self.height() != height:
                self.setFixedSize(self._full_size.width(), height)

        def hideEvent(self, event):
            self._end_question()
            super().hideEvent(event)

        def _show_answers(self, answers):
            for card in self._cards:
                self.cards_grid.removeWidget(card)
                card.deleteLater()
            self._cards = []
            self.answers_title.setText(f"Answers for “{answers.query}”")
            if not answers.results:
                self.stack.setCurrentWidget(self.home_page)
                self._set_status("No results found. Try different words.", warning=True)
                return
            for index, result in enumerate(answers.results):
                card = AnswerCard(result)
                card.clicked.connect(lambda _checked=False, r=result: self.read_url(r.url))
                self.cards_grid.addWidget(card, index // 2, index % 2)
                self._cards.append(card)
            self.stack.setCurrentWidget(self.answers_page)
            self._set_status("Look at a card and blink to read it.")

        def _open_query_in_google(self):
            if self._last_query:
                webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(self._last_query))

        # ─── clean reader ────────────────────────────────────────────────

        def read_current_page(self):
            self.open()
            if _web_client is None:
                self._show_home()
                self._set_status(MISSING_KEY_MESSAGE, warning=True)
                return
            client = _web_client
            self._reader_return_page = self.home_page
            last_external = self._last_external

            def task():
                time.sleep(0.3)  # let focus return to the browser after any dialog
                return client.fetch(capture_browser_url(last_external=last_external))

            self._run("read", "Opening the page in the reader", task)

        def _track_foreground(self):
            try:
                info = foreground_window()
            except Exception:
                return
            if info is not None and info.pid != self._own_pid and info.name:
                self._last_external = info

        def read_url(self, url):
            if _web_client is None:
                return
            client = _web_client
            self._reader_return_page = self.stack.currentWidget()
            self._run("read", "Opening the page in the reader", lambda: client.fetch(url))

        def _show_article(self, article):
            self._article = article
            metrics = QtGui.QFontMetrics(_pill_font(22, QtGui.QFont.DemiBold))
            self.reader_title.setText(metrics.elidedText(article.title, QtCore.Qt.ElideRight, 820))
            self.reader_meta.setText(f"{article.site_name}  ·  {article.reading_minutes} min read")
            self.reader_text.setMarkdown(article.text)
            self._apply_reader_format()
            self.reader_text.verticalScrollBar().setValue(0)
            self.stack.setCurrentWidget(self.reader_page)
            self._update_scroll_buttons()
            self._set_status("Use Up and Down to read. A+ and A− change the text size.")

        def _reader_font_px(self):
            try:
                size = int(_settings.get("reader_font_px", 24))
            except (TypeError, ValueError):
                size = 24
            return max(READER_FONT_MIN, min(READER_FONT_MAX, size))

        def _apply_reader_format(self):
            size = self._reader_font_px()
            document = self.reader_text.document()
            font = QtGui.QFont(PILL_FONT_FAMILY)
            font.setPixelSize(size)
            document.setDefaultFont(font)
            document.setDocumentMargin(36)
            cursor = QtGui.QTextCursor(document)
            cursor.select(QtGui.QTextCursor.Document)
            block = QtGui.QTextBlockFormat()
            block.setLineHeight(150, QtGui.QTextBlockFormat.ProportionalHeight)
            block.setBottomMargin(size * 0.6)
            cursor.mergeBlockFormat(block)

        def _change_font(self, delta):
            size = max(READER_FONT_MIN, min(READER_FONT_MAX, self._reader_font_px() + delta))
            _settings["reader_font_px"] = size
            _perform_action("save_settings")
            bar = self.reader_text.verticalScrollBar()
            fraction = bar.value() / bar.maximum() if bar.maximum() else 0.0
            self._apply_reader_format()
            bar.setValue(int(fraction * bar.maximum()))
            self._set_status(f"Text size {size}.")

        def _scroll_reader(self, direction):
            bar = self.reader_text.verticalScrollBar()
            step = int(self.reader_text.viewport().height() * 0.85)
            start = bar.value()
            if self._scroll_anim.state() == QtCore.QAbstractAnimation.Running:
                start = int(self._scroll_anim.endValue())
                self._scroll_anim.stop()
            target = max(bar.minimum(), min(bar.maximum(), start + direction * step))
            self._scroll_anim.setStartValue(bar.value())
            self._scroll_anim.setEndValue(target)
            self._scroll_anim.start()

        def _update_scroll_buttons(self, *_args):
            bar = self.reader_text.verticalScrollBar()
            self.up_btn.setEnabled(bar.value() > bar.minimum())
            self.down_btn.setEnabled(bar.value() < bar.maximum())

        def _open_article_in_browser(self):
            if self._article is not None:
                webbrowser.open(self._article.final_url)


    def request_answers(query):
        """Voice hook: show answer cards for a query. False means fall back to Google."""
        if web_panel_widget is None or _web_client is None:
            return False
        web_panel_widget.bridge.answers_requested.emit(str(query))
        return True


    def request_read_page():
        """Voice hook: open the browser's current page in the reader."""
        if web_panel_widget is None or _web_client is None:
            return False
        web_panel_widget.bridge.read_page_requested.emit()
        return True


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
            hit_widget = self._drop_transparent_hit(hit_widget, global_point)
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

        def _drop_transparent_hit(self, widget, global_point):
            """Ignore hits on an overlay window's clear margin so the OS click falls through."""
            if widget is None:
                return None
            accepts = getattr(widget.window(), "accepts_gaze_point", None)
            if callable(accepts) and not accepts(global_point):
                return None
            return widget

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
            except Exception as exc:
                logger.debug("I-beam cursor detection unavailable: %s", exc)

            # A click in another app moves its text cursor; restart word suggestions.
            if keyboard_panel_widget is not None:
                keyboard_panel_widget.note_external_click()

            try:
                pyautogui.click(x=int(x), y=int(y))
            except pyautogui.FailSafeException:
                _settings["system_paused"] = True
                logger.warning("OS click cancelled by PyAutoGUI failsafe; system paused")
            except Exception as exc:
                logger.warning("OS click failed: %s", exc)
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
    except pyautogui.FailSafeException:
        _settings["system_paused"] = True
        logger.warning("Blink click cancelled by PyAutoGUI failsafe; system paused")
    except Exception as exc:
        logger.warning("Blink click failed: %s", exc)


def launch_overlay():
    """Initialize and launch the PyQt5 overlay application."""
    global gaze_click_bridge, cursor_overlay_widget, blink_debug_overlay_widget
    global keyboard_panel_widget, mini_camera_widget
    global notification_overlay_widget, notification_bridge
    global floating_bar_widget, overlay_panel_widgets
    global calibration_overlay_widget, calibration_bridge, web_panel_widget

    if not PYQT5_AVAILABLE:
        print("ERROR: PyQt5 is required for system-level overlay mode.")
        print("Install with: pip install PyQt5")
        sys.exit(1)

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    qt_app.setQuitOnLastWindowClosed(False)
    if _shutdown_callback is not None:
        qt_app.aboutToQuit.connect(_shutdown_callback)
    gaze_click_bridge = GazeClickBridge()

    notification_overlay_widget = NotificationToastOverlay()
    notification_bridge = NotificationBridge(notification_overlay_widget)

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

    keyboard_panel = KeyboardOverlayPanel()
    keyboard_panel_widget = keyboard_panel
    camera_panel = CameraOverlayPanel()
    emergency_panel = EmergencyOverlayPanel()
    control_panel = ControlOverlayPanel()
    web_panel_widget = WebPanel()

    overlay_panel_widgets = {
        "keyboard": keyboard_panel,
        "camera": camera_panel,
        "web": web_panel_widget,
        "emergency": emergency_panel,
        "control": control_panel,
    }
    if _voice_executor is not None:
        # Spoken searches show answer cards; "read this page" opens the reader.
        _voice_executor.set_web_handlers(search=request_answers, read_page=request_read_page)
    floating_bar = FloatingControlBar(overlay_panel_widgets)
    floating_bar_widget = floating_bar
    floating_bar.show()

    calibration_overlay_widget = HeadCalibrationOverlay()
    calibration_bridge = CalibrationBridge(calibration_overlay_widget)
    if _calibration_needed is not None:
        QtCore.QTimer.singleShot(1500, _maybe_auto_calibrate)

    print("[Overlay] Native floating overlay launched (always-on-top, frameless, transparent).")
    qt_app.exec_()


AUTO_CALIBRATION_WAIT_S = 60.0
AUTO_CALIBRATION_POLL_MS = 500


def _maybe_auto_calibrate(waited_s=0.0):
    """First run: start the 5-point calibration once a face is being tracked."""
    try:
        if _calibration_needed is None or not _calibration_needed():
            return
        if calibration_overlay_widget is None or calibration_overlay_widget.running:
            return
        if _pose_provider is not None and _pose_provider() is not None:
            start_head_calibration()
            return
    except Exception as exc:
        logger.warning("Automatic calibration check failed: %s", exc)
        return
    if waited_s < AUTO_CALIBRATION_WAIT_S:
        next_wait = waited_s + AUTO_CALIBRATION_POLL_MS / 1000.0
        QtCore.QTimer.singleShot(AUTO_CALIBRATION_POLL_MS, lambda: _maybe_auto_calibrate(next_wait))
