from desktop_overlay import GazeClickBridge, dispatch_blink_click
import ctypes

try:
    bridge = GazeClickBridge()
    bridge._fallback_os_click(10, 10)
    print("Success")
except Exception as e:
    import traceback
    traceback.print_exc()
