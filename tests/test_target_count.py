import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action.target_count import (  # noqa: E402
    TargetCountDetermine, TargetCountProgress, TargetCountState, _parse_params, reset_state_for_test,
)
from custom.action import target_count  # noqa: E402
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

    def test_target_rejects_truncated_or_non_numeric_values(self) -> None:
        for value in (True, False, 1.5, -1.5, float("inf"), "1.5", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TargetCountState().reset(value)

    def test_progress_rejects_invalid_count_without_changing_state(self) -> None:
        state = TargetCountState()
        state.reset("3")
        for value in (True, 1.5, 0, -1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                state.progress(value)
            self.assertEqual(state.completed_count, 0)
        state.progress("2")
        self.assertEqual(state.completed_count, 2)

    def test_params_accept_dict_and_json(self) -> None:
        self.assertEqual(_parse_params({"target_count": 3}), {"target_count": 3})
        self.assertEqual(_parse_params('{"target_count": 4}'), {"target_count": 4})

    def test_roi_union(self) -> None:
        self.assertEqual(_union([[10, 20, 30, 40], [30, 10, 50, 20]]), [10, 10, 70, 50])

    def test_aspect_ratio(self) -> None:
        self.assertTrue(is_16_by_9(1280, 720))
        self.assertFalse(is_16_by_9(1280, 800))


class TargetCountProgressRouteTest(unittest.TestCase):
    def tearDown(self):
        reset_state_for_test()

    def advance(self, context, params):
        return TargetCountProgress().run(context, SimpleNamespace(node_name="AfterResult", custom_action_param=params))

    def test_continue_then_finish_count_each_settlement_once(self):
        reset_state_for_test()
        target_count._state.reset(2)
        context = Mock()
        params = {"continue_node": "Continue", "finish_node": "Finish"}
        self.assertTrue(self.advance(context, params).success)
        context.override_next.assert_called_with("AfterResult", ["[JumpBack]CommonBountyInterrupt", "Continue"])
        self.assertEqual(target_count._state.completed_count, 1)
        self.assertTrue(self.advance(context, params).success)
        context.override_next.assert_called_with("AfterResult", ["[JumpBack]CommonBountyInterrupt", "Finish"])
        self.assertEqual(target_count._state.completed_count, 2)

    def test_unlimited_run_keeps_continuing(self):
        reset_state_for_test()
        target_count._state.reset(-1)
        context = Mock()
        self.assertTrue(self.advance(context, {"continue_node": "Continue", "finish_node": "Finish"}).success)
        context.override_next.assert_called_once_with("AfterResult", ["[JumpBack]CommonBountyInterrupt", "Continue"])

    def test_invalid_or_incomplete_route_does_not_increment(self):
        for params in ({"continue_node": "Continue"}, {"finish_node": "Finish"},
                       {"continue_node": "", "finish_node": "Finish"},
                       {"continue_node": "Continue", "finish_node": None}):
            with self.subTest(params=params):
                reset_state_for_test()
                target_count._state.reset(2)
                context = Mock()
                self.assertFalse(self.advance(context, params).success)
                self.assertEqual(target_count._state.completed_count, 0)
                context.override_next.assert_not_called()

    def test_route_failure_stops_after_recording_settlement(self):
        reset_state_for_test()
        target_count._state.reset(2)
        context = Mock()
        context.override_next.return_value = False
        self.assertFalse(self.advance(context, {"continue_node": "Continue", "finish_node": "Finish"}).success)
        self.assertEqual(target_count._state.completed_count, 1)

    def test_existing_progress_only_call_does_not_change_routing(self):
        reset_state_for_test()
        target_count._state.reset(2)
        context = Mock()
        self.assertTrue(self.advance(context, {"count": 1}).success)
        context.override_next.assert_not_called()


class TargetCountRouteTest(unittest.TestCase):
    def setUp(self):
        reset_state_for_test()
        target_count._state.reset(1)
        self.params = {"decision_node": "Decision", "continue_node": "Continue", "finish_node": "Finish"}

    def tearDown(self):
        reset_state_for_test()

    def test_failed_route_override_is_not_reported_as_success(self):
        context = Mock()
        context.override_next.return_value = False
        result = TargetCountDetermine().run(context, SimpleNamespace(custom_action_param=self.params))
        self.assertFalse(result.success)
        context.override_next.assert_called_once_with("Decision", ["[JumpBack]CommonBountyInterrupt", "Continue"])

    def test_invalid_node_name_is_rejected_before_override(self):
        for value in (None, "", " ", 42):
            with self.subTest(value=value):
                context = Mock()
                params = {**self.params, "finish_node": value}
                result = TargetCountDetermine().run(context, SimpleNamespace(custom_action_param=params))
                self.assertFalse(result.success)
                context.override_next.assert_not_called()


if __name__ == "__main__":
    unittest.main()
