"""
Fatigue-Aware Blink Monitoring System for Senseway

This module monitors blink patterns to detect user fatigue during extended
eye-gaze interaction sessions. When fatigue is detected, it:
1. Triggers a soft UI notification
2. Temporarily reduces blink sensitivity (making clicks require more deliberate blinks)
3. Enters a cooldown period before resetting

Design Philosophy:
- Passive monitoring: never blocks or interrupts the user
- Adaptive thresholds: adjusts based on user's natural blink rate
- Supportive feedback: calm, non-alarming notifications
"""

import time
from collections import deque
from threading import Lock
from typing import Optional, Dict, Any
import logging

# Configure logging for fatigue events (optional tuning data)
logging.basicConfig(level=logging.INFO)
fatigue_logger = logging.getLogger('fatigue_monitor')


class FatigueMonitor:
    """
    Monitors blink patterns to detect and respond to user fatigue.
    
    Uses a rolling time window to track blink frequency. When blinks exceed
    the threshold, fatigue mode is activated with:
    - Reduced blink sensitivity
    - Cooldown period before reset
    - Logged events for tuning
    
    Thread-safe for use with Flask and video processing threads.
    """
    
    # Default configuration (accessible for tuning)
    DEFAULT_CONFIG = {
        'window_size': 45,           # Rolling window in seconds
        'blink_threshold': 20,       # Blinks in window to trigger fatigue
        'cooldown_period': 30,       # Seconds before fatigue resets
        'sensitivity_reduction': 0.7, # Multiplier for blink threshold (lower = harder to click)
        'min_blinks_baseline': 8,    # Minimum blinks before fatigue can trigger
    }
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the fatigue monitor.
        
        Args:
            config: Optional dict to override default configuration values
        """
        # Merge provided config with defaults
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        
        # Rolling window of blink timestamps
        self.blink_timestamps: deque = deque()
        
        # Fatigue state
        self.is_fatigued: bool = False
        self.fatigue_start_time: float = 0
        self.last_notification_time: float = 0
        
        # Thread safety
        self._lock = Lock()
        
        # Statistics for adaptive thresholds (future enhancement)
        self.total_blinks: int = 0
        self.session_start_time: float = time.time()
        
        fatigue_logger.info("FatigueMonitor initialized with config: %s", self.config)
    
    def record_blink(self) -> bool:
        """
        Record a blink event and check if fatigue threshold is exceeded.
        
        Returns:
            True if this blink triggered a fatigue state change
        """
        current_time = time.time()
        triggered_fatigue = False
        
        with self._lock:
            # Add current blink
            self.blink_timestamps.append(current_time)
            self.total_blinks += 1
            
            # Remove old blinks outside the window
            window_start = current_time - self.config['window_size']
            while self.blink_timestamps and self.blink_timestamps[0] < window_start:
                self.blink_timestamps.popleft()
            
            # Check fatigue condition
            blink_count = len(self.blink_timestamps)
            
            if not self.is_fatigued:
                # Only trigger if we have enough baseline data
                if blink_count >= self.config['blink_threshold']:
                    if blink_count >= self.config['min_blinks_baseline']:
                        self._enter_fatigue_state(current_time, blink_count)
                        triggered_fatigue = True
            else:
                # Check if cooldown has elapsed
                if current_time - self.fatigue_start_time > self.config['cooldown_period']:
                    self._exit_fatigue_state(current_time)
        
        return triggered_fatigue
    
    def _enter_fatigue_state(self, current_time: float, blink_count: int) -> None:
        """Enter fatigue state and log the event."""
        self.is_fatigued = True
        self.fatigue_start_time = current_time
        self.last_notification_time = current_time
        
        fatigue_logger.warning(
            "FATIGUE DETECTED: %d blinks in %ds window. "
            "Reducing sensitivity by %.0f%% for %ds cooldown.",
            blink_count,
            self.config['window_size'],
            (1 - self.config['sensitivity_reduction']) * 100,
            self.config['cooldown_period']
        )
    
    def _exit_fatigue_state(self, current_time: float) -> None:
        """Exit fatigue state after cooldown."""
        self.is_fatigued = False
        cooldown_duration = current_time - self.fatigue_start_time
        
        fatigue_logger.info(
            "Fatigue cooldown complete after %.1fs. Sensitivity restored.",
            cooldown_duration
        )
        
        # Clear blink history to prevent immediate re-triggering
        self.blink_timestamps.clear()
    
    def check_fatigue(self) -> Dict[str, Any]:
        """
        Get current fatigue status.
        
        Returns:
            Dict with fatigue state information:
            - is_fatigued: bool
            - blink_count: current blinks in window
            - threshold: current threshold
            - cooldown_remaining: seconds until fatigue clears (if fatigued)
            - should_show_toast: bool (rate-limited notification)
        """
        current_time = time.time()
        
        with self._lock:
            # Clean up old timestamps
            window_start = current_time - self.config['window_size']
            while self.blink_timestamps and self.blink_timestamps[0] < window_start:
                self.blink_timestamps.popleft()
            
            blink_count = len(self.blink_timestamps)
            
            # Calculate cooldown remaining
            if self.is_fatigued:
                elapsed = current_time - self.fatigue_start_time
                cooldown_remaining = max(0, self.config['cooldown_period'] - elapsed)
                
                # Check if cooldown completed
                if cooldown_remaining == 0:
                    self._exit_fatigue_state(current_time)
            else:
                cooldown_remaining = 0
            
            # Rate-limit toast notifications (show once per fatigue episode)
            should_show_toast = (
                self.is_fatigued and 
                current_time - self.last_notification_time < 1.0  # Within 1s of fatigue trigger
            )
            
            return {
                'is_fatigued': self.is_fatigued,
                'blink_count': blink_count,
                'threshold': self.config['blink_threshold'],
                'cooldown_remaining': round(cooldown_remaining, 1),
                'should_show_toast': should_show_toast,
                'window_size': self.config['window_size']
            }
    
    def get_adjusted_sensitivity(self, base_sensitivity: float) -> float:
        """
        Get the blink sensitivity adjusted for fatigue state.
        
        During fatigue, sensitivity is reduced (threshold lowered), 
        requiring more deliberate blinks to trigger clicks.
        This reduces accidental clicks when the user is struggling.
        
        Args:
            base_sensitivity: The normal blink threshold (e.g., 0.2)
        
        Returns:
            Adjusted sensitivity value
        """
        with self._lock:
            if self.is_fatigued:
                # Lower threshold = harder to trigger a click
                # This is protective during fatigue
                return base_sensitivity * self.config['sensitivity_reduction']
            return base_sensitivity
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get session statistics for monitoring and tuning.
        
        Returns:
            Dict with session statistics
        """
        current_time = time.time()
        session_duration = current_time - self.session_start_time
        
        with self._lock:
            return {
                'total_blinks': self.total_blinks,
                'session_duration_seconds': round(session_duration, 1),
                'average_blinks_per_minute': round(
                    (self.total_blinks / session_duration) * 60, 1
                ) if session_duration > 0 else 0,
                'is_fatigued': self.is_fatigued,
                'current_window_blinks': len(self.blink_timestamps)
            }
    
    def reset(self) -> None:
        """Reset the fatigue monitor state."""
        with self._lock:
            self.blink_timestamps.clear()
            self.is_fatigued = False
            self.fatigue_start_time = 0
            self.last_notification_time = 0
            fatigue_logger.info("FatigueMonitor reset")


# Global instance for easy access from main.py
fatigue_monitor = FatigueMonitor()
