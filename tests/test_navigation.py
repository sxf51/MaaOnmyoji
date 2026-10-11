import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom import navigation as nav


class NavigationTest(unittest.TestCase):
    def context(self):
        context = Mock()
        context.tasker.stopping = False
        return context

    def test_false_hit_with_box_is_not_a_match(self):
        self.assertFalse(nav.hit(SimpleNamespace(hit=False, box=[0, 0, 1, 1])))

    def test_overlay_precedes_underlying_page(self):
        context = self.context()
        context.run_recognition.side_effect = lambda name, image: SimpleNamespace(
            hit=name in {"PageRaid", "PageRaidTarget"}, box=[0, 0, 1, 1]
        )
        self.assertEqual(nav.detect_page(context, object()), "RaidTarget")

    def test_cross_task_route_stays_in_exploration(self):
        self.assertEqual(nav.first_edge("MitamaStage", ["Raid"]), "ReturnMainBackButton")
        self.assertEqual(nav.first_edge("Explore", ["Raid"]), "NavOpenRaid")
        self.assertEqual(nav.first_edge("Raid", ["MitamaStage"]), "NavClose")

    @patch.object(nav, "act")
    @patch.object(nav, "detect_page", return_value="Raid")
    @patch.object(nav, "screenshot", return_value=object())
    def test_already_at_destination_never_clicks(self, screen, page, act):
        self.assertTrue(nav.navigate(self.context(), ["Raid"]))
        act.assert_not_called()

    @patch.object(nav, "act")
    @patch.object(nav, "detect_page", return_value="Battle")
    @patch.object(nav, "screenshot", return_value=object())
    def test_battle_never_clicks_back(self, screen, page, act):
        self.assertFalse(nav.navigate(self.context(), ["Home"]))
        act.assert_not_called()

    @patch.object(nav, "act", return_value=True)
    @patch.object(nav, "detect_page", side_effect=["ChapterField", "ExitExplore", "Chapter", "Explore", "Raid"])
    @patch.object(nav, "screenshot", side_effect=[object() for _ in range(5)])
    def test_exploration_exit_confirmation_and_direct_raid(self, screen, page, act):
        self.assertTrue(nav.navigate(self.context(), ["Raid"]))
        self.assertEqual([call.args[1] for call in act.call_args_list], [
            "ReturnMainBackButton", "NavConfirmExitExplore", "ReturnMainBackButton", "NavOpenRaid"
        ])
        self.assertEqual(screen.call_count, 5)

    @patch.object(nav, "act", side_effect=[False, True])
    @patch.object(nav, "detect_page", side_effect=["Explore", "Home"])
    @patch.object(nav, "screenshot", return_value=object())
    def test_missing_home_icon_falls_back_to_back(self, screen, page, act):
        self.assertTrue(nav.navigate(self.context(), ["Home"]))
        self.assertEqual([c.args[1] for c in act.call_args_list], ["ReturnMainHomeButton", "ReturnMainBackButton"])

    @patch.object(nav, "act", return_value=True)
    @patch.object(nav, "detect_page", return_value="Explore")
    @patch.object(nav, "screenshot", return_value=object())
    def test_stuck_screen_stops_after_four_actions(self, screen, page, act):
        self.assertFalse(nav.navigate(self.context(), ["Raid"]))
        self.assertEqual(act.call_count, 4)

    @patch.object(nav, "screenshot")
    def test_stopping_prevents_screenshots_and_actions(self, screen):
        context = self.context()
        context.tasker.stopping = True
        self.assertFalse(nav.navigate(context, ["Home"]))
        self.assertFalse(nav.act(context, "NavBack", object()))
        screen.assert_not_called()
        context.run_action.assert_not_called()

    @patch.object(nav, "screenshot")
    def test_invalid_target_fails_before_controller_access(self, screen):
        self.assertFalse(nav.navigate(self.context(), ["typo"]))
        self.assertFalse(nav.navigate(self.context(), []))
        screen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
