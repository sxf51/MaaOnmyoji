"""Input contract after removing SafeInput: each click lives on its own recognized node,
and confirm/cancel popups without a rule stop the task from the end of every visual list."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.route_policy import INTERRUPT_SUFFIX, is_control_route

INPUT_TYPES = ("Click", "Swipe", "LongPress", "TouchDown", "TouchMove", "Scroll", "Key")


class InputContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for file in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            if file.name != "Example.json":
                cls.nodes.update(load_jsonc(file))

    def test_safe_input_layer_is_gone(self):
        self.assertFalse((ROOT / "assets/resource/base/pipeline/InputSafety.json").exists())
        self.assertFalse((ROOT / "agent/custom/input_safety.py").exists())
        self.assertFalse((ROOT / "agent/custom/reco/input_target.py").exists())
        for path in [*(ROOT / "assets").rglob("*.json"), *(ROOT / "agent").rglob("*.py")]:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("SafeInput_", text)
                self.assertNotIn("InputTarget", text)

    def test_every_input_requires_a_recognized_target(self):
        count = 0
        for name, node in self.nodes.items():
            action = node.get("action", "DoNothing")
            kind = action.get("type") if isinstance(action, dict) else action
            custom = action.get("param", {}).get("custom_action") if isinstance(action, dict) else None
            if kind not in INPUT_TYPES and custom != "BezierSwipe":
                continue
            count += 1
            with self.subTest(node=name):
                recognition = node.get("recognition", "DirectHit")
                kind = recognition.get("type") if isinstance(recognition, dict) else recognition
                self.assertNotEqual(kind, "DirectHit")
                self.assertEqual(node.get("repeat", 1), 1)
        self.assertGreater(count, 100)

    def test_unknown_popup_stops_the_task(self):
        popup = self.nodes["CommonUnknownPopup"]
        self.assertEqual(popup["action"], "StopTask")
        param = popup["recognition"]["param"]
        self.assertEqual(popup["recognition"]["type"], "OCR")
        self.assertEqual(param["roi"], [240, 220, 800, 420])
        for text in ("确定", "确认", "取消"):
            self.assertTrue(any(re.fullmatch(p, text) for p in param["expected"]), text)
        self.assertNotIn("next", popup)

    def test_rare_checks_are_last_in_every_visual_list(self):
        exempt = {"CommonBountyInterrupt", "CommonRefuseBounty", "CommonActivityInsufficientAP"}
        for name, node in self.nodes.items():
            routes = node.get("next")
            if not routes or name in exempt or is_control_route(routes, self.nodes.get):
                continue
            with self.subTest(node=name):
                self.assertEqual(routes[-2:], INTERRUPT_SUFFIX)
                self.assertNotIn(INTERRUPT_SUFFIX[0], routes[:-2])
                self.assertNotIn(INTERRUPT_SUFFIX[1], routes[:-2])

    def test_navigation_retry_cannot_send_input(self):
        retry = self.nodes["NativeNavRetry"]
        self.assertNotIn("recognition", retry)
        self.assertEqual((retry["action"], retry["next"]), ("DoNothing", []))

    def test_confirmation_requires_update_context_and_an_exact_button(self):
        self.assertEqual(self.nodes["StartupUpdateConfirm"]["recognition"]["param"]["all_of"],
                         ["StartupUpdateText", "StartupUpdateButton"])
        expected = self.nodes["StartupUpdateText"]["recognition"]["param"]["expected"]
        self.assertFalse(any(re.search(pattern, "确认购买") for pattern in expected))


if __name__ == "__main__":
    unittest.main()
