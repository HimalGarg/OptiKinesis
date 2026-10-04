import unittest
from unittest import mock

import main


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = main.SETTINGS.copy()
        self.client = main.app.test_client()

    def tearDown(self):
        main.SETTINGS.clear()
        main.SETTINGS.update(self.original_settings)

    def test_fatigue_status_route_exists(self):
        response = self.client.get("/fatigue_status")
        self.assertEqual(response.status_code, 200)
        self.assertIn("is_fatigued", response.get_json())

    def test_settings_are_validated_and_clamped(self):
        with mock.patch.object(main, "save_settings", return_value=True):
            response = self.client.post(
                "/update_settings",
                json={
                    "cursor_speed": 99,
                    "cursor_scope": 0.1,
                    "blinks_to_click": 1,
                    "lock_delay": 0.1,
                    "overlay_enabled": "false",
                },
            )
        self.assertEqual(response.status_code, 200)
        settings = response.get_json()["settings"]
        self.assertEqual(settings["cursor_speed"], 1.5)
        self.assertEqual(settings["cursor_scope"], 0.35)
        self.assertEqual(settings["blinks_to_click"], 1)
        self.assertEqual(settings["lock_delay"], 0.2)
        self.assertFalse(settings["overlay_enabled"])

    def test_calibration_reports_not_ready_without_a_face(self):
        with mock.patch.object(main, "latest_raw_yaw", None), mock.patch.object(
            main, "latest_raw_pitch", None
        ):
            response = self.client.post("/calibrate")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
