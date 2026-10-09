import random
import unittest
from unittest import mock

from head_calibration import (
    POINT_ORDER,
    CalibrationError,
    CalibrationSession,
    HeadCalibration,
    StableSampler,
    default_targets,
)


def affine_user(nx, ny):
    """A user whose head angles are an affine function of screen position.

    Includes a left-right mirror and some shear between the axes, which the
    calibration must learn without being told.
    """
    yaw = 180.0 - 40.0 * (nx - 0.5) + 3.0 * (ny - 0.5)
    pitch = 182.0 + 4.0 * (nx - 0.5) + 28.0 * (ny - 0.5)
    return yaw, pitch


def readings_for(user, targets=None):
    targets = targets or default_targets()
    return {name: user(*targets[name]) for name in POINT_ORDER}


class HeadCalibrationMapTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected, places=6):
        self.assertAlmostEqual(actual[0], expected[0], places=places)
        self.assertAlmostEqual(actual[1], expected[1], places=places)

    def test_each_target_maps_exactly(self):
        uneven = {
            "center": (181.0, 179.0),
            "top_left": (172.0, 171.0),
            "top_right": (196.0, 173.5),
            "bottom_right": (194.0, 190.0),
            "bottom_left": (174.0, 186.0),
        }
        model = HeadCalibration(uneven)
        for name, target in default_targets().items():
            self.assertPointAlmostEqual(model.map(*uneven[name]), target)

    def test_affine_head_motion_is_recovered_everywhere(self):
        model = HeadCalibration(readings_for(affine_user))
        rng = random.Random(7)
        for _ in range(200):
            nx, ny = rng.uniform(-0.1, 1.1), rng.uniform(-0.1, 1.1)
            self.assertPointAlmostEqual(model.map(*affine_user(nx, ny)), (nx, ny))

    def test_uneven_reach_is_respected(self):
        # The user can turn 8 degrees toward the left targets but 20 toward the right.
        readings = {
            "center": (180.0, 180.0),
            "top_left": (172.0, 172.0),
            "bottom_left": (172.0, 188.0),
            "top_right": (200.0, 172.0),
            "bottom_right": (200.0, 188.0),
        }
        model = HeadCalibration(readings)
        self.assertAlmostEqual(model.map(176.0, 180.0)[0], 0.3)
        self.assertAlmostEqual(model.map(190.0, 180.0)[0], 0.7)

    def test_map_is_continuous_across_triangle_edges(self):
        model = HeadCalibration(readings_for(affine_user))
        center = model.angles["center"]
        corner = model.angles["top_right"]
        for fraction in (0.3, 1.0, 1.4):
            on_edge = (
                center[0] + (corner[0] - center[0]) * fraction,
                center[1] + (corner[1] - center[1]) * fraction,
            )
            left_side = model.map(on_edge[0], on_edge[1] - 1e-4)
            right_side = model.map(on_edge[0], on_edge[1] + 1e-4)
            self.assertPointAlmostEqual(left_side, right_side, places=3)

    def test_mirrored_camera_is_accepted(self):
        mirrored = {name: (360.0 - yaw, pitch) for name, (yaw, pitch) in readings_for(affine_user).items()}
        model = HeadCalibration(mirrored)
        self.assertPointAlmostEqual(model.map(*mirrored["top_left"]), default_targets()["top_left"])

    def test_too_small_movement_is_rejected(self):
        tiny = {
            "center": (180.0, 180.0),
            "top_left": (178.0, 178.0),
            "top_right": (182.0, 178.0),
            "bottom_right": (182.0, 182.0),
            "bottom_left": (178.0, 182.0),
        }
        with self.assertRaises(CalibrationError):
            HeadCalibration(tiny)

    def test_crossed_corners_are_rejected(self):
        readings = readings_for(affine_user)
        readings["top_left"], readings["top_right"] = readings["top_right"], readings["top_left"]
        with self.assertRaises(CalibrationError):
            HeadCalibration(readings)

    def test_recentering_shifts_the_whole_map(self):
        model = HeadCalibration(readings_for(affine_user))
        shifted = model.recentered(185.0, 175.0)
        self.assertPointAlmostEqual(shifted.map(185.0, 175.0), (0.5, 0.5))
        offset_corner = model.angles["top_left"]
        moved = (offset_corner[0] + 185.0 - model.angles["center"][0],
                 offset_corner[1] + 175.0 - model.angles["center"][1])
        self.assertPointAlmostEqual(shifted.map(*moved), default_targets()["top_left"])

    def test_settings_round_trip(self):
        model = HeadCalibration(readings_for(affine_user))
        restored = HeadCalibration.from_dict(model.to_dict())
        self.assertIsNotNone(restored)
        self.assertPointAlmostEqual(restored.map(*affine_user(0.25, 0.8)), (0.25, 0.8), places=3)

    def test_bad_saved_data_is_ignored(self):
        self.assertIsNone(HeadCalibration.from_dict(None))
        self.assertIsNone(HeadCalibration.from_dict({"version": 1, "points": {}}))
        self.assertIsNone(HeadCalibration.from_dict({"version": 99}))


class StableSamplerTests(unittest.TestCase):
    def test_steady_head_completes_after_capture_time(self):
        sampler = StableSampler(capture_s=1.0, max_spread_deg=1.0)
        t = 0.0
        while t < 0.9:
            sampler.add(t, 180.0 + 0.2 * (t % 0.2), 180.0)
            t += 1 / 30
        self.assertFalse(sampler.done)
        while t < 1.1:
            sampler.add(t, 180.1, 180.0)
            t += 1 / 30
        self.assertTrue(sampler.done)
        self.assertAlmostEqual(sampler.reading()[1], 180.0)

    def test_moving_head_never_completes(self):
        sampler = StableSampler(capture_s=1.0, max_spread_deg=1.0)
        t = 0.0
        while t < 3.0:
            sampler.add(t, 170.0 + 10.0 * t, 180.0)
            t += 1 / 30
        self.assertFalse(sampler.done)

    def test_losing_the_face_restarts_the_count(self):
        sampler = StableSampler(capture_s=1.0, max_spread_deg=1.0, max_gap_s=0.4)
        for i in range(25):
            sampler.add(i / 30, 180.0, 180.0)
        sampler.note_gap(1.5)
        self.assertEqual(sampler.progress, 0.0)


class CalibrationSessionTests(unittest.TestCase):
    def run_session(self, pose_for, seconds=40.0):
        session = CalibrationSession(point_timeout_s=8.0)
        now = 0.0
        stamp = 0.0
        session.start(now)
        while not session.finished and now < seconds:
            now += 1 / 30
            stamp += 1 / 30
            session.update(now, pose_for(session, now, stamp))
        return session

    def test_simulated_user_completes_calibration(self):
        rng = random.Random(3)

        def pose(session, now, stamp):
            nx, ny = session.target_position(now)
            yaw, pitch = affine_user(nx, ny)
            return yaw + rng.uniform(-0.2, 0.2), pitch + rng.uniform(-0.2, 0.2), stamp

        session = self.run_session(pose)
        self.assertEqual(session.phase, CalibrationSession.DONE, session.error)
        for name, target in default_targets().items():
            mapped = session.result.map(*affine_user(*target))
            self.assertAlmostEqual(mapped[0], target[0], delta=0.02)
            self.assertAlmostEqual(mapped[1], target[1], delta=0.02)

    def test_missing_face_fails_with_clear_message(self):
        session = self.run_session(lambda session, now, stamp: None)
        self.assertEqual(session.phase, CalibrationSession.FAILED)
        self.assertIn("Face not detected", session.error)

    def test_cancel_stops_the_session(self):
        session = CalibrationSession()
        session.start(0.0)
        session.cancel()
        self.assertTrue(session.finished)
        self.assertIsNone(session.result)


class TrackerIntegrationTests(unittest.TestCase):
    """How main.py uses the calibration: cursor mapping, re-center and saving."""

    def setUp(self):
        import main

        self.main = main
        self.original_settings = main.SETTINGS.copy()
        self.model = HeadCalibration(readings_for(affine_user))
        patches = [
            mock.patch.object(main, "head_calibration_model", self.model),
            mock.patch.object(main, "save_settings", return_value=True),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        main.calibration_active.clear()

    def tearDown(self):
        self.main.SETTINGS.clear()
        self.main.SETTINGS.update(self.original_settings)
        self.main.calibration_active.clear()

    def test_calibrated_map_drives_cursor_position(self):
        x, y = self.main.map_pose_to_screen(*affine_user(0.25, 0.75))
        self.assertAlmostEqual(x, 0.25 * self.main.SCREEN_W, delta=0.5)
        self.assertAlmostEqual(y, 0.75 * self.main.SCREEN_H, delta=0.5)

    def test_recenter_shifts_saved_calibration(self):
        with mock.patch.object(self.main, "latest_raw_yaw", 186.0), mock.patch.object(
            self.main, "latest_raw_pitch", 177.0
        ):
            self.assertTrue(self.main.calibrate_current_pose())
        x, y = self.main.map_pose_to_screen(186.0, 177.0)
        self.assertAlmostEqual(x, 0.5 * self.main.SCREEN_W, delta=0.5)
        self.assertAlmostEqual(y, 0.5 * self.main.SCREEN_H, delta=0.5)
        self.assertEqual(self.main.SETTINGS["head_calibration"]["points"]["center"], [186.0, 177.0])

    def test_finished_calibration_is_applied_and_tracking_resumes(self):
        new_model = HeadCalibration(readings_for(lambda nx, ny: affine_user(1 - nx, ny)))
        self.main.begin_head_calibration()
        self.assertTrue(self.main.calibration_active.is_set())
        self.assertTrue(self.main.finish_head_calibration(new_model))
        self.assertFalse(self.main.calibration_active.is_set())
        self.assertIs(self.main.head_calibration_model, new_model)

    def test_cancelled_calibration_keeps_previous_map(self):
        self.main.begin_head_calibration()
        self.assertFalse(self.main.finish_head_calibration(None))
        self.assertFalse(self.main.calibration_active.is_set())
        self.assertIs(self.main.head_calibration_model, self.model)


if __name__ == "__main__":
    unittest.main()
