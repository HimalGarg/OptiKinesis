"""Regression tests for safe OS-click fallback routing."""

import unittest
from unittest import mock

import desktop_overlay


class ClickFallbackTests(unittest.TestCase):
    def test_dispatch_uses_os_click_without_a_qt_bridge(self):
        with (
            mock.patch.object(desktop_overlay, "gaze_click_bridge", None),
            mock.patch.object(desktop_overlay.pyautogui, "click") as click,
        ):
            desktop_overlay.dispatch_blink_click(10, 20)
        click.assert_called_once_with(x=10, y=20)


if __name__ == "__main__":
    unittest.main()
