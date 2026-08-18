import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action.target_count import TargetCountState, _parse_params  # noqa: E402
from custom.reco.general import _union  # noqa: E402
from custom.sink.aspect_ratio import is_16_by_9  # noqa: E402


class TargetCountStateTest(unittest.TestCase):
    def test_progress_reaches_target(self) -> None:
        state = TargetCountState()
        state.reset(2)

        self.assertFalse(state.finished)
        state.progress()
        self.assertFalse(state.finished)
        state.progress()
        self.assertTrue(state.finished)

    def test_invalid_target_is_rejected(self) -> None:
        state = TargetCountState()
        with self.assertRaises(ValueError):
            state.reset(0)

    def test_unlimited_target_counts_without_finishing(self) -> None:
        state = TargetCountState()
        state.reset(-1)

        for _ in range(10):
            state.progress()

        self.assertEqual(state.completed_count, 10)
        self.assertFalse(state.finished)

    def test_negative_target_other_than_unlimited_is_rejected(self) -> None:
        state = TargetCountState()
        with self.assertRaises(ValueError):
            state.reset(-2)

    def test_params_accept_dict_and_json(self) -> None:
        self.assertEqual(_parse_params({"target_count": 3}), {"target_count": 3})
        self.assertEqual(_parse_params('{"target_count": 4}'), {"target_count": 4})

    def test_roi_union(self) -> None:
        self.assertEqual(_union([[10, 20, 30, 40], [30, 10, 50, 20]]), [10, 10, 70, 50])

    def test_aspect_ratio(self) -> None:
        self.assertTrue(is_16_by_9(1280, 720))
        self.assertFalse(is_16_by_9(1280, 800))


if __name__ == "__main__":
    unittest.main()
