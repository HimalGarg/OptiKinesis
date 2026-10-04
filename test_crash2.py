"""Regression tests for overlay callback dispatch."""

import unittest
from unittest import mock

import desktop_overlay


class OverlayCallbackTests(unittest.TestCase):
    def test_action_callback_receives_action_and_text(self):
        callback = mock.Mock(return_value={"status": "ok"})
        with mock.patch.object(desktop_overlay, "_action_executor", callback):
            result = desktop_overlay._perform_action("google", "accessibility")
        callback.assert_called_once_with("google", "accessibility")
        self.assertEqual(result, {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
