"""Reusable, testable blink-to-click state machine.

The camera loop owns cursor freezing and click dispatch.  This class only
decides whether a closed/open eye sequence is a deliberate blink and whether
enough deliberate blinks occurred inside the configured time window.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass(frozen=True)
class BlinkUpdate:
    blink_started: bool = False
    blink_completed: bool = False
    click_triggered: bool = False
    blink_count: int = 0
    duration: float = 0.0


class DeliberateBlinkDetector:
    """Convert eye-closed state changes into safe click events."""

    def __init__(
        self,
        *,
        min_duration: float = 0.15,
        max_duration: float = 1.2,
        blinks_required: int = 2,
        blink_window: float = 1.5,
        click_cooldown: float = 0.5,
    ) -> None:
        self.min_duration = float(min_duration)
        self.max_duration = float(max_duration)
        self.blinks_required = max(1, int(blinks_required))
        self.blink_window = max(0.1, float(blink_window))
        self.click_cooldown = max(0.0, float(click_cooldown))

        self._closed_since: Optional[float] = None
        self._blink_times: Deque[float] = deque()
        self._last_click_time = float("-inf")

    @property
    def is_closed(self) -> bool:
        return self._closed_since is not None

    @property
    def blink_count(self) -> int:
        return len(self._blink_times)

    def configure(
        self,
        *,
        blinks_required: Optional[int] = None,
        blink_window: Optional[float] = None,
        click_cooldown: Optional[float] = None,
    ) -> None:
        """Apply live settings without discarding an in-progress blink."""
        if blinks_required is not None:
            self.blinks_required = max(1, int(blinks_required))
        if blink_window is not None:
            self.blink_window = max(0.1, float(blink_window))
        if click_cooldown is not None:
            self.click_cooldown = max(0.0, float(click_cooldown))

    def update(self, eye_closed: bool, timestamp: float) -> BlinkUpdate:
        now = float(timestamp)
        self._remove_expired_blinks(now)

        if eye_closed:
            if self._closed_since is None:
                self._closed_since = now
                return BlinkUpdate(blink_started=True, blink_count=self.blink_count)
            return BlinkUpdate(blink_count=self.blink_count)

        if self._closed_since is None:
            return BlinkUpdate(blink_count=self.blink_count)

        duration = max(0.0, now - self._closed_since)
        self._closed_since = None

        # Very short closures are camera noise; very long closures are likely
        # rest/sleep and should never become a click.
        if duration < self.min_duration or duration > self.max_duration:
            return BlinkUpdate(blink_count=self.blink_count, duration=duration)

        # Blinks during the post-click lock are intentionally discarded so
        # they cannot accumulate into a delayed accidental click.
        if now - self._last_click_time < self.click_cooldown:
            self._blink_times.clear()
            return BlinkUpdate(blink_completed=True, duration=duration)

        self._blink_times.append(now)
        click_triggered = (
            len(self._blink_times) >= self.blinks_required
        )

        count = len(self._blink_times)
        if click_triggered:
            self._last_click_time = now
            self._blink_times.clear()
            count = 0

        return BlinkUpdate(
            blink_completed=True,
            click_triggered=click_triggered,
            blink_count=count,
            duration=duration,
        )

    def reset(self, *, clear_sequence: bool = True) -> None:
        self._closed_since = None
        if clear_sequence:
            self._blink_times.clear()

    def _remove_expired_blinks(self, now: float) -> None:
        cutoff = now - self.blink_window
        while self._blink_times and self._blink_times[0] < cutoff:
            self._blink_times.popleft()
