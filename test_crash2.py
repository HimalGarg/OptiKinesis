import sys
from PyQt5.QtWidgets import QApplication
from desktop_overlay import GazeClickBridge

app = QApplication(sys.argv)
bridge = GazeClickBridge()
try:
    bridge._handle_blink_click(10, 10)
    print("Direct call success")
except Exception as e:
    import traceback
    traceback.print_exc()
