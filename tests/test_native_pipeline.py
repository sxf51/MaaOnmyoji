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

    def test_navigation_callers_check_destination_before_jumpback(self):
        for name in ("ReturnMain", "MitamaNavigate", "MitamaDisableBuff", "RealmRaidTask",
                     "DailyRewardsTask", "NativeRewardsToTasks", "NativeRewardsToRewards"):
            choices = self.nodes[name]["next"]
            self.assertTrue(choices[-1].startswith("[JumpBack]Navigate"), name)
            self.assertIn("recognition", self.nodes[choices[1]], name)

    def test_battle_never_counts_waiting_room_without_settlement(self):
        # The raid loop checks settlement only once the battle exit button is gone.
        loops = {"ExplorationBattle": "ExplorationBattle", "MitamaNativeBattle": "MitamaNativeBattle",
                 "RealmRaidBattle": "RealmRaidBattleSettle"}
        for prefix, loop in loops.items():
            after = prefix + "AfterResult"
            self.assertNotIn(after, self.nodes[loop]["next"])
            self.assertIn(after, self.nodes["SafeInput_" + prefix + "Result"]["next"])
            self.assertIn(prefix + "Failure", self.nodes[loop]["next"])

    def test_raid_verifies_stamp_and_limits_each_card(self):
        self.assertEqual(self.nodes['SafeInput_RealmRaidSelect']['max_hit'], 3)
        self.assertIn('RealmRaidBroken', self.nodes['RealmRaidSuccess']['recognition']['param']['all_of'])

    def test_raid_attack_uses_detected_button_and_dismiss_stays_above_popup_buttons(self):
        param=self.nodes['PageRaidTarget']['recognition']['param']
        self.assertEqual(param['all_of'][0],'RealmRaidPersonalTab')
        button_roi=param['all_of'][param['box_index']]['recognition']['param']['roi']
        self.assertIs(self.nodes['SafeInput_RealmRaidAttack']['action']['param']['target'], True)
        self.assertIs(self.nodes['SafeInput_RealmRaidSelect']['action']['param']['target'], True)
        for name in ('SafeInput_NavDismissRaidTarget','SafeInput_NativeNavDismissRaidTarget'):
            target=self.nodes[name]['action']['param']['target']
            self.assertLess(target[1]+target[3],button_roi[1])

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
        self.assertEqual(self.nodes['RealmRaidCard']['recognition']['type'], 'TemplateMatch')
        self.assertEqual(self.nodes['RealmRaidBroken']['recognition']['param']['roi'], 'RealmRaidChoose')




if __name__ == "__main__":
    unittest.main()
