import json
import re
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc


ROOT = Path(__file__).resolve().parents[1]


class CommonActivityPipelineTest(unittest.TestCase):
    def test_resource_hint_is_local_and_distinct_from_purchase_input_guard(self) -> None:
        nodes = load_jsonc(ROOT / "assets/resource/base/pipeline/Common.json")
        patterns = nodes["CommonInsufficientAP"]["recognition"]["param"]["expected"]
        for text in ("体力不足", "突破券不足", "挑战券不足", "结界突破券不足"):
            with self.subTest(text=text):
                self.assertTrue(any(re.search(pattern, text) for pattern in patterns))
        for text in ("商城", "获得奖励", "购买外观", "勾玉购买"):
            with self.subTest(text=text):
                self.assertFalse(any(re.search(pattern, text) for pattern in patterns))

    def test_pipeline_uses_mitama_battle_parameters(self) -> None:
        pipeline = json.loads(
            (ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(
                encoding="utf-8"
            )
        )

        challenge = pipeline["CommonActivityChallenge"]
        self.assertEqual(challenge["recognition"]["param"]["roi"], [938, 473, 325, 231])
        self.assertEqual(challenge["recognition"]["param"]["expected"], ["^挑战$", "^搜寻$", "^开启$", "^召唤$"])

        challenging = pipeline["CommonActivityChallenging"]
        self.assertEqual(challenging["recognition"]["param"]["all_of"], ["PageBattle"])

        result = pipeline["CommonActivityBattleResult"]
        self.assertEqual(result["recognition"]["param"]["roi"], [440, 665, 400, 55])
        patterns = result["recognition"]["param"]["expected"]
        for text in ("点击屏幕继续", "点击空白区域继续"):
            self.assertTrue(any(re.fullmatch(pattern, text) for pattern in patterns))

    def test_count_state_lives_in_native_anchors(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        idle = {"CommonActivityStage": "CommonActivityStageIdle", "CommonActivityAfterBattle": "CommonActivityFallbackOff"}
        armed = {"CommonActivityStage": "CommonActivityFinishOnce", "CommonActivityAfterBattle": "CommonActivityAfterBattleTap"}
        # Task start and every count reset the state; seeing the battle arms it.
        self.assertEqual(pipeline["CommonActivityTask"]["anchor"], idle)
        self.assertEqual(pipeline["CommonActivityFinishOnce"]["anchor"], idle)
        self.assertEqual(pipeline["CommonActivityChallenging"]["anchor"], armed)
        self.assertEqual(pipeline["CommonActivityChallenging"]["action"], "DoNothing")
        hub = pipeline["CommonActivityTask"]["next"]
        self.assertIn("[Anchor]CommonActivityStage", hub)
        self.assertNotIn("CommonActivityFinishOnce", hub)
        finish = pipeline["CommonActivityFinishOnce"]
        self.assertEqual(finish["recognition"]["param"]["all_of"], ["CommonActivityChallenge"])
        params = finish["action"]["param"]["custom_action_param"]
        self.assertEqual((params["continue_node"], params["finish_node"]), ("CommonActivityPreparation", "CommonActivityFinish"))
        # Without a battle the stage only re-taps 挑战, skipping the budget reset in Preparation.
        stage_idle = pipeline["CommonActivityStageIdle"]
        self.assertEqual(stage_idle["action"], "DoNothing")
        self.assertEqual(stage_idle["next"][0], "CommonActivityChallenge")
        self.assertFalse(pipeline["CommonActivityFallbackOff"]["enabled"])
        counters = [name for name, node in pipeline.items() if isinstance(node.get("action"), dict)
                    and node["action"].get("param", {}).get("custom_action") == "TargetCountProgress"]
        self.assertEqual(counters, ["CommonActivityFinishOnce"])
        self.assertNotIn("TargetCountArm", json.dumps(pipeline))

    def test_one_shared_list_ordered_by_frequency(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        hub = pipeline["CommonActivityTask"]["next"]
        for name in ("CommonActivityChallenge", "CommonActivityBattleResult", "CommonActivityVictory"):
            self.assertEqual(pipeline[name]["next"], hub, name)
        order = ["[JumpBack]CommonActivityChallenging", "CommonActivityBattleFailure", "CommonActivityBattleResult",
                 "CommonActivityVictory", "CommonActivityInsufficientAP", "[Anchor]CommonActivityStage",
                 "[JumpBack]CommonActivityPrepare", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"]
        self.assertEqual([hub.index(n) for n in order], sorted(hub.index(n) for n in order))
        # Auto switching needs the battle page too, so it must precede the battle wait.
        self.assertLess(hub.index("[JumpBack]CommonClickAuto"), hub.index("[JumpBack]CommonActivityChallenging"))

    def test_victory_without_continue_text_clicks_safe_area(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        victory = pipeline["CommonActivityVictory"]
        drum, bag = victory["recognition"]["param"]["any_of"]
        self.assertEqual(drum["recognition"]["param"]["template"], "Common/BattleWin.png")
        self.assertEqual(bag, "CommonBattleRewardBag")
        self.assertTrue((ROOT / "assets/resource/base/image/Common/BattleWin.png").exists())
        x, y, w, h = victory["action"]["param"]["target"]
        # Right-side blank area (OAS C_REWARD_3), clear of the reward counter near (970-1110, 530-590).
        self.assertGreaterEqual(x, 1092)
        self.assertLessEqual(y + h, 520)
        self.assertIn("CommonActivityVictory", pipeline["CommonActivityPreparation"]["action"]["param"]["custom_action_param"]["nodes"])
    def test_hub_handles_battle_popups_like_the_other_loops(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        hub = pipeline["CommonActivityTask"]["next"]
        for name in ("[JumpBack]CommonRewardItemDetail", "[JumpBack]CommonRewardOverflow", "[JumpBack]CommonSoulMismatch",
                     "[JumpBack]DailyRewardPopup", "[JumpBack]CommonNetworkRetry", "[Anchor]CommonActivityAfterBattle"):
            self.assertIn(name, hub)
        # Popups covering the result or prepare page come before the buttons underneath them.
        self.assertLess(hub.index("[JumpBack]CommonRewardItemDetail"), hub.index("CommonActivityBattleResult"))
        self.assertLess(hub.index("[JumpBack]CommonSoulMismatch"), hub.index("[JumpBack]CommonActivityPrepare"))
        self.assertLess(hub.index("[JumpBack]CommonSoulMismatchMute"), hub.index("[JumpBack]CommonSoulMismatch"))
        # The fallback tap is the last ordinary candidate.
        self.assertEqual(hub[-3:], ["[Anchor]CommonActivityAfterBattle", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])

    def test_fallback_tap_only_after_a_battle_and_bounded(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        tap = pipeline["CommonActivityAfterBattleTap"]
        # Native "nothing known on screen": no popup, invite or battle (inverse on the node itself).
        self.assertEqual(tap["recognition"]["param"]["any_of"], ["CommonUnknownPopup", "CommonRefuseBounty", "PageBattle"])
        self.assertTrue(tap["inverse"])
        self.assertEqual((tap["max_hit"], tap["post_delay"]), (5, 3000))
        self.assertEqual(tap["action"]["param"]["target"], pipeline["CommonActivityVictory"]["action"]["param"]["target"])
        resets = pipeline["CommonActivityPreparation"]["action"]["param"]["custom_action_param"]["nodes"]
        self.assertIn("CommonActivityAfterBattleTap", resets)
        self.assertNotIn("TargetCountProgress", json.dumps(tap))

    def test_shared_popup_handlers(self) -> None:
        common = json.loads((ROOT / "assets/resource/base/pipeline/Common.json").read_text(encoding="utf-8"))
        detail = common["CommonRewardItemDetail"]
        self.assertEqual(detail["recognition"]["param"]["expected"], "获取途径")
        x, y, w, h = detail["action"]["param"]["target"]
        self.assertLessEqual(x + w, 249)  # OAS C_REWARD_2: left blank strip
        mismatch = common["CommonSoulMismatch"]["recognition"]["param"]["all_of"]
        self.assertTrue(re.search(mismatch[0]["recognition"]["param"]["expected"], "御魂与预设不一致"))
        self.assertTrue(re.fullmatch(mismatch[1]["recognition"]["param"]["expected"], "确认"))
        self.assertEqual(common["CommonSoulMismatchMute"]["max_hit"], 1)
        nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            if path.name != "Example.json":
                nodes.update(json.loads(path.read_text(encoding="utf-8")))
        # The prompt appears right after tapping 挑战, so the post-tap waits handle it before retrying the tap.
        for wait in ("MitamaWaitSoloBattleStart", "AwakeningWaitBattleStart", "BondlingSearchWaitBattleStart",
                     "RealmRaidWaitBattleStart", "TeamLeaderWaitBattleStart"):
            self.assertIn("[JumpBack]CommonSoulMismatch", nodes[wait]["next"], wait)
        for loop in ("MitamaBattle", "AwakeningBattle", "BondlingSearchBattle", "TeamLeaderBattle", "ExplorationBattle", "RealmRaidBattleSettle"):
            routes = nodes[loop]["next"]
            self.assertIn("[JumpBack]CommonRewardItemDetail", routes, loop)
            self.assertIn("[JumpBack]CommonSoulMismatch", routes, loop)


if __name__ == "__main__":
    unittest.main()
