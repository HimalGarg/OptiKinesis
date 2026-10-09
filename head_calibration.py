"""Five-point head-pose calibration for OptiKinesis.

The user turns their head toward five on-screen targets: the center and the
four corners, each inset from the screen edge.  Each target records the head
angles (yaw, pitch) the user actually reached.  Those five readings define a
personal map from head angles to screen position:

* The screen is split into four triangles that fan out from the center
  (top, right, bottom, left).  Inside each triangle the map is an affine
  blend of its three calibration points, so the cursor lands exactly on all
  five targets and adjacent triangles agree along their shared edge.
* Outside the calibrated corners the same per-triangle maps extrapolate, so
  the screen edges stay reachable beyond the inset targets.

This module is pure Python with no Qt or camera dependency so it can be unit
tested directly.  ``CalibrationSession`` holds the step-by-step capture logic
that the on-screen overlay drives.
"""

from __future__ import annotations

import math
import time
from collections import deque
from statistics import median
from typing import Deque, Dict, List, Optional, Tuple

Point = Tuple[float, float]

CALIBRATION_VERSION = 1

# Fraction of the screen that each corner target sits in from the edges.
# Aiming at the true corner is a strain; the map extrapolates past the target.
TARGET_INSET = 0.10

# Capture order: center first to anchor the neutral pose, then clockwise.
POINT_ORDER = ("center", "top_left", "top_right", "bottom_right", "bottom_left")

POINT_LABELS = {
    "center": "center",
    "top_left": "top-left",
    "top_right": "top-right",
    "bottom_right": "bottom-right",
    "bottom_left": "bottom-left",
}

# The four triangles that fan out from the center, as (corner, next corner).
FAN = (
    ("top_left", "top_right"),
    ("top_right", "bottom_right"),
    ("bottom_right", "bottom_left"),
    ("bottom_left", "top_left"),
)

# Smallest head movement, in degrees, accepted between opposite sides.  Below
# this, normal tracking noise would be magnified into a jumpy cursor.
MIN_SPAN_DEG = 6.0

# Smallest allowed |sin| of the angle between neighbouring corner directions.
# Rejects near-collinear corners that would make a triangle degenerate.
MIN_CORNER_SINE = 0.15


def default_targets(inset: float = TARGET_INSET) -> Dict[str, Point]:
    """Normalised (0..1) screen positions of the five calibration targets."""
    low, high = inset, 1.0 - inset
    return {
        "center": (0.5, 0.5),
        "top_left": (low, low),
        "top_right": (high, low),
        "bottom_right": (high, high),
        "bottom_left": (low, high),
    }


class CalibrationError(ValueError):
    """Raised when calibration readings cannot form a usable map."""


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _mid(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def validate_readings(angles: Dict[str, Point]) -> None:
    """Raise CalibrationError if the five readings cannot form a reliable map."""
    missing = [name for name in POINT_ORDER if name not in angles]
    if missing:
        raise CalibrationError(f"Missing calibration points: {', '.join(missing)}")
    for name in POINT_ORDER:
        yaw, pitch = angles[name]
        if not (math.isfinite(yaw) and math.isfinite(pitch)):
            raise CalibrationError("Calibration readings contain invalid numbers")

    horizontal = _distance(
        _mid(angles["top_left"], angles["bottom_left"]),
        _mid(angles["top_right"], angles["bottom_right"]),
    )
    vertical = _distance(
        _mid(angles["top_left"], angles["top_right"]),
        _mid(angles["bottom_left"], angles["bottom_right"]),
    )
    if horizontal < MIN_SPAN_DEG or vertical < MIN_SPAN_DEG:
        raise CalibrationError(
            "Head movement was too small to calibrate. "
            "Try turning a little further toward each corner."
        )

    # Every corner direction, seen from the center, must turn the same way
    # round as the next one.  That guarantees the four triangles tile the
    # plane around the center with no overlaps or gaps.  A consistent mirror
    # image (all turning the other way) is fine; the map learns it.
    center = angles["center"]
    turns = []
    for first, second in FAN:
        a = _sub(angles[first], center)
        b = _sub(angles[second], center)
        length = math.hypot(*a) * math.hypot(*b)
        if length < 1e-9:
            raise CalibrationError(
                "A corner reading matched the center. Please try again."
            )
        turns.append(_cross(a, b) / length)
    if not (all(t > MIN_CORNER_SINE for t in turns) or all(t < -MIN_CORNER_SINE for t in turns)):
        raise CalibrationError(
            "The corner readings didn't line up. "
            "Please try again and turn toward each dot as it appears."
        )


class HeadCalibration:
    """Personal map from raw head angles (degrees) to normalised screen position."""

    def __init__(
        self,
        angles: Dict[str, Point],
        targets: Optional[Dict[str, Point]] = None,
        created: Optional[float] = None,
    ) -> None:
        self.angles = {name: (float(angles[name][0]), float(angles[name][1])) for name in POINT_ORDER}
        source_targets = targets or default_targets()
        self.targets = {
            name: (float(source_targets[name][0]), float(source_targets[name][1]))
            for name in POINT_ORDER
        }
        self.created = float(created if created is not None else time.time())
        validate_readings(self.angles)
        self._triangles = self._build_triangles()

    def _build_triangles(self):
        center = self.angles["center"]
        center_target = self.targets["center"]
        triangles = []
        for first, second in FAN:
            a = _sub(self.angles[first], center)
            b = _sub(self.angles[second], center)
            det = _cross(a, b)
            screen_a = _sub(self.targets[first], center_target)
            screen_b = _sub(self.targets[second], center_target)
            triangles.append((a, b, det, screen_a, screen_b))
        return triangles

    def map(self, yaw: float, pitch: float) -> Point:
        """Return the normalised screen position (0..1 inside the screen) for a pose."""
        center = self.angles["center"]
        vx, vy = yaw - center[0], pitch - center[1]
        chosen = None
        fallback = None
        for a, b, det, screen_a, screen_b in self._triangles:
            # Solve v = s * a + t * b for this triangle's two corner directions.
            s = (vx * b[1] - vy * b[0]) / det
            t = (a[0] * vy - a[1] * vx) / det
            if s >= -1e-9 and t >= -1e-9:
                chosen = (s, t, screen_a, screen_b)
                break
            score = min(s, t)
            if fallback is None or score > fallback[0]:
                fallback = (score, s, t, screen_a, screen_b)
        if chosen is None:
            _score, s, t, screen_a, screen_b = fallback
        else:
            s, t, screen_a, screen_b = chosen
        cx, cy = self.targets["center"]
        return (
            cx + s * screen_a[0] + t * screen_b[0],
            cy + s * screen_a[1] + t * screen_b[1],
        )

    def recentered(self, yaw: float, pitch: float) -> "HeadCalibration":
        """Shift the whole map so the given pose becomes the center target.

        Used for quick re-centering when the user shifts position; the shape
        and reach learned during the full calibration are kept.
        """
        dx = float(yaw) - self.angles["center"][0]
        dy = float(pitch) - self.angles["center"][1]
        shifted = {name: (value[0] + dx, value[1] + dy) for name, value in self.angles.items()}
        return HeadCalibration(shifted, self.targets, created=self.created)

    def to_dict(self) -> dict:
        return {
            "version": CALIBRATION_VERSION,
            "created": round(self.created, 3),
            "points": {name: [round(v, 4) for v in self.angles[name]] for name in POINT_ORDER},
            "targets": {name: [round(v, 4) for v in self.targets[name]] for name in POINT_ORDER},
        }

    @classmethod
    def from_dict(cls, data) -> Optional["HeadCalibration"]:
        """Rebuild a saved calibration; returns None for missing or unusable data."""
        if not isinstance(data, dict) or data.get("version") != CALIBRATION_VERSION:
            return None
        try:
            points = {name: tuple(data["points"][name]) for name in POINT_ORDER}
            targets = {name: tuple(data["targets"][name]) for name in POINT_ORDER}
            return cls(points, targets, created=data.get("created"))
        except (KeyError, TypeError, ValueError, IndexError):
            return None


class StableSampler:
    """Collect pose samples until the head has been steady long enough.

    Keeps the most recent run of samples whose spread on each axis stays
    within ``max_spread_deg``.  When that run covers ``capture_s`` seconds,
    its median is the reading for the target.
    """

    def __init__(self, capture_s: float = 1.0, max_spread_deg: float = 1.5, max_gap_s: float = 0.4):
        self.capture_s = float(capture_s)
        self.max_spread_deg = float(max_spread_deg)
        self.max_gap_s = float(max_gap_s)
        self._run: Deque[Tuple[float, float, float]] = deque()

    def reset(self) -> None:
        self._run.clear()

    def add(self, now: float, yaw: float, pitch: float) -> None:
        if self._run and now - self._run[-1][0] > self.max_gap_s:
            self._run.clear()
        self._run.append((now, float(yaw), float(pitch)))
        while len(self._run) > 1 and not self._steady():
            self._run.popleft()

    def note_gap(self, now: float) -> None:
        """Call when no fresh pose is available (face lost)."""
        if self._run and now - self._run[-1][0] > self.max_gap_s:
            self._run.clear()

    def _steady(self) -> bool:
        yaws = [sample[1] for sample in self._run]
        pitches = [sample[2] for sample in self._run]
        return (
            max(yaws) - min(yaws) <= self.max_spread_deg
            and max(pitches) - min(pitches) <= self.max_spread_deg
        )

    @property
    def progress(self) -> float:
        if len(self._run) < 2:
            return 0.0
        return max(0.0, min(1.0, (self._run[-1][0] - self._run[0][0]) / self.capture_s))

    @property
    def done(self) -> bool:
        return self.progress >= 1.0

    def reading(self) -> Point:
        return (
            median(sample[1] for sample in self._run),
            median(sample[2] for sample in self._run),
        )


class CalibrationSession:
    """Step-by-step capture of the five calibration points.

    The overlay calls ``update(now, pose)`` on every animation tick, where
    ``pose`` is ``(yaw, pitch, timestamp)`` or ``None`` when no face is seen,
    and draws the target at ``target_position(now)``.
    """

    TRAVEL = "travel"
    SETTLE = "settle"
    CAPTURE = "capture"
    DONE = "done"
    FAILED = "failed"

    def __init__(
        self,
        targets: Optional[Dict[str, Point]] = None,
        travel_s: float = 0.8,
        settle_s: float = 0.5,
        capture_s: float = 1.0,
        max_spread_deg: float = 1.5,
        point_timeout_s: float = 15.0,
    ) -> None:
        self.targets = targets or default_targets()
        self.travel_s = float(travel_s)
        self.settle_s = float(settle_s)
        self.point_timeout_s = float(point_timeout_s)
        self.sampler = StableSampler(capture_s=capture_s, max_spread_deg=max_spread_deg)
        self.readings: Dict[str, Point] = {}
        self.index = 0
        self.phase = self.SETTLE
        self.result: Optional[HeadCalibration] = None
        self.error = ""
        self.face_visible = False
        self._phase_started = 0.0
        self._previous_target: Point = self.targets[POINT_ORDER[0]]
        self._last_pose_stamp: Optional[float] = None
        self._started = False

    # ── state ────────────────────────────────────────────────────────────

    @property
    def point_name(self) -> str:
        return POINT_ORDER[min(self.index, len(POINT_ORDER) - 1)]

    @property
    def point_count(self) -> int:
        return len(POINT_ORDER)

    @property
    def finished(self) -> bool:
        return self.phase in (self.DONE, self.FAILED)

    @property
    def progress(self) -> float:
        """Capture progress for the current point, 0..1."""
        if self.phase == self.CAPTURE:
            return self.sampler.progress
        if self.phase == self.DONE:
            return 1.0
        return 0.0

    def completed_points(self) -> List[str]:
        return [name for name in POINT_ORDER if name in self.readings]

    def target_position(self, now: float) -> Point:
        """Where to draw the target now, gliding between points while travelling."""
        current = self.targets[self.point_name]
        if self.phase != self.TRAVEL or self.travel_s <= 0:
            return current
        t = max(0.0, min(1.0, (now - self._phase_started) / self.travel_s))
        eased = t * t * (3.0 - 2.0 * t)  # smoothstep
        start = self._previous_target
        return (
            start[0] + (current[0] - start[0]) * eased,
            start[1] + (current[1] - start[1]) * eased,
        )

    # ── driving ──────────────────────────────────────────────────────────

    def start(self, now: float) -> None:
        self._started = True
        self.index = 0
        self.readings.clear()
        self.result = None
        self.error = ""
        self._enter(self.SETTLE, now)

    def update(self, now: float, pose: Optional[Tuple[float, float, float]]) -> None:
        if not self._started:
            self.start(now)
        if self.finished:
            return

        fresh = None
        if pose is not None:
            yaw, pitch, stamp = pose
            self.face_visible = True
            if self._last_pose_stamp is None or stamp > self._last_pose_stamp:
                self._last_pose_stamp = stamp
                fresh = (yaw, pitch)
        else:
            self.face_visible = False

        elapsed = now - self._phase_started
        if self.phase == self.TRAVEL:
            if elapsed >= self.travel_s:
                self._enter(self.SETTLE, now)
            return
        if self.phase == self.SETTLE:
            if elapsed >= self.settle_s:
                self._enter(self.CAPTURE, now)
            return

        # CAPTURE
        if fresh is not None:
            self.sampler.add(now, fresh[0], fresh[1])
        else:
            self.sampler.note_gap(now)

        if self.sampler.done:
            self.readings[self.point_name] = self.sampler.reading()
            self._advance(now)
        elif elapsed >= self.point_timeout_s:
            label = POINT_LABELS[self.point_name]
            if self.face_visible:
                self.error = f"Couldn't get a steady reading at the {label} point."
            else:
                self.error = "Face not detected. Make sure your face is visible to the camera."
            self.phase = self.FAILED

    def cancel(self, reason: str = "Calibration cancelled") -> None:
        if not self.finished:
            self.error = reason
            self.phase = self.FAILED

    def _advance(self, now: float) -> None:
        if self.index + 1 >= len(POINT_ORDER):
            try:
                self.result = HeadCalibration(self.readings, self.targets)
                self.phase = self.DONE
            except CalibrationError as exc:
                self.error = str(exc)
                self.phase = self.FAILED
            return
        self._previous_target = self.targets[self.point_name]
        self.index += 1
        self._enter(self.TRAVEL, now)

    def _enter(self, phase: str, now: float) -> None:
        self.phase = phase
        self._phase_started = now
        if phase == self.CAPTURE:
            self.sampler.reset()
