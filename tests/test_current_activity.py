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
        self.assertEqual(challenge["recognition"]["param"]["roi"], [1067, 564, 212, 154])
        self.assertEqual(challenge["recognition"]["param"]["expected"], ["^挑战$", "^搜寻$", "^开启$"])

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


if __name__ == "__main__":
    unittest.main()
