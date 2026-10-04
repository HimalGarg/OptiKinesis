import unittest

from blink_detector import DeliberateBlinkDetector


class DeliberateBlinkDetectorTests(unittest.TestCase):
    def make_detector(self, **overrides):
        settings = {
            "min_duration": 0.15,
            "max_duration": 1.0,
            "blinks_required": 2,
            "blink_window": 1.5,
            "click_cooldown": 0.5,
        }
        settings.update(overrides)
        return DeliberateBlinkDetector(**settings)

    def blink(self, detector, start, duration=0.2):
        detector.update(True, start)
        return detector.update(False, start + duration)

    def test_short_camera_noise_is_ignored(self):
        update = self.blink(self.make_detector(), 1.0, duration=0.05)
        self.assertFalse(update.blink_completed)
        self.assertFalse(update.click_triggered)

    def test_two_deliberate_blinks_trigger_one_click(self):
        detector = self.make_detector()
        first = self.blink(detector, 1.0)
        second = self.blink(detector, 1.6)
        self.assertTrue(first.blink_completed)
        self.assertFalse(first.click_triggered)
        self.assertTrue(second.click_triggered)
        self.assertEqual(second.blink_count, 0)

    def test_blinks_outside_window_do_not_combine(self):
        detector = self.make_detector(blink_window=0.5)
        self.blink(detector, 1.0)
        second = self.blink(detector, 2.0)
        self.assertFalse(second.click_triggered)
        self.assertEqual(second.blink_count, 1)

    def test_long_eye_closure_never_clicks(self):
        detector = self.make_detector(blinks_required=1)
        update = self.blink(detector, 1.0, duration=2.0)
        self.assertFalse(update.blink_completed)
        self.assertFalse(update.click_triggered)

    def test_live_configuration_can_enable_single_blink_mode(self):
        detector = self.make_detector()
        detector.configure(blinks_required=1)
        self.assertTrue(self.blink(detector, 1.0).click_triggered)

    def test_blinks_during_post_click_lock_are_discarded(self):
        detector = self.make_detector(blinks_required=1, click_cooldown=1.0)
        self.assertTrue(self.blink(detector, 1.0).click_triggered)
        locked = self.blink(detector, 1.4)
        self.assertFalse(locked.click_triggered)
        self.assertEqual(locked.blink_count, 0)


if __name__ == "__main__":
    unittest.main()
