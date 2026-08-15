"""
Overlay UI Preview Launcher (with Auto-Reload)

Launches ONLY the PyQt5 overlay UI — no camera, no head tracking, no MediaPipe.
Your normal mouse works, so you can click around and test the UI freely.

When you save changes to desktop_overlay.py, the UI automatically restarts
with your new code within ~1 second.

Usage:
    python preview_overlay.py
    
    Then edit desktop_overlay.py in your editor, save, and watch the UI reload.
    Press Ctrl+C in the terminal to stop.
"""

import sys
import os
import time
import subprocess

WATCHED_FILE = os.path.join(os.path.dirname(__file__), "desktop_overlay.py")
CHILD_FLAG = "__PREVIEW_OVERLAY_CHILD__"


def run_overlay():
    """Run the overlay UI in-process (called in the child subprocess)."""
    import desktop_overlay

    # Configure with no-op callbacks so the UI works standalone
    desktop_overlay.configure_overlay(
        settings={
            "emergency_contact": "+911234567890",
            "cursor_scope": 1.0,
            "blink_sensitivity": 0.2,
            "cursor_speed": 0.5,
            "overlay_enabled": False,   # Disable gaze ring (normal mouse mode)
            "mouse_control_enabled": False,
        },
        action_executor=lambda action, text="": print(f"[Preview] Action: {action}, Text: {text!r}"),
        whatsapp_sender=lambda num, msg: print(f"[Preview] WhatsApp → {num}: {msg}"),
        frame_provider=lambda: None,   # No camera feed
        voice_executor=None,
        lock_delay=1.5,
        debug_hud=False,
    )

    print("=" * 60)
    print("  OVERLAY UI PREVIEW  —  Normal mouse active")
    print("  Edit desktop_overlay.py and save → auto-reload")
    print("  Ctrl+C in terminal to stop")
    print("=" * 60)

    desktop_overlay.launch_overlay()


def watch_and_restart():
    """Parent process: launch child, watch file, restart on change."""
    print(f"[AutoReload] Watching: {WATCHED_FILE}")
    print(f"[AutoReload] Starting overlay preview...\n")

    env = os.environ.copy()
    env[CHILD_FLAG] = "1"

    last_mtime = os.path.getmtime(WATCHED_FILE)
    proc = subprocess.Popen([sys.executable, __file__], env=env)

    try:
        while True:
            # Check if the child exited on its own (user closed window)
            ret = proc.poll()
            if ret is not None:
                print(f"\n[AutoReload] Overlay exited (code {ret}). Stopping.")
                break

            # Check for file changes
            try:
                current_mtime = os.path.getmtime(WATCHED_FILE)
            except OSError:
                time.sleep(0.5)
                continue

            if current_mtime != last_mtime:
                last_mtime = current_mtime
                print("\n[AutoReload] Change detected in desktop_overlay.py — reloading...")
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

                time.sleep(0.3)  # Brief pause before restart
                proc = subprocess.Popen([sys.executable, __file__], env=env)

            time.sleep(0.7)  # Poll interval

    except KeyboardInterrupt:
        print("\n[AutoReload] Ctrl+C — shutting down...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    if os.environ.get(CHILD_FLAG):
        # We are the child subprocess — run the actual overlay
        run_overlay()
    else:
        # We are the parent — watch and auto-restart
        watch_and_restart()
