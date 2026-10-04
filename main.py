import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import pyautogui
import numpy as np
import math
import threading
from flask import Flask, render_template, Response, request, jsonify
import webbrowser
import time
import urllib.parse
import sys
import os
import json
import atexit
import argparse
import logging
from logging.handlers import RotatingFileHandler
from collections import deque

from blink_detector import DeliberateBlinkDetector


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
LOG_PATH = os.path.join(BASE_DIR, "optikinesis.log")


def configure_logging():
    """Configure one console and one bounded file log for all modules."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(threadName)s: %(message)s"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)


configure_logging()
logger = logging.getLogger("optikinesis")

# --- PyQt5 IMPORTS FOR NATIVE OVERLAY ---
try:
    from PyQt5 import QtWidgets, QtGui, QtCore, QtTest
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False
    print("WARNING: PyQt5 not installed. Native desktop overlay will not be available.")
    print("Install with: pip install PyQt5")

# --- TWILIO IMPORT (Optional) ---
try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

# --- FATIGUE MONITORING SYSTEM ---
from fatigue_monitor import fatigue_monitor

# --- VOICE COMMAND AUTOMATION ---
from voice_commands import voice_executor

app = Flask(__name__)

# --- CONFIGURATION ---
pyautogui.FAILSAFE = True

# Screen settings
SCREEN_W, SCREEN_H = pyautogui.size()
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

# Native overlay placement
TOP_MARGIN = 24
BAR_HEIGHT = 100
PANEL_TOP_OFFSET = TOP_MARGIN + BAR_HEIGHT + 18

# Head-pose tracking settings (accessible speed)
FILTER_LENGTH = 12  # More frames = smoother cursor
YAW_DEGREES = 25    # Degrees left/right to reach screen edge
PITCH_DEGREES = 18  # Degrees up/down to reach screen edge

# Axis inversion flags (correct camera mirroring)
# Set True to invert axis: look LEFT -> cursor LEFT, look RIGHT -> cursor RIGHT
INVERT_X = True    # Invert horizontal (for front-facing camera mirror correction)
INVERT_Y = False   # Invert vertical (usually not needed)

# Blink Settings (tuned for reliable 2-blink click)
BLINK_THRESH = 0.2
BLINK_DURATION_MIN = 0.15  # Minimum blink hold time (150ms)
BLINK_COOLDOWN = 0.25      # Time between blinks
BLINKS_TO_CLICK = 1        # Require 1 blink to trigger click
BLINK_WINDOW = 1.0         # 1.0 second window to complete blink

# Face landmark indices for bounding box (from MonitorTracking.py)
LANDMARKS = {
    "left": 234,
    "right": 454,
    "top": 10,
    "bottom": 152,
    "front": 1,
}

# Configurable settings.  User changes are persisted to settings.json.
DEFAULT_SETTINGS = {
    "emergency_contact": "",
    "cursor_scope": 1.0,
    "blink_sensitivity": BLINK_THRESH,
    "cursor_speed": 0.5,
    "blinks_to_click": BLINKS_TO_CLICK,
    "blink_window": BLINK_WINDOW,
    "lock_delay": 0.5,
    "overlay_enabled": True,
    "mouse_control_enabled": True,
    "system_paused": False,
}


def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as stream:
            stored = json.load(stream)
        if isinstance(stored, dict):
            settings.update({key: stored[key] for key in settings.keys() & stored.keys()})
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not load settings: %s", exc)
    # A safety pause is session state, not a preference.
    settings["system_paused"] = False
    return settings


def save_settings():
    temporary_path = SETTINGS_PATH + ".tmp"
    try:
        persisted = {
            key: SETTINGS[key]
            for key in DEFAULT_SETTINGS
            if key != "system_paused"
        }
        with open(temporary_path, "w", encoding="utf-8") as stream:
            json.dump(persisted, stream, indent=2, sort_keys=True)
        os.replace(temporary_path, SETTINGS_PATH)
        return True
    except OSError as exc:
        logger.error("Could not save settings: %s", exc)
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        return False


SETTINGS = load_settings()


def coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("Expected a boolean value")

# EMA smoothing for cursor position
prev_screen_x = CENTER_X
prev_screen_y = CENTER_Y

# Calibration offsets
calibration_offset_yaw = 0
calibration_offset_pitch = 0
latest_raw_yaw = None
latest_raw_pitch = None
calibration_lock = threading.Lock()

# Ray smoothing buffers
ray_origins = deque(maxlen=FILTER_LENGTH)
ray_directions = deque(maxlen=FILTER_LENGTH)

# Shared mouse target position (thread-safe)
mouse_target = [CENTER_X, CENTER_Y]
mouse_lock = threading.Lock()

# Latest preview frame for native camera overlay
latest_preview_frame = None
latest_preview_lock = threading.Lock()

# UI-only setting for keyboard lock delay indicator
LOCK_DELAY = float(SETTINGS.get("lock_delay", 0.5))

# Debug HUD for blink->click routing
DEBUG_BLINK_HUD = False

# Qt bridge for blink->widget click routing in native overlay mode
gaze_click_bridge = None
cursor_overlay_widget = None
blink_debug_overlay_widget = None

# Lifecycle and face-presence state
shutdown_event = threading.Event()
cleanup_lock = threading.Lock()
worker_threads = []
tracking_state_lock = threading.Lock()
last_face_seen = 0.0
FACE_LOSS_TIMEOUT = 0.5

# --- TWILIO CREDENTIALS (Optional) ---
from dotenv import load_dotenv  # pyright: ignore[reportMissingImports]
load_dotenv()
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")

SMTP_CONFIG = {
    "smtp_server": os.getenv("SMTP_SERVER"),
    "smtp_port": os.getenv("SMTP_PORT"),
    "email": os.getenv("SMTP_EMAIL"),
    "password": os.getenv("SMTP_PASSWORD"),
}
if all(SMTP_CONFIG.values()):
    voice_executor.configure_email(SMTP_CONFIG)
    logger.info("SMTP email integration configured for %s", SMTP_CONFIG["email"])
else:
    logger.info("SMTP email integration is not configured")

# --- DESKTOP OVERLAY MODULE ---
import desktop_overlay
from desktop_overlay import dispatch_blink_click, PYQT5_AVAILABLE

def get_latest_preview_frame():
    with latest_preview_lock:
        return None if latest_preview_frame is None else latest_preview_frame.copy()


def is_face_tracking_active():
    with tracking_state_lock:
        return time.time() - last_face_seen <= FACE_LOSS_TIMEOUT


def mark_face_seen():
    global last_face_seen
    with tracking_state_lock:
        last_face_seen = time.time()


def calibrate_current_pose():
    """Map the most recently observed neutral head pose to screen center."""
    global calibration_offset_yaw, calibration_offset_pitch
    with calibration_lock:
        if latest_raw_yaw is None or latest_raw_pitch is None:
            return False
        calibration_offset_yaw = 180.0 - latest_raw_yaw
        calibration_offset_pitch = 180.0 - latest_raw_pitch
    logger.info(
        "Calibrated center: yaw offset %.2f, pitch offset %.2f",
        calibration_offset_yaw,
        calibration_offset_pitch,
    )
    return True


# --- MOUSE MOVER THREAD ---
def mouse_mover():
    """Continuously move mouse to target position (smoother than direct control in main loop)."""
    while not shutdown_event.is_set():
        if (
            SETTINGS.get("mouse_control_enabled", True)
            and not SETTINGS.get("system_paused", False)
            and is_face_tracking_active()
        ):
            with mouse_lock:
                x, y = mouse_target
            try:
                if not (math.isnan(x) or math.isnan(y)):
                    pyautogui.moveTo(x, y)
            except pyautogui.FailSafeException:
                SETTINGS["system_paused"] = True
                logger.warning("PyAutoGUI failsafe activated; system paused")
            except Exception as exc:
                logger.debug("Mouse movement failed: %s", exc)
        shutdown_event.wait(0.01)


# --- HELPERS ---
def landmark_to_np(landmark, w, h):
    """Convert MediaPipe landmark to numpy array with pixel coordinates."""
    return np.array([landmark.x * w, landmark.y * h, landmark.z * w])


def normalize_vector(vector):
    norm = np.linalg.norm(vector)
    if not np.isfinite(norm) or norm < 1e-8:
        return None
    return vector / norm


def make_twilio_call():
    if not TWILIO_AVAILABLE:
        print("[Twilio Error] twilio package is not installed.")
        return
    if not (TWILIO_SID and TWILIO_AUTH and TWILIO_PHONE):
        print("[Twilio Error] Missing TWILIO_SID, TWILIO_AUTH, or TWILIO_PHONE in environment / .env file.")
        return
    try:
        client = Client(TWILIO_SID, TWILIO_AUTH)
        call = client.calls.create(
            twiml='<Response><Say>Emergency alert. User triggered SOS.</Say></Response>',
            to=SETTINGS["emergency_contact"],
            from_=TWILIO_PHONE
        )
        print(f"[Twilio Success] Emergency call initiated to {SETTINGS['emergency_contact']} (SID: {call.sid})")
    except Exception as exc:
        print(f"[Twilio Error] Failed to place call to {SETTINGS['emergency_contact']}: {exc}")


def auto_send_whatsapp(number, message):
    msg_encoded = urllib.parse.quote(message)
    link = f"https://web.whatsapp.com/send?phone={number}&text={msg_encoded}"
    webbrowser.open(link)
    try:
        delay = max(3.0, float(os.getenv("WHATSAPP_SEND_DELAY", "12")))
    except ValueError:
        delay = 12.0
    if not shutdown_event.wait(delay):
        # This remains a best-effort browser integration.  The native UI asks
        # for confirmation before reaching this point.
        try:
            pyautogui.press('enter')
        except pyautogui.FailSafeException:
            SETTINGS["system_paused"] = True
            logger.warning("WhatsApp send cancelled by PyAutoGUI failsafe")


def execute_type_external(text):
    if not shutdown_event.wait(5):
        try:
            pyautogui.write(text, interval=0.1)
        except pyautogui.FailSafeException:
            SETTINGS["system_paused"] = True
            logger.warning("External typing cancelled by PyAutoGUI failsafe")


def perform_action_internal(action, text=""):
    """Execute system/browser/emergency action from both web and native UI."""
    contact_num = SETTINGS["emergency_contact"]
    msg_body = "SOS! I need help. Sent via OptiKinesis."

    if action in {"emergency_contact", "dial_contact", "emergency_call"} and not contact_num:
        return {"status": "error", "message": "Configure an emergency contact first"}

    if action == 'google':
        webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(text)}")
    elif action == 'youtube':
        webbrowser.open(f"https://www.youtube.com/results?search_query={urllib.parse.quote(text)}")
    elif action == 'type_external':
        threading.Thread(target=execute_type_external, args=(text,), daemon=True).start()
        return {"status": "timer_started"}
    elif action == 'emergency_contact':
        threading.Thread(target=auto_send_whatsapp, args=(contact_num, msg_body), daemon=True).start()
    elif action == 'dial_contact':
        webbrowser.open(f"tel:{contact_num}")
        return {"status": "dialer_opened"}
    elif action == 'emergency_call':
        threading.Thread(target=make_twilio_call, daemon=True).start()
    elif action == 'emergency_police':
        webbrowser.open("tel:100")
    elif action == 'emergency_ambulance':
        webbrowser.open("tel:112")
    elif action == 'toggle_overlay':
        SETTINGS['overlay_enabled'] = not SETTINGS.get('overlay_enabled', True)
        save_settings()
        return {"status": "toggled", "overlay_enabled": SETTINGS['overlay_enabled']}
    elif action == 'toggle_mouse':
        SETTINGS['mouse_control_enabled'] = not SETTINGS.get('mouse_control_enabled', True)
        save_settings()
        return {"status": "toggled", "mouse_control_enabled": SETTINGS['mouse_control_enabled']}
    elif action == 'toggle_pause':
        SETTINGS['system_paused'] = not SETTINGS.get('system_paused', False)
        desktop_overlay.dispatch_notification(
            "SYSTEM PAUSED - press F12 to resume"
            if SETTINGS['system_paused']
            else "OptiKinesis resumed"
        )
        return {"status": "toggled", "system_paused": SETTINGS['system_paused']}
    elif action == 'calibrate':
        calibrated = calibrate_current_pose()
        return {
            "status": "calibrated" if calibrated else "error",
            "message": "Calibration complete" if calibrated else "No face pose is available yet",
        }
    elif action == 'save_settings':
        return {"status": "saved" if save_settings() else "error"}
    elif action == 'exit_application':
        shutdown_event.set()
        save_settings()
        desktop_overlay.request_exit()
        return {"status": "exiting"}

    return {"status": "ok"}


def get_blink_ratio(landmarks, eye_indices):
    """Calculate eye aspect ratio for blink detection."""
    top = landmarks[eye_indices[1]]
    bottom = landmarks[eye_indices[3]]
    left = landmarks[eye_indices[0]]
    right = landmarks[eye_indices[2]]
    
    ver_dist = math.hypot(top.x - bottom.x, top.y - bottom.y)
    hor_dist = math.hypot(left.x - right.x, left.y - right.y)
    
    return ver_dist / hor_dist if hor_dist != 0 else 0


# --- MEDIAPIPE/CAMERA SETUP (initialized only when the app starts) ---
MODEL_PATH = os.path.join(BASE_DIR, "face_landmarker.task")
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
face_landmarker = None
cap = None


def initialize_hardware():
    global face_landmarker, cap
    if not os.path.exists(MODEL_PATH):
        logger.info("Downloading face landmarker model to %s", MODEL_PATH)
        import urllib.request
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)

    base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=False,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    face_landmarker = vision.FaceLandmarker.create_from_options(options)

    for camera_index in (0, 1):
        candidate = cv2.VideoCapture(camera_index)
        if candidate.isOpened():
            cap = candidate
            logger.info("Using camera %d", camera_index)
            return True
        candidate.release()
    logger.error("No usable camera was found")
    cap = None
    return False


def cleanup_resources():
    """Stop worker loops and release camera/native model resources once."""
    shutdown_event.set()
    with cleanup_lock:
        if getattr(cleanup_resources, "_completed", False):
            return
        cleanup_resources._completed = True
        current_thread = threading.current_thread()
        for worker in list(worker_threads):
            if worker is not current_thread and worker.is_alive():
                worker.join(timeout=1.5)
        try:
            if cap is not None and cap.isOpened():
                cap.release()
        except Exception as exc:
            logger.warning("Camera cleanup failed: %s", exc)
        try:
            if face_landmarker is not None:
                face_landmarker.close()
        except Exception as exc:
            logger.warning("MediaPipe cleanup failed: %s", exc)
        try:
            voice_executor.reminder_scheduler.cancel_all()
        except Exception as exc:
            logger.warning("Reminder cleanup failed: %s", exc)
        logger.info("OptiKinesis resources released")


atexit.register(cleanup_resources)


def gen_frames(stream_output=True):
    """Generate video frames with head-pose tracking (from MonitorTracking.py)."""
    global calibration_offset_yaw, calibration_offset_pitch, latest_preview_frame
    global latest_raw_yaw, latest_raw_pitch

    if cap is None or face_landmarker is None:
        logger.error("Tracking requested before hardware initialization")
        return

    blink_detector = DeliberateBlinkDetector(
        min_duration=BLINK_DURATION_MIN,
        max_duration=1.2,
        blinks_required=int(SETTINGS.get("blinks_to_click", BLINKS_TO_CLICK)),
        blink_window=float(SETTINGS.get("blink_window", BLINK_WINDOW)),
        click_cooldown=float(SETTINGS.get("lock_delay", BLINK_COOLDOWN)),
    )
    blink_freeze = False
    blink_cursor_pos = (CENTER_X, CENTER_Y)  # Pre-blink cursor position for click
    LEFT_EYE = [33, 159, 133, 145]
    RIGHT_EYE = [362, 386, 263, 374]

    print("Camera Loop Started with Head-Pose Tracking.")

    while not shutdown_event.is_set():
        success, frame = cap.read()
        if not success:
            shutdown_event.wait(0.1)
            continue

        h, w, _ = frame.shape
        frame = cv2.flip(frame, 1)  # Mirror for natural interaction
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Convert to MediaPipe Image format for Tasks API
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        results = face_landmarker.detect(mp_image)

        if results.face_landmarks and len(results.face_landmarks) > 0:
            mark_face_seen()
            face_landmarks = results.face_landmarks[0]

            # --- HEAD-POSE TRACKING (from MonitorTracking.py) ---
            # Extract key points for orientation
            key_points = {}
            for name, idx in LANDMARKS.items():
                pt = landmark_to_np(face_landmarks[idx], w, h)
                key_points[name] = pt
                x, y = int(pt[0]), int(pt[1])
                
                cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)

            left = key_points["left"]
            right = key_points["right"]
            top = key_points["top"]
            bottom = key_points["bottom"]
            front = key_points["front"]

            # Compute oriented axes based on head geometry
            right_axis = normalize_vector(right - left)
            up_axis = normalize_vector(top - bottom)
            if right_axis is None or up_axis is None:
                continue

            forward_axis = normalize_vector(np.cross(right_axis, up_axis))
            if forward_axis is None:
                continue
            forward_axis = -forward_axis  # Flip to face outward

            # Compute center of the head
            center = (left + right + top + bottom + front) / 5

            # Update smoothing buffers
            ray_origins.append(center)
            ray_directions.append(forward_axis)

            # Compute averaged ray direction
            avg_origin = np.mean(ray_origins, axis=0)
            avg_direction = normalize_vector(np.mean(ray_directions, axis=0))
            if avg_direction is None:
                continue

            # Reference forward direction
            reference_forward = np.array([0, 0, -1])

            # Horizontal (yaw) angle
            xz_proj = np.array([avg_direction[0], 0, avg_direction[2]])
            xz_proj = normalize_vector(xz_proj)
            if xz_proj is None:
                continue
            yaw_rad = math.acos(np.clip(np.dot(reference_forward, xz_proj), -1.0, 1.0))
            if avg_direction[0] < 0:
                yaw_rad = -yaw_rad

            # Vertical (pitch) angle
            yz_proj = np.array([0, avg_direction[1], avg_direction[2]])
            yz_proj = normalize_vector(yz_proj)
            if yz_proj is None:
                continue
            pitch_rad = math.acos(np.clip(np.dot(reference_forward, yz_proj), -1.0, 1.0))
            if avg_direction[1] > 0:
                pitch_rad = -pitch_rad

            # Convert to degrees
            yaw_deg = np.degrees(yaw_rad)
            pitch_deg = np.degrees(pitch_rad)

            # Normalize angles (from MonitorTracking.py)
            if yaw_deg < 0:
                yaw_deg = abs(yaw_deg)
            elif yaw_deg < 180:
                yaw_deg = 360 - yaw_deg

            if pitch_deg < 0:
                pitch_deg = 360 + pitch_deg

            raw_yaw_deg = yaw_deg
            raw_pitch_deg = pitch_deg

            # Apply calibration offsets
            with calibration_lock:
                latest_raw_yaw = raw_yaw_deg
                latest_raw_pitch = raw_pitch_deg
                yaw_deg += calibration_offset_yaw
                pitch_deg += calibration_offset_pitch

            # Map to screen coordinates
            scope = max(0.35, min(2.0, float(SETTINGS.get("cursor_scope", 1.0))))
            yaw_range = YAW_DEGREES * scope
            pitch_range = PITCH_DEGREES * scope
            screen_x = int(((yaw_deg - (180 - yaw_range)) / (2 * yaw_range)) * SCREEN_W)
            screen_y = int(((180 + pitch_range - pitch_deg) / (2 * pitch_range)) * SCREEN_H)

            # Apply axis inversion to correct camera mirroring
            # INVERT_X: flip horizontal so look LEFT = cursor LEFT
            # INVERT_Y: flip vertical so look UP = cursor UP
            if INVERT_X:
                screen_x = SCREEN_W - screen_x
            if INVERT_Y:
                screen_y = SCREEN_H - screen_y

            # Clamp to screen bounds
            screen_x = max(10, min(SCREEN_W - 10, screen_x))
            screen_y = max(10, min(SCREEN_H - 10, screen_y))

            # Apply EMA smoothing for accessible speed
            global prev_screen_x, prev_screen_y
            speed = max(0.1, min(1.5, float(SETTINGS.get("cursor_speed", 0.5))))
            ema_alpha = max(0.05, min(0.85, speed * 0.57))
            screen_x = int(prev_screen_x + ema_alpha * (screen_x - prev_screen_x))
            screen_y = int(prev_screen_y + ema_alpha * (screen_y - prev_screen_y))
            prev_screen_x = screen_x
            prev_screen_y = screen_y

            # Update mouse target
            if SETTINGS.get("mouse_control_enabled", True) and not blink_freeze:
                with mouse_lock:
                    mouse_target[0] = screen_x
                    mouse_target[1] = screen_y

            # Draw gaze direction ray on frame
            half_depth = 80
            ray_length = 2.5 * half_depth
            ray_end = avg_origin - avg_direction * ray_length
            cv2.line(frame, (int(avg_origin[0]), int(avg_origin[1])), 
                    (int(ray_end[0]), int(ray_end[1])), (15, 255, 0), 3)
            # --- BLINK DETECTION with FATIGUE MONITORING ---
            left_ratio = get_blink_ratio(face_landmarks, LEFT_EYE)
            right_ratio = get_blink_ratio(face_landmarks, RIGHT_EYE)
            ratio = (left_ratio + right_ratio) / 2.0
            current_time = time.time()
            base_thresh = float(SETTINGS.get("blink_sensitivity", BLINK_THRESH))
            thresh = fatigue_monitor.get_adjusted_sensitivity(base_thresh)
            blink_detector.configure(
                blinks_required=int(SETTINGS.get("blinks_to_click", BLINKS_TO_CLICK)),
                blink_window=float(SETTINGS.get("blink_window", BLINK_WINDOW)),
                click_cooldown=float(SETTINGS.get("lock_delay", BLINK_COOLDOWN)),
            )
            blink_update = blink_detector.update(ratio < thresh, current_time)

            if blink_update.blink_started:
                with mouse_lock:
                    blink_cursor_pos = (mouse_target[0], mouse_target[1])
                blink_freeze = True

            if blink_update.blink_completed:
                blink_freeze = False
                fatigue_monitor.record_blink()
                if blink_update.click_triggered and not SETTINGS.get("system_paused", False):
                    dispatch_blink_click(blink_cursor_pos[0], blink_cursor_pos[1])
            elif not blink_detector.is_closed:
                blink_freeze = False

            required_blinks = blink_detector.blinks_required
            cv2.putText(frame, f"Blinks: {blink_update.blink_count}/{required_blinks}",
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            # Display tracking info
            cv2.putText(frame, f"Yaw: {raw_yaw_deg:.1f} Pitch: {raw_pitch_deg:.1f}", 
                       (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(frame, f"Blink {required_blinks}x to click | 'c' calibrates | F12 pauses",
                       (10, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

        else:
            blink_detector.reset(clear_sequence=True)
            blink_freeze = False
            cv2.putText(frame, "FACE NOT DETECTED", (50, 50), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        with latest_preview_lock:
            latest_preview_frame = frame.copy()

        if stream_output:
            ret, buffer = cv2.imencode('.jpg', frame)
            if ret:
                yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        else:
            yield None


def tracking_loop():
    """Run gaze/blink tracking continuously for native overlay mode."""
    try:
        for _ in gen_frames(stream_output=False):
            pass
    except Exception as exc:
        if not shutdown_event.is_set():
            logger.exception("Tracking loop stopped: %s", exc)


def stream_preview_frames():
    """Stream copies produced by the one tracking loop to Flask clients."""
    while not shutdown_event.is_set():
        frame = get_latest_preview_frame()
        if frame is None:
            shutdown_event.wait(0.05)
            continue
        ret, buffer = cv2.imencode('.jpg', frame)
        if ret:
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n'
                + buffer.tobytes()
                + b'\r\n'
            )
        shutdown_event.wait(0.04)


# --- ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    return Response(stream_preview_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/calibrate', methods=['POST'])
def calibrate():
    """Calibrate the gaze tracking to center on current head position."""
    calibrated = calibrate_current_pose()
    status = 200 if calibrated else 409
    return jsonify({
        "status": "calibrated" if calibrated else "not_ready",
        "message": "Calibration complete" if calibrated else "No face pose is available yet",
    }), status


@app.route('/update_settings', methods=['POST'])
def update_settings():
    data = request.get_json(silent=True) or {}
    if 'emergency_contact' in data:
        SETTINGS['emergency_contact'] = str(data['emergency_contact']).strip()
    if 'cursor_scope' in data:
        try:
            SETTINGS['cursor_scope'] = max(0.35, min(2.0, float(data['cursor_scope'])))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid cursor_scope"}), 400
    if 'blink_sensitivity' in data:
        try:
            SETTINGS['blink_sensitivity'] = max(0.10, min(0.40, float(data['blink_sensitivity'])))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid blink_sensitivity"}), 400
    if 'cursor_speed' in data:
        try:
            speed = float(data['cursor_speed'])
            SETTINGS['cursor_speed'] = max(0.1, min(1.5, speed))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid cursor_speed"}), 400
    if 'blinks_to_click' in data:
        try:
            SETTINGS['blinks_to_click'] = max(1, min(2, int(data['blinks_to_click'])))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid blinks_to_click"}), 400
    if 'blink_window' in data:
        try:
            SETTINGS['blink_window'] = max(0.5, min(3.0, float(data['blink_window'])))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid blink_window"}), 400
    if 'lock_delay' in data:
        try:
            SETTINGS['lock_delay'] = max(0.2, min(5.0, float(data['lock_delay'])))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid lock_delay"}), 400
    if 'overlay_enabled' in data:
        try:
            SETTINGS['overlay_enabled'] = coerce_bool(data['overlay_enabled'])
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid overlay_enabled"}), 400
    if 'mouse_control_enabled' in data:
        try:
            SETTINGS['mouse_control_enabled'] = coerce_bool(data['mouse_control_enabled'])
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid mouse_control_enabled"}), 400
    save_settings()
    return jsonify({"status": "updated", "settings": SETTINGS})


@app.route('/settings')
def get_settings():
    return jsonify(SETTINGS)


@app.route('/perform_action', methods=['POST'])
def perform_action():
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    text = data.get('text', '')
    return jsonify(perform_action_internal(action, text))


@app.route('/fatigue_status')
def fatigue_status():
    return jsonify(fatigue_monitor.check_fatigue())


@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "face_detected": is_face_tracking_active(),
        "camera_open": bool(cap and cap.isOpened()),
        "paused": SETTINGS.get("system_paused", False),
        "fatigue": fatigue_monitor.get_stats(),
    })


# --- VOICE COMMAND ROUTES ---
@app.route('/voice_command', methods=['POST'])
def voice_command():
    """
    Process a voice command from the frontend.
    
    Expects JSON: {"text": "search hello on google"}
    Returns action status or pending confirmation request.
    """
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    
    if not text:
        return jsonify({'status': 'error', 'message': 'No command text provided'})
    
    result = voice_executor.process_command(text)
    return jsonify(result)


@app.route('/confirm_action', methods=['POST'])
def confirm_action():
    """Confirm the pending voice command action."""
    result = voice_executor.confirm_pending()
    return jsonify(result)


@app.route('/cancel_action', methods=['POST'])
def cancel_action():
    """Cancel the pending voice command action."""
    result = voice_executor.cancel_pending()
    return jsonify(result)


@app.route('/pending_action')
def pending_action():
    """Check if there's a pending action awaiting confirmation."""
    pending = voice_executor.get_pending()

    if pending:
        return jsonify({'has_pending': True, **pending})
    return jsonify({'has_pending': False})


# --- KEYBOARD LISTENER FOR CALIBRATION ---
def keyboard_listener():
    """Listen for the caregiver calibration and emergency-pause keys."""
    
    try:
        from pynput import keyboard
        from pynput.keyboard import Key
        
        def on_press(key):
            try:
                if hasattr(key, 'char') and key.char == 'c':
                    if not calibrate_current_pose():
                        logger.warning("Calibration requested before a face pose was available")
                elif key == Key.f12:
                    SETTINGS["system_paused"] = not SETTINGS.get("system_paused", False)
                    desktop_overlay.dispatch_notification(
                        "SYSTEM PAUSED - press F12 to resume"
                        if SETTINGS["system_paused"]
                        else "OptiKinesis resumed"
                    )
                    logger.warning(
                        "F12 safety pause: %s",
                        "PAUSED" if SETTINGS["system_paused"] else "RESUMED",
                    )
            except AttributeError:
                pass
        
        listener = keyboard.Listener(on_press=on_press)
        listener.start()
        while not shutdown_event.wait(0.2):
            pass
        listener.stop()
    except ImportError:
        print("WARNING: pynput not installed. Keyboard calibration disabled.")
        print("Install with: pip install pynput")
    except Exception as exc:
        logger.warning("Global safety-key listener stopped: %s", exc)


def run_application(args):
    """Start either the native overlay (default) or the optional web UI."""
    hardware_ready = initialize_hardware()
    desktop_overlay.configure_overlay(
        settings=SETTINGS,
        action_executor=perform_action_internal,
        whatsapp_sender=auto_send_whatsapp,
        frame_provider=get_latest_preview_frame,
        voice_executor=voice_executor,
        fatigue_provider=fatigue_monitor.check_fatigue,
        shutdown_callback=cleanup_resources,
        lock_delay=LOCK_DELAY,
        debug_hud=DEBUG_BLINK_HUD,
    )
    voice_executor.set_reminder_callback(desktop_overlay.dispatch_notification)

    new_workers = [
        threading.Thread(target=mouse_mover, name="mouse-mover", daemon=True),
        threading.Thread(target=keyboard_listener, name="safety-keys", daemon=True),
    ]
    if hardware_ready:
        new_workers.append(
            threading.Thread(target=tracking_loop, name="face-tracking", daemon=True)
        )
    else:
        logger.warning("Starting UI without camera tracking")
    worker_threads.extend(new_workers)
    for worker in new_workers:
        worker.start()

    if args.web:
        url = f"http://{args.host}:{args.port}"
        if not args.no_browser:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        logger.info("Starting optional web interface at %s", url)
        try:
            app.run(
                host=args.host,
                port=args.port,
                debug=False,
                threaded=True,
                use_reloader=False,
            )
        finally:
            cleanup_resources()
        return

    if PYQT5_AVAILABLE:
        try:
            # Qt must own the main thread on Windows and macOS.
            desktop_overlay.launch_overlay()
        finally:
            cleanup_resources()
    else:
        logger.error("PyQt5 is required for native overlay mode")
        cleanup_resources()
        raise SystemExit(1)


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="OptiKinesis assistive computer control")
    parser.add_argument(
        "--web",
        action="store_true",
        help="run the legacy-compatible browser dashboard instead of the native overlay",
    )
    parser.add_argument("--host", default="127.0.0.1", help="web-mode bind address")
    parser.add_argument("--port", default=5000, type=int, help="web-mode port")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser automatically in web mode"
    )
    return parser.parse_args(argv)


if __name__ == '__main__':
    try:
        run_application(parse_arguments())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        cleanup_resources()
