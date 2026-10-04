import unittest

from fatigue_monitor import FatigueMonitor


class FatigueMonitorTests(unittest.TestCase):
    def test_threshold_enters_fatigue_and_reduces_sensitivity(self):
        config = {
            "window_size": 10,
            "blink_threshold": 3,
            "min_blinks_baseline": 2,
            "cooldown_period": 5,
            "sensitivity_reduction": 0.5,
        }
        monitor = FatigueMonitor(config)
        self.assertFalse(monitor.record_blink())
        self.assertFalse(monitor.record_blink())
        self.assertTrue(monitor.record_blink())
        self.assertTrue(monitor.is_fatigued)
        self.assertEqual(monitor.get_adjusted_sensitivity(0.2), 0.1)

    def test_reset_clears_active_state(self):
        monitor = FatigueMonitor()
        monitor.record_blink()
        monitor.reset()
        self.assertFalse(monitor.is_fatigued)
        self.assertEqual(len(monitor.blink_timestamps), 0)


if __name__ == "__main__":
    unittest.main()
