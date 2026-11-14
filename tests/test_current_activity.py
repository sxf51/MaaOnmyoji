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

    def test_count_route_returns_to_challenge(self) -> None:
        pipeline = json.loads(
            (ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(
                encoding="utf-8"
            )
        )
        params = pipeline["CommonActivityFinishOnce"]["action"]["param"][
            "custom_action_param"
        ]
        self.assertEqual(params["continue_node"], "CommonActivityPreparation")
        self.assertEqual(params["finish_node"], "CommonActivityFinish")

    def test_battle_arms_counter_and_challenge_page_counts(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        # Seeing the battle arms the FSM; returning to the challenge page counts once.
        arm = pipeline["CommonActivityChallenging"]["action"]["param"]["custom_action"]
        self.assertEqual(arm, "TargetCountArm")
        finish = pipeline["CommonActivityFinishOnce"]
        self.assertEqual(finish["recognition"]["param"]["all_of"], ["CommonActivityChallenge"])
        params = finish["action"]["param"]["custom_action_param"]
        self.assertTrue(params["require_armed"])
        self.assertEqual(params["idle_node"], "CommonActivityChallenge")
        counters = [name for name, node in pipeline.items() if isinstance(node.get("action"), dict)
                    and node["action"].get("param", {}).get("custom_action") == "TargetCountProgress"]
        self.assertEqual(counters, ["CommonActivityFinishOnce"])

    def test_one_shared_list_ordered_by_frequency(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        hub = pipeline["CommonActivityTask"]["next"]
        for name in ("CommonActivityChallenge", "CommonActivityBattleResult", "CommonActivityVictory"):
            self.assertEqual(pipeline[name]["next"], hub, name)
        order = ["[JumpBack]CommonActivityChallenging", "CommonActivityBattleFailure", "CommonActivityBattleResult",
                 "CommonActivityVictory", "CommonActivityInsufficientAP", "CommonActivityFinishOnce",
                 "[JumpBack]CommonActivityPrepare", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"]
        self.assertEqual([hub.index(n) for n in order], sorted(hub.index(n) for n in order))
        # Auto switching needs the battle page too, so it must precede the battle wait.
        self.assertLess(hub.index("[JumpBack]CommonClickAuto"), hub.index("[JumpBack]CommonActivityChallenging"))

    def test_victory_without_continue_text_clicks_safe_area(self) -> None:
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(encoding="utf-8"))
        victory = pipeline["CommonActivityVictory"]
        self.assertEqual(victory["recognition"]["param"]["template"], "Common/BattleWin.png")
        self.assertTrue((ROOT / "assets/resource/base/image/Common/BattleWin.png").exists())
        x, y, w, h = victory["action"]["param"]["target"]
        # Right-side blank area (OAS C_REWARD_3), clear of the reward counter near (970-1110, 530-590).
        self.assertGreaterEqual(x, 1092)
        self.assertLessEqual(y + h, 520)
        self.assertIn("CommonActivityVictory", pipeline["CommonActivityPreparation"]["action"]["param"]["custom_action_param"]["nodes"])

if __name__ == "__main__":
    unittest.main()
