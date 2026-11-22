"""探索：首领优先级、整章计数与可选的难度/锁定/预设。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from tools.validate_schema import load_jsonc  # noqa: E402


class ExplorationPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            cls.nodes.update(load_jsonc(path))
        cls.task = load_jsonc(ROOT / "assets/tasks/Exploration.json")

    def test_field_priority_is_boss_then_plain_then_swipes(self):
        for name in ("ExplorationFieldReady", "ExplorationScan"):
            routes = self.nodes[name]["next"]
            order = ["ExplorationUnlockTeam", "ExplorationPreset", "ExplorationFindBoss",
                     "ExplorationFindEnemy", "ExplorationEnemySwipe", "ExplorationEnemySwipeBack", "ExplorationReenter"]
            positions = [next(i for i, r in enumerate(routes) if r.replace("[JumpBack]", "") == n) for n in order]
            self.assertEqual(positions, sorted(positions), name)

    def test_only_the_boss_win_counts(self):
        self.assertEqual(self.nodes["ExplorationFindBoss"]["next"], ["ExplorationBossBattle"])
        self.assertIn("ExplorationBossExited", self.nodes["ExplorationBossResult"]["next"])
        self.assertEqual(self.nodes["ExplorationBossExited"]["next"][0], "ExplorationProgress")
        self.assertNotIn("ExplorationProgress", self.nodes["ExplorationPostBattleRewards"]["next"])
        self.assertEqual(self.nodes["ExplorationNormalWin"]["next"], ["ExplorationRun"])
        reset = self.nodes["ExplorationBossExited"]["action"]["param"]["custom_action_param"]["nodes"]
        self.assertIn("ExplorationNormalWin", reset)

    def test_swipes_cover_both_directions(self):
        right = self.nodes["ExplorationEnemySwipe"]["action"]["param"]["custom_action_param"]
        left = self.nodes["ExplorationEnemySwipeBack"]["action"]["param"]["custom_action_param"]
        self.assertGreater(right["begin"][0], right["end"][0])
        self.assertLess(left["begin"][0], left["end"][0])
        self.assertEqual(right["begin"][1], left["begin"][1])
        reset = self.nodes["ExplorationFieldReady"]["action"]["param"]["custom_action_param"]["nodes"]
        self.assertTrue({"ExplorationEnemySwipe", "ExplorationEnemySwipeBack"} <= set(reset))

    def test_preset_runs_once_per_exploration_phase_without_out_button(self):
        preset = self.nodes["ExplorationPreset"]
        self.assertEqual((preset["enabled"], preset["max_hit"]), (False, 1))
        self.assertEqual(preset["anchor"], {"TeamPresetReturn": "ExplorationPresetDone"})
        self.assertIn("ExplorationPreset", self.nodes["ExplorationSetup"]["action"]["param"]["custom_action_param"]["nodes"])
        layout = preset["action"]["param"]["custom_action_param"]
        self.assertEqual(layout["TeamPresetInUse"]["next"][0], "TeamPresetClose")
        self.assertNotIn("TeamPresetOut", str(layout))
        self.assertIn("ExplorationPresetConfirm", layout["TeamPresetApply"]["next"])

    def test_difficulty_must_be_selected_before_entering(self):
        enter = self.nodes["ExplorationEnterChapter"]["recognition"]["param"]["all_of"]
        self.assertEqual(enter[0], "ExplorationDifficultySelected")
        cases = {c["name"]: c for c in self.task["option"]["探索难度"]["cases"]}
        self.assertEqual(set(cases), {"困难", "普通", "不切换"})
        self.assertEqual(cases["不切换"]["pipeline_override"]["ExplorationDifficultySelected"]["recognition"]["type"], "DirectHit")
        rows = {name: cases[name]["pipeline_override"]["ExplorationDifficultySelected"]["recognition"]["param"]["roi"][1]
                for name in ("困难", "普通")}
        self.assertLess(rows["普通"], rows["困难"])

    def test_task_exposes_new_options_and_count_is_unified(self):
        options = self.task["task"][0]["option"]
        for name in ("探索作战次数", "探索章节", "探索难度", "探索锁定阵容", "探索更换队伍预设", "探索突破券阈值"):
            self.assertIn(name, options)
        count = self.task["option"]["探索作战次数"]
        self.assertEqual(count["pipeline_override"], {"ExplorationTask": {"attach": {"target_count": "{作战次数}"}}})


class UnifiedBattleCountTest(unittest.TestCase):
    def test_every_task_uses_the_same_count_input(self):
        seen = 0
        for path in (ROOT / "assets/tasks").glob("*.json"):
            doc = load_jsonc(path)
            for name, option in doc.get("option", {}).items():
                inputs = option.get("inputs", [])
                if not any(i.get("name") == "作战次数" for i in inputs):
                    continue
                seen += 1
                with self.subTest(option=name):
                    # Loop options carry their own labels so two counts in one task stay distinguishable.
                    self.assertTrue(option["label"] == "$battle_count" or name.startswith("循环"))
                    field = inputs[0]
                    self.assertTrue(field["pattern_msg"].startswith("请输入作战次数，"))
                    self.assertGreater(len(field["pattern_msg"]), len("请输入作战次数，"))
                    (entry, body), = option["pipeline_override"].items()
                    self.assertEqual(body, {"attach": {"target_count": "{作战次数}"}})
            self.assertNotIn("自定义作战次数", str(doc))
        self.assertGreaterEqual(seen, 8)


if __name__ == "__main__":
    unittest.main()
