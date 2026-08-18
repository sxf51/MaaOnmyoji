import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action.general import (  # noqa: E402
    SubTask,
    clear_subtask_states,
    reset_subtask_states_for_test,
)


def make_argv() -> Mock:
    task_detail = Mock(task_id=42)
    return Mock(
        task_detail=task_detail,
        node_name="MitamaPreparation",
        custom_action_param={
            "sub": [
                "MitamaEnableBuff",
                "MitamaCloseBuffPanel",
                "MitamaOpenPreset",
            ],
            "completion_next": ["MitamaPreparationDestination"],
            "timeout": 1000,
        },
    )


class SubTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_subtask_states_for_test()

    def test_enabled_sub_nodes_become_ordered_next_candidates(self) -> None:
        context = Mock()
        enabled = {
            "MitamaEnableBuff": True,
            "MitamaCloseBuffPanel": False,
            "MitamaOpenPreset": True,
        }
        context.get_node_data.side_effect = lambda name: {"enabled": enabled[name]}
        context.override_next.return_value = True

        result = SubTask().run(context, make_argv())

        self.assertTrue(result.success)
        context.override_next.assert_called_once_with(
            "MitamaPreparation",
            ["MitamaEnableBuff", "MitamaOpenPreset"],
        )
        context.run_task.assert_not_called()

    def test_all_disabled_uses_normal_completion_next(self) -> None:
        context = Mock()
        context.get_node_data.return_value = {"enabled": False}
        context.override_next.return_value = True

        result = SubTask().run(context, make_argv())

        self.assertTrue(result.success)
        context.override_next.assert_called_once_with(
            "MitamaPreparation", ["MitamaPreparationDestination"]
        )

    def test_reentry_uses_explicit_completion_next(self) -> None:
        context = Mock()
        enabled = {"MitamaEnableBuff": True}

        def node_data(name: str) -> dict:
            return {"enabled": enabled.get(name, False)}

        context.get_node_data.side_effect = node_data
        context.override_next.return_value = True
        argv = make_argv()

        self.assertTrue(SubTask().run(context, argv).success)
        enabled["MitamaEnableBuff"] = False
        self.assertTrue(SubTask().run(context, argv).success)

        self.assertEqual(
            context.override_next.call_args_list[-1].args,
            ("MitamaPreparation", ["MitamaPreparationDestination"]),
        )

    @patch("custom.action.general.time.monotonic", side_effect=[10.0, 11.0])
    def test_reentry_stops_after_shared_timeout(self, monotonic: Mock) -> None:
        del monotonic
        context = Mock()
        context.get_node_data.return_value = {"enabled": True}
        context.override_next.return_value = True
        argv = make_argv()
        argv.custom_action_param["timeout"] = 500

        self.assertTrue(SubTask().run(context, argv).success)
        self.assertFalse(SubTask().run(context, argv).success)
        self.assertEqual(context.override_next.call_count, 1)

    @patch("custom.action.general.time.monotonic", return_value=10.0)
    def test_task_end_cleanup_discards_saved_state(self, monotonic: Mock) -> None:
        del monotonic
        context = Mock()
        context.get_node_data.return_value = {"enabled": True}
        context.override_next.return_value = True
        argv = make_argv()

        self.assertTrue(SubTask().run(context, argv).success)
        clear_subtask_states(42)

        context.get_node_data.reset_mock()
        self.assertTrue(SubTask().run(context, argv).success)
        self.assertEqual(context.override_next.call_count, 2)

    def test_missing_sub_node_fails(self) -> None:
        context = Mock()
        context.get_node_data.return_value = None

        result = SubTask().run(context, make_argv())

        self.assertFalse(result.success)
        context.override_next.assert_not_called()

    def test_missing_completion_next_fails(self) -> None:
        context = Mock()
        argv = make_argv()
        del argv.custom_action_param["completion_next"]

        result = SubTask().run(context, argv)

        self.assertFalse(result.success)
        context.get_node_data.assert_not_called()

    @patch("custom.action.general.time.monotonic", side_effect=[10.0, 11.0])
    def test_all_disabled_completes_even_after_deadline(self, monotonic: Mock) -> None:
        del monotonic
        context = Mock()
        enabled = {"MitamaEnableBuff": True}
        context.get_node_data.side_effect = lambda name: {
            "enabled": enabled.get(name, False)
        }
        context.override_next.return_value = True
        argv = make_argv()
        argv.custom_action_param["timeout"] = 500

        self.assertTrue(SubTask().run(context, argv).success)
        enabled["MitamaEnableBuff"] = False
        self.assertTrue(SubTask().run(context, argv).success)
        self.assertEqual(
            context.override_next.call_args_list[-1].args,
            ("MitamaPreparation", ["MitamaPreparationDestination"]),
        )


if __name__ == "__main__":
    unittest.main()
