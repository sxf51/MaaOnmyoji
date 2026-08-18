import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CommonActivityPipelineTest(unittest.TestCase):
    def test_pipeline_uses_mitama_battle_parameters(self) -> None:
        pipeline = json.loads(
            (ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(
                encoding="utf-8"
            )
        )

        challenge = pipeline["CommonActivityChallenge"]
        self.assertEqual(challenge["recognition"]["param"]["roi"], [1067, 564, 212, 154])
        self.assertEqual(challenge["recognition"]["param"]["expected"], ["挑战"])

        challenging = pipeline["CommonActivityChallenging"]
        self.assertEqual(challenging["recognition"]["param"]["expected"], ["妖术", "普攻"])

        result = pipeline["CommonActivityBattleResult"]
        self.assertEqual(result["recognition"]["param"]["roi"], [545, 669, 189, 44])
        self.assertEqual(result["recognition"]["param"]["expected"], ["点击屏幕继续"])

    def test_count_route_returns_to_challenge(self) -> None:
        pipeline = json.loads(
            (ROOT / "assets/resource/base/pipeline/CommonActivity.json").read_text(
                encoding="utf-8"
            )
        )
        params = pipeline["CommonActivityCountDetermine"]["action"]["param"][
            "custom_action_param"
        ]
        self.assertEqual(params["continue_node"], "CommonActivityChallenge")
        self.assertEqual(params["finish_node"], "CommonActivityFinish")


if __name__ == "__main__":
    unittest.main()
