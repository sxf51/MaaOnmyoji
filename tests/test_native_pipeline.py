"""Structural regression checks for production native state machines."""
from pathlib import Path
import re
import unittest

from tools.validate_schema import load_jsonc


ROOT = Path(__file__).resolve().parents[1] / "assets/resource/base/pipeline"


class NativePipelineTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {}
        for filename in ROOT.glob("*.json"):
            if filename.name != "Example.json":
                self.nodes.update(load_jsonc(filename))

    def test_production_and_examples_do_not_call_legacy_flow_actions(self):
        legacy = {"NavigateTo", "ExploreBattles", "RaidBattles", "ClaimDailyRewards",
                  "SelectTeammate", "WaitLeaderInvitation", "TeamBattle"}
        nodes = {**self.nodes, **load_jsonc(ROOT / "Example.json")}
        for name, node in nodes.items():
            action = node.get("action", {})
            if isinstance(action, dict):
                self.assertNotIn(action.get("param", {}).get("custom_action"), legacy, name)

    def test_navigation_callers_check_destination_before_jumpback(self):
        for name in ("ReturnMain", "MitamaNavigate", "MitamaDisableBuff", "RealmRaidTask",
                     "DailyRewardsTask", "NativeRewardsToTasks", "NativeRewardsToRewards"):
            choices = self.nodes[name]["next"]
            self.assertTrue(choices[-1].startswith("[JumpBack]Navigate"), name)
            self.assertIn("recognition", self.nodes[choices[1]], name)

    def test_battle_never_counts_waiting_room_without_settlement(self):
        for prefix in ("ExplorationBattle", "MitamaNativeBattle", "RealmRaidBattle"):
            after = prefix + "AfterResult"
            self.assertNotIn(after, self.nodes[prefix]["next"])
            self.assertIn(after, self.nodes["SafeInput_" + prefix + "Result"]["next"])
            self.assertIn(prefix + "Failure", self.nodes[prefix]["next"])

    def test_raid_verifies_stamp_and_limits_each_card(self):
        for i in range(9):
            self.assertEqual(self.nodes[f"RealmRaidSelect{i}"]["max_hit"], 1)
            self.assertIn(f"RealmRaid{i}Broken", self.nodes[f"RealmRaid{i}Success"]["recognition"]["param"]["all_of"])

    def test_invitation_click_targets_accept_button(self):
        param = self.nodes["MitamaAcceptNamedInvitation"]["recognition"]["param"]
        self.assertEqual(param["all_of"][param["box_index"]], "MitamaAcceptInvite")

    def test_mitama_prepare_accepts_automatic_battle_transition(self):
        prepare = self.nodes["MitamaPrepare"]
        self.assertEqual(prepare["recognition"]["param"]["expected"], ["^准备$"])
        self.assertLess(prepare["next"].index("MitamaPreparationPassed"),
                        prepare["next"].index("SafeInput_MitamaPrepare"))
        passed = self.nodes["MitamaPreparationPassed"]
        self.assertEqual(passed["action"], "DoNothing")
        self.assertEqual(passed["next"], [])
        self.assertIn("MitamaChallenging", passed["recognition"]["param"]["any_of"])
        self.assertIn("[JumpBack]MitamaPrepare", self.nodes["MitamaNativeBattle"]["next"])

    def test_mitama_treasure_supports_both_reference_titles(self):
        pattern = self.nodes["MitamaTreasurePopup"]["recognition"]["param"]["expected"]
        self.assertIsNotNone(re.fullmatch(pattern, "发现宝藏"))
        self.assertIsNotNone(re.fullmatch(pattern, "获得宝藏"))
        self.assertIsNone(re.fullmatch(pattern, "购买宝藏"))

    def test_context_specific_confirmations_require_page_and_prompt(self):
        for name, page in (("DailyMailClaimConfirm", "PageMail"),
                           ("MitamaTeamStartConfirm", "PageTeamRoom")):
            param = self.nodes[name]["recognition"]["param"]
            self.assertEqual(param["all_of"][0], page)
            self.assertEqual(len(param["all_of"]), 3)
            self.assertEqual(param["box_index"], 2)

    def test_reward_tab_verification_waits_for_task_content_to_disappear(self):
        for name in ("NativeRewardsTabReady", "NativeQuestRewardsTabReady"):
            self.assertTrue(self.nodes[name]["inverse"])
            self.assertEqual(self.nodes[name]["recognition"]["param"]["all_of"], ["PageQuestTasks"])

    def test_team_start_uses_verified_button_box(self):
        param = self.nodes["MitamaNamedTeamStart"]["recognition"]["param"]
        self.assertEqual(param["all_of"][param["box_index"]], "MitamaTeamStartButton")
        self.assertIs(self.nodes["SafeInput_MitamaNamedTeamStart"]["action"]["param"]["target"], True)

    def test_settlement_labels_cover_screen_and_blank_area_variants(self):
        for name in ("DailyBattleResult", "PageResult", "MitamaBattleResult", "CommonActivityBattleResult", "CurrentActivityBattleResult"):
            expected = self.nodes[name]["recognition"]["param"]["expected"]
            patterns = [expected] if isinstance(expected, str) else expected
            for text in ("点击屏幕继续", "点击空白区域继续"):
                self.assertTrue(any(re.fullmatch(pattern, text) for pattern in patterns), (name, text))

    def test_composite_references_do_not_rely_on_inverse(self):
        def check(recognition):
            if not isinstance(recognition, dict):
                return
            param = recognition.get("param", {})
            for item in param.get("all_of", []) + param.get("any_of", []):
                if isinstance(item, str):
                    self.assertFalse(self.nodes[item].get("inverse"), item)
                else:
                    check(item.get("recognition", {}))
        for node in self.nodes.values():
            check(node.get("recognition", {}))

    def test_broken_card_skip_precedes_selection(self):
        for i in range(9):
            self.assertEqual(self.nodes[f"RealmRaidCheck{i}"]["next"][1:3],
                             [f"RealmRaid{i}SkipBroken", f"RealmRaidSelect{i}"])
            self.assertEqual(self.nodes[f"SafeInput_RealmRaidSelect{i}"]["anchor"],
                             {"RealmRaidVerify": f"RealmRaid{i}Verify"})


if __name__ == "__main__":
    unittest.main()
