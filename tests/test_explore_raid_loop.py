"""探索-结界突破循环：全部用原生 pipeline 接线（轮数用 max_hit，阶段结束改 next）。"""
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]


class ExploreRaidLoopWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            cls.nodes.update(load_jsonc(path))
        cls.tasks = load_jsonc(ROOT / "assets/tasks/ExploreRaidLoop.json")
        cls.task = cls.tasks["task"][0]

    def test_registered_in_interface(self):
        self.assertIn("tasks/ExploreRaidLoop.json", load_jsonc(ROOT / "assets/interface.json")["import"])

    def test_phase_finishes_hand_over_instead_of_ending(self):
        override = self.task["pipeline_override"]
        self.assertEqual(override["RealmRaidFinish"]["next"], ["ExploreRaidLoopAfterRaid"])
        # Only the ticket threshold leads to the raid; any other end of exploration (stamina, cap) ends the loop.
        self.assertEqual(override["ExplorationTicketThreshold"]["next"][0], "ExploreRaidLoopTicketsReady")
        self.assertEqual(override["ExplorationFinish"]["next"], ["ExploreRaidLoopExploreShort"])
        self.assertEqual(self.nodes["ExploreRaidLoopExploreShort"]["next"][0], "ExploreRaidLoopDone")
        # Standalone tasks keep ending at their finish node.
        for name in ("RealmRaidFinish", "ExplorationFinish"):
            self.assertFalse(self.nodes[name].get("next"), name)
        self.assertEqual(self.nodes["ExplorationTicketThreshold"]["next"][0], "ExplorationFinish")

    def test_round_order_is_exploration_then_one_raid(self):
        self.assertEqual(self.nodes[self.task["entry"]]["next"][:2], ["ExploreRaidLoopRound", "ExploreRaidLoopDone"])
        self.assertEqual(self.nodes["ExploreRaidLoopRound"]["next"], ["ExplorationTask"])
        self.assertEqual(self.nodes["ExploreRaidLoopTicketsReady"]["next"], ["ExploreRaidLoopRaidSetup"])
        self.assertEqual(self.nodes["ExploreRaidLoopRaidSetup"]["next"], ["RealmRaidTask"])
        # When the round budget is spent the framework falls through to Done.
        self.assertEqual(self.nodes["ExploreRaidLoopAfterRaid"]["next"][:2], ["ExploreRaidLoopRound", "ExploreRaidLoopDone"])

    def test_exploration_stops_on_tickets_only_after_a_finished_chapter(self):
        threshold = self.task["pipeline_override"]["ExplorationTicketThreshold"]
        self.assertTrue(threshold["enabled"])
        # Entrance (after the boss exit) or map only: a chapter in progress is never cut short.
        self.assertEqual(threshold["recognition"]["param"]["any_of"], ["ExplorationTicketMap", "ExplorationTicketEntrance"])
        option = self.tasks["option"]["循环突破券阈值"]
        self.assertEqual(option["default_case"], "10张及以上")
        for case in option["cases"]:
            self.assertNotIn("ExplorationTicketThreshold", case["pipeline_override"], case["name"])
            self.assertEqual(set(case["pipeline_override"]), {"ExplorationTicketCountMap", "ExplorationTicketCountEntrance"})
        # The standalone option could disable the threshold and loop forever.
        self.assertNotIn("探索突破券阈值", self.task["option"])

    def test_default_ticket_threshold_is_more_than_nine(self):
        import re
        case = next(c for c in self.tasks["option"]["循环突破券阈值"]["cases"] if c["name"] == "10张及以上")
        pattern = case["pipeline_override"]["ExplorationTicketCountEntrance"]["recognition"]["param"]["expected"]
        for text, hit in (("9/30", False), ("0/30", False), ("10/30", True), ("19 / 30", True), ("30/30", True), ("3/30", False)):
            self.assertEqual(bool(re.fullmatch(pattern, text)), hit, text)

    def test_each_round_raids_one_board_with_four_retreats(self):
        options = self.task["option"]
        self.assertIn("循环每轮突破次数", options)
        self.assertIn("循环突破主动退出次数", options)
        # The standalone count (default 30) and retreat (default 0) would fight two different defaults.
        self.assertNotIn("结界突破成功次数", options)
        self.assertNotIn("结界突破主动退出次数", options)
        count = self.tasks["option"]["循环每轮突破次数"]
        self.assertEqual(count["inputs"][0]["default"], "9")
        self.assertEqual(count["pipeline_override"], {"RealmRaidTask": {"attach": {"target_count": "{作战次数}"}}})
        retreat = self.tasks["option"]["循环突破主动退出次数"]
        self.assertEqual(retreat["inputs"][0]["default"], "4")
        self.assertEqual(set(retreat["pipeline_override"]), {"RealmRaidRetreatBegin", "RealmRaidRetreatRetry"})

    def test_raid_budgets_reset_every_round(self):
        reset = self.nodes["ExploreRaidLoopRaidSetup"]["action"]["param"]["custom_action_param"]["nodes"]
        self.assertEqual(set(reset), {"RealmRaidRetreatBegin", "RealmRaidRetreatRetry", "RealmRaidSkipDefeat"})

    def test_loop_nodes_are_native_pipeline_only(self):
        loop = load_jsonc(ROOT / "assets/resource/base/pipeline/ExploreRaidLoop.json")
        for name, node in loop.items():
            action = node.get("action", "DoNothing")
            custom = action.get("param", {}).get("custom_action") if isinstance(action, dict) else None
            with self.subTest(node=name):
                # ResetCount is the project's existing hit-count reset; nothing loop-specific is custom.
                self.assertIn(custom, (None, "ResetCount"))
                recognition = node.get("recognition", "DirectHit")
                self.assertEqual(recognition if isinstance(recognition, str) else recognition.get("type"), "DirectHit")

    def test_round_count_option_sets_round_budget(self):
        option = self.tasks["option"]["循环轮数"]
        self.assertEqual(option["pipeline_override"], {"ExploreRaidLoopRound": {"max_hit": "{轮数}"}})
        self.assertIn("max_hit", self.nodes["ExploreRaidLoopRound"])


if __name__ == "__main__":
    unittest.main()
