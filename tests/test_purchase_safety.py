"""Local input protection and contextual confirmation contracts."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))


class PurchasePipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for file in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            if file.name != "Example.json":
                cls.nodes.update(load_jsonc(file))

    def test_every_production_input_requires_matched_target_and_local_guard(self):
        count = 0
        for name, node in self.nodes.items():
            action = node.get("action", "DoNothing")
            kind = action.get("type") if isinstance(action, dict) else action
            custom = action.get("param", {}).get("custom_action") if isinstance(action, dict) else None
            if kind not in ("Click", "Swipe", "LongPress", "TouchDown", "TouchMove", "Scroll", "Key") and custom != "BezierSwipe":
                continue
            count += 1
            with self.subTest(node=name):
                self.assertTrue(name.startswith("SafeInput_"))
                public = self.nodes[name.removeprefix("SafeInput_")]
                self.assertEqual(public["action"], "DoNothing")
                preparation_exits = {
                    "SafeInput_MitamaPrepare": "MitamaPreparationPassed",
                    "SafeInput_DailyPrepare": "DailyPreparationPassed",
                    "SafeInput_CommonActivityPrepare": "CommonActivityPreparationPassed",
                    "SafeInput_CurrentActivityPrepare": "CurrentActivityPreparationPassed",
                    "SafeInput_MitamaRoomMemberReady": "MitamaRoomPreparationPassed",
                }
                if name == "SafeInput_CommonRefuseBounty":
                    self.assertEqual(public["next"], ["CommonBountyClosed", name])
                elif name in preparation_exits:
                    exit_node = preparation_exits[name]
                    wait = [] if name == 'SafeInput_MitamaRoomMemberReady' else ['[JumpBack]CommonBattleEnteringWait']
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", exit_node, *wait, name])
                    passed = self.nodes[exit_node]
                    self.assertEqual(passed["action"], "DoNothing")
                    self.assertEqual(passed["next"], [])
                elif name == "SafeInput_MitamaTreasurePopup":
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", "MitamaTreasureClosed", name])
                    closed = self.nodes["MitamaTreasureClosed"]
                    self.assertEqual(closed["action"], "DoNothing")
                    self.assertTrue(closed["inverse"])
                    self.assertEqual(closed["next"], [])
                elif name in ("SafeInput_MitamaBattleResult", "SafeInput_MitamaNativeBattleResult"):
                    stats = "MitamaSoloDropStatistics" if name == "SafeInput_MitamaBattleResult" else "MitamaTeamDropStatistics"
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", "[JumpBack]MitamaTreasurePopup", stats, name])
                    self.assertEqual(self.nodes[stats]["next"], ["[JumpBack]CommonBountyInterrupt", name])
                elif name == "SafeInput_AwakeningBattleResult":
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", "[JumpBack]MitamaTreasurePopup", name])
                else:
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", name])
                self.assertEqual(node["recognition"]["param"]["custom_recognition"], "InputTarget")
                self.assertEqual(node["recognition"]["param"]["custom_recognition_param"],
                                 {"target_node": name.removeprefix("SafeInput_")})
                self.assertEqual(node["pre_delay"], 0)
                self.assertEqual(node["pre_wait_freezes"], 0)
                self.assertEqual(node.get("repeat", 1), 1)
                recognition = public.get("recognition", "DirectHit")
                self.assertNotEqual(recognition, "DirectHit")
                if isinstance(recognition, dict):
                    self.assertNotEqual(recognition.get("type"), "DirectHit")
        self.assertGreater(count, 0)

    def test_stop_branch_cannot_cancel_or_cleanup(self):
        stop = self.nodes["CommonPurchaseStop"]
        self.assertEqual(stop["action"], "DoNothing")
        self.assertEqual(stop["next"], ["CommonPurchaseNever"])
        self.assertFalse(stop["on_error"])
        self.assertTrue(self.nodes["CommonPurchaseNever"]["inverse"])

    def test_confirmation_requires_update_context_and_an_exact_button(self):
        self.assertEqual(self.nodes["StartupUpdateConfirm"]["recognition"]["param"]["all_of"],
                         ["StartupUpdateText", "StartupUpdateButton"])
        expected = self.nodes["StartupUpdateText"]["recognition"]["param"]["expected"]
        self.assertFalse(any(re.search(pattern, "确认购买") for pattern in expected))


if __name__ == "__main__":
    unittest.main()
