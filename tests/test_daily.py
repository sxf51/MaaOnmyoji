import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action import daily
from custom.action.team import named, teammate
from custom.action.target_count import reset_state_for_test


class DailyFlowTest(unittest.TestCase):
    def context(self):
        context = Mock()
        context.tasker.stopping = False
        context.run_recognition.return_value = SimpleNamespace(hit=False, box=None)
        return context

    @patch.object(daily, "pause", return_value=True)
    @patch.object(daily, "act")
    @patch.object(daily, "screenshot", side_effect=["room", "result", "room"])
    def test_team_room_counts_only_after_settlement(self, screen, act, pause):
        context = self.context()
        context.run_recognition.side_effect = lambda node, image: SimpleNamespace(
            hit=node == "PageTeamRoom" and image == "room", box=[0, 0, 1, 1]
        )
        act.side_effect = lambda ctx, node, image: node == "DailyBattleResult" and image == "result"
        self.assertEqual(daily.battle(context, "TeamRoom"), "success")
        self.assertEqual(screen.call_count, 3)

    @patch.object(daily, "pause", return_value=True)
    @patch.object(daily, "act")
    @patch.object(daily, "screenshot", side_effect=["lost", "room"])
    def test_failure_is_not_counted_as_victory(self, screen, act, pause):
        context = self.context()
        context.run_recognition.side_effect = lambda node, image: SimpleNamespace(
            hit=(node, image) in {("DailyBattleFailed", "lost"), ("PageRaid", "room")},
            box=[0, 0, 1, 1]
        )
        act.side_effect = lambda ctx, node, image: node == "DailyBattleResult" and image == "lost"
        self.assertEqual(daily.battle(context, "Raid"), "failed")

    @patch.object(daily, "act")
    @patch.object(daily, "screenshot", return_value=object())
    def test_resource_shortage_never_clicks_purchase(self, screen, act):
        context = self.context()
        context.run_recognition.return_value = SimpleNamespace(hit=True, box=[0, 0, 1, 1])
        self.assertEqual(daily.battle(context, "Raid"), "exhausted")
        act.assert_not_called()

    @patch.object(daily, "pause", return_value=True)
    @patch.object(daily, "act", return_value=False)
    @patch.object(daily, "screenshot", return_value=object())
    def test_claim_page_requires_two_stable_empty_scans(self, screen, act, pause):
        context = self.context()
        context.run_recognition.return_value = SimpleNamespace(hit=True, box=[0, 0, 1, 1])
        self.assertTrue(daily.claim_page(context, "Mail", "Claim"))
        self.assertEqual(screen.call_count, 2)

    @patch.object(daily, "act", return_value=False)
    @patch.object(daily, "screenshot", return_value=object())
    def test_claim_does_not_succeed_on_unexpected_page(self, screen, act):
        self.assertFalse(daily.claim_page(self.context(), "Mail", "Claim"))

    @patch.object(daily, "act", return_value=True)
    @patch.object(daily, "screenshot", return_value=object())
    def test_repeated_popup_is_bounded(self, screen, act):
        self.assertFalse(daily.claim_page(self.context(), "Mail", "Claim"))
        self.assertEqual(screen.call_count, 40)

    def test_absent_card_is_not_a_target(self):
        context = self.context()
        self.assertFalse(daily.raid_card_available(context, object(), daily.RAID_CARDS[0]))
        self.assertEqual(context.run_recognition.call_count, 1)

    def test_broken_card_is_skipped(self):
        context = self.context()
        context.run_recognition.return_value = SimpleNamespace(hit=True, box=[0, 0, 1, 1])
        self.assertFalse(daily.raid_card_available(context, object(), daily.RAID_CARDS[0]))

    @patch.object(daily, "get_runtime_params", return_value={"target_count": 1})
    @patch.object(daily, "enter_chapter", return_value=True)
    @patch.object(daily, "screenshot", return_value=object())
    @patch.object(daily, "act", return_value=True)
    @patch.object(daily, "battle", return_value="success")
    def test_exploration_updates_shared_count_after_settlement(self, battle, act, screen, enter, params):
        context = self.context()
        context.run_action.return_value = SimpleNamespace(success=True)
        self.assertTrue(daily.ExploreBattles().run(context, Mock()).success)
        context.run_action.assert_called_once_with("ExplorationProgress")

    @patch.object(daily, "get_runtime_params", return_value={"target_count": 1})
    @patch.object(daily, "enter_chapter", return_value=True)
    @patch.object(daily, "screenshot", return_value=object())
    @patch.object(daily, "act", return_value=True)
    @patch.object(daily, "battle", return_value="failed")
    def test_exploration_failure_does_not_update_shared_count(self, battle, act, screen, enter, params):
        context = self.context()
        self.assertFalse(daily.ExploreBattles().run(context, Mock()).success)
        context.run_action.assert_not_called()

    def test_teammate_pattern_treats_name_as_literal(self):
        context = self.context()
        named(context, "Name", object(), "a.*[b]")
        override = context.run_recognition.call_args.args[2]
        self.assertEqual(override["Name"]["recognition"]["param"]["expected"], r"^a\.\*\[b\]$")

    def test_empty_teammate_is_rejected(self):
        reset_state_for_test()
        with self.assertRaises(ValueError):
            teammate()


if __name__ == "__main__":
    unittest.main()
