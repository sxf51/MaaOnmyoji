import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.action.team_config import ConfigureTeamRecognition, teammate
from custom.action.target_count import reset_state_for_test


class TeamConfigTest(unittest.TestCase):
    @patch("custom.action.team_config.get_runtime_params", return_value={"teammate": "  队友.+[1]  "})
    def test_exact_nickname_override_has_no_screen_input(self, runtime):
        context = Mock()
        context.override_pipeline.return_value = True
        result = ConfigureTeamRecognition().run(context, SimpleNamespace())
        self.assertTrue(result.success)
        overrides = context.override_pipeline.call_args.args[0]
        self.assertEqual(set(overrides), {"MitamaTeammateName", "MitamaRoomTeammate", "MitamaInvitationName"})
        for node in overrides.values():
            self.assertEqual(node["recognition"]["param"]["expected"], r"^队友\.\+\[1\]$")
        context.run_recognition.assert_not_called()
        context.tasker.controller.post_touch_down.assert_not_called()

    @patch("custom.action.team_config.get_runtime_params", return_value={"teammate": " "})
    def test_blank_nickname_rejected_without_override(self, runtime):
        context = Mock()
        self.assertFalse(ConfigureTeamRecognition().run(context, SimpleNamespace()).success)
        context.override_pipeline.assert_not_called()

    def test_missing_nickname_rejected(self):
        reset_state_for_test()
        with self.assertRaises(ValueError):
            teammate()
