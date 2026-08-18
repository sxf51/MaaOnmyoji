import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from cBezier import bezier_points, swipe_control_points  # noqa: E402
from custom.action.bezier_swipe import build_track  # noqa: E402


class BezierSwipeTest(unittest.TestCase):
    def test_track_keeps_endpoints_and_segment_count(self) -> None:
        points = bezier_points([[10, 20], [50, 80], [100, 20]], segments=10)
        self.assertEqual(points[0], (10, 20))
        self.assertEqual(points[-1], (100, 20))
        self.assertEqual(len(points), 11)
        self.assertGreater(max(y for _, y in points), 20)

    def test_vertical_swipe_is_supported(self) -> None:
        controls = swipe_control_points([100, 600], [100, 100], deviation=25)
        points = bezier_points(controls, segments=12)
        self.assertEqual(points[0], (100, 600))
        self.assertEqual(points[-1], (100, 100))
        self.assertNotEqual(max(x for x, _ in points), 100)

    def test_action_params_allow_explicit_control_points(self) -> None:
        track, duration, contact, pressure = build_track(
            {
                "begin": [100, 600],
                "end": [900, 200],
                "control_points": [[300, 400], [700, 100]],
                "segments": 16,
                "duration": 640,
            }
        )
        self.assertEqual((len(track), duration, contact, pressure), (17, 640, 0, 1))

    def test_endpoint_can_be_random_on_a_line(self) -> None:
        track, _, _, _ = build_track(
            {
                "begin": {"line": [[100, 600], [300, 500]]},
                "end": [900, 200],
                "segments": 10,
                "random_seed": 7,
            }
        )
        x, y = track[0]
        self.assertGreaterEqual(x, 100)
        self.assertLessEqual(x, 300)
        # 该线段满足 y = 650 - x / 2，允许整数取整带来的 1 px 误差。
        self.assertLessEqual(abs(y - (650 - x / 2)), 1)

    def test_fixed_endpoint_format_remains_supported(self) -> None:
        track, _, _, _ = build_track({"begin": [10, 20], "end": [300, 400]})
        self.assertEqual(track[0], (10, 20))
        self.assertEqual(track[-1], (300, 400))

    def test_endpoint_can_be_random_in_a_region(self) -> None:
        track, _, _, _ = build_track(
            {
                "begin": [100, 500, 80, 40],
                "end": [800, 100, 100, 60],
                "random_seed": 11,
            }
        )
        begin_x, begin_y = track[0]
        end_x, end_y = track[-1]
        self.assertTrue(100 <= begin_x <= 180 and 500 <= begin_y <= 540)
        self.assertTrue(800 <= end_x <= 900 and 100 <= end_y <= 160)

    def test_region_rejects_negative_size(self) -> None:
        with self.assertRaises(ValueError):
            build_track({"begin": [10, 20, -1, 30], "end": [300, 400]})

    def test_duration_can_have_random_extension(self) -> None:
        _, duration, _, _ = build_track(
            {
                "begin": [10, 20],
                "end": [300, 400],
                "duration": 600,
                "duration_random": 200,
                "random_seed": 17,
            }
        )
        self.assertGreaterEqual(duration, 600)
        self.assertLessEqual(duration, 800)

    def test_duration_random_defaults_to_zero(self) -> None:
        _, duration, _, _ = build_track(
            {"begin": [10, 20], "end": [300, 400], "duration": 600}
        )
        self.assertEqual(duration, 600)

    def test_duration_random_rejects_negative_value(self) -> None:
        with self.assertRaises(ValueError):
            build_track(
                {
                    "begin": [10, 20],
                    "end": [300, 400],
                    "duration_random": -1,
                }
            )

    def test_equal_endpoints_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_track({"begin": [1, 1], "end": [1, 1]})


if __name__ == "__main__":
    unittest.main()
