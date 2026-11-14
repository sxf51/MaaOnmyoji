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

    def test_navigation_retry_cannot_send_input(self):
        retry = self.nodes["NativeNavRetry"]
        self.assertNotIn("recognition", retry)
        self.assertEqual((retry["action"], retry["next"]), ("DoNothing", []))
        users = {name for name, node in self.nodes.items() if "NativeNavRetry" in node.get("next", [])}
        self.assertEqual(len(users), 25)
        self.assertTrue(all(name.startswith("Native") for name in users))

    def test_every_production_input_requires_matched_target_and_local_guard(self):
        count = 0
        navigation_steps = {item.removeprefix("[JumpBack]") for name, node in self.nodes.items() if name.startswith("Navigate")
                            for item in node.get("next", []) if item.startswith("[JumpBack]Native")}
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
                }
                if name == "SafeInput_CommonRefuseBounty":
                    self.assertEqual(public["next"], ["CommonBountyClosed", name])
                elif name in preparation_exits:
                    exit_node = preparation_exits[name]
                    wait = ['[JumpBack]CommonBattleEnteringWait']
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
                elif name in ("SafeInput_MitamaBattleResult", "SafeInput_TeamLeaderBattleResult"):
                    stats = "MitamaSoloDropStatistics" if name == "SafeInput_MitamaBattleResult" else "TeamLeaderDropStatistics"
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", "[JumpBack]MitamaTreasurePopup", stats, name])
                    self.assertEqual(self.nodes[stats]["next"], ["[JumpBack]CommonBountyInterrupt", name])
                elif name == "SafeInput_AwakeningBattleResult":
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", "[JumpBack]MitamaTreasurePopup", "AwakeningDropStatistics", name])
                    self.assertEqual(self.nodes["AwakeningDropStatistics"]["next"], ["[JumpBack]CommonBountyInterrupt", name])
                elif name.removeprefix("SafeInput_") in navigation_steps:
                    # Only a no-input return may follow, and max_hit still limits the retries.
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", name, "NativeNavRetry"])
                    self.assertIsNotNone(public.get("max_hit"))
                else:
                    self.assertEqual(public["next"], ["[JumpBack]CommonBountyInterrupt", name])
                target, guard = node["recognition"]["param"]["all_of"]
                self.assertEqual(target, name.removeprefix("SafeInput_"))
                self.assertEqual(guard["recognition"]["param"]["custom_recognition"], "InputTarget")
                self.assertEqual(guard["recognition"]["param"]["custom_recognition_param"], {"target_node": target})
                self.assertEqual(node["pre_delay"], 0)
                self.assertEqual(node["pre_wait_freezes"], 0)
                self.assertEqual(node.get("repeat", 1), 1)
                recognition = public.get("recognition", "DirectHit")
                self.assertNotEqual(recognition, "DirectHit")
                if isinstance(recognition, dict):
                    self.assertNotEqual(recognition.get("type"), "DirectHit")
        self.assertGreater(count, 0)

    def test_confirmation_requires_update_context_and_an_exact_button(self):
        self.assertEqual(self.nodes["StartupUpdateConfirm"]["recognition"]["param"]["all_of"],
                         ["StartupUpdateText", "StartupUpdateButton"])
        expected = self.nodes["StartupUpdateText"]["recognition"]["param"]["expected"]
        self.assertFalse(any(re.search(pattern, "确认购买") for pattern in expected))


if __name__ == "__main__":
    unittest.main()


class ConfirmTargetTest(unittest.TestCase):
    """A click next to 确定/确认 is refused unless its node is allowlisted (log 2026-11-13 02:41)."""

    def test_nodes_that_click_a_confirm_button_are_allowlisted(self):
        import json as _json
        import re as _re
        from custom.input_safety import CONFIRM_TARGETS
        nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            if path.name != "Example.json":
                nodes.update(_json.loads(path.read_text(encoding="utf-8")))

        def expects_confirm(recognition, seen):
            if not isinstance(recognition, dict):
                return False
            param = recognition.get("param", {})
            if recognition.get("type") == "OCR":
                expected = param.get("expected", [])
                expected = [expected] if isinstance(expected, str) else expected
                return any(_re.search(r"确定|确认", item) for item in expected)
            children = param.get("all_of", param.get("any_of", []))
            for child in children:
                if isinstance(child, str):
                    if child not in seen and expects_confirm(nodes.get(child, {}).get("recognition"), seen | {child}):
                        return True
                elif expects_confirm(child.get("recognition"), seen):
                    return True
            return False

        for name in nodes:
            if name.startswith("SafeInput_"):
                public = name.removeprefix("SafeInput_")
                if expects_confirm(nodes.get(public, {}).get("recognition"), {public}):
                    self.assertIn(public, CONFIRM_TARGETS, name)

