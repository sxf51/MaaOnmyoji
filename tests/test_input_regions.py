import ast
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))


class InputRegionTest(unittest.TestCase):
    def test_agent_has_no_bare_click_or_swipe_coordinate_calls(self):
        for path in (ROOT / "agent").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ("post_click", "post_swipe"), (str(path), node.lineno))

    def test_all_resource_and_task_input_parameters_use_regions(self):
        paths = list((ROOT / "assets/resource").rglob("*.json")) + list((ROOT / "assets/tasks").rglob("*.json"))
        for path in paths:
            with self.subTest(path=path):
                self.audit_task_inputs(load_jsonc(path))

    def test_business_swipes_only_use_bezier_with_region_endpoints(self):
        def check(value):
            if isinstance(value, list):
                for child in value:
                    check(child)
            elif isinstance(value, dict):
                action = value.get("action")
                kind = action.get("type") if isinstance(action, dict) else action
                self.assertNotIn(kind, ("Swipe", "MultiSwipe", "Scroll"))
                if kind == "Custom" and action.get("param", {}).get("custom_action") == "BezierSwipe":
                    params = action["param"]["custom_action_param"]
                    self.assert_task_region(params["begin"])
                    self.assert_task_region(params["end"])
                    self.assertNotIn("random_seed", params)
                    self.assertNotEqual(params["deviation"], 0)
                for child in value.values():
                    check(child)

        for folder in ("assets/resource", "assets/tasks"):
            for path in (ROOT / folder).rglob("*.json"):
                # Keep the user's original API examples intact, including test seeds.
                if path.name != "Example.json":
                    with self.subTest(path=path):
                        check(load_jsonc(path))

    def audit_task_inputs(self, value):
        """Repository task convention only; public APIs still accept points/lines."""
        if isinstance(value, list):
            for child in value:
                self.audit_task_inputs(child)
        elif isinstance(value, dict):
            action = value.get("action")
            kind = action.get("type") if isinstance(action, dict) else action
            param = action.get("param", {}) if isinstance(action, dict) else value
            if kind in ("Click", "LongPress", "TouchDown", "TouchMove", "Scroll"):
                self.assert_task_region(param.get("target", True))
            elif kind == "Swipe":
                self.assert_task_region(param.get("begin", True))
                self.assert_task_region(param.get("end", True))
            elif kind == "Custom" and param.get("custom_action") == "BezierSwipe":
                params = param.get("custom_action_param", {})
                if isinstance(params, dict):
                    self.assert_task_region(params.get("begin"))
                    self.assert_task_region(params.get("end"))
            for child in value.values():
                self.audit_task_inputs(child)

    def assert_task_region(self, value):
        if value is True or isinstance(value, str):
            return
        if isinstance(value, list) and value and isinstance(value[0], list):
            for child in value:
                self.assert_task_region(child)
            return
        self.assertIsInstance(value, (list, tuple))
        self.assertEqual(len(value), 4)
        self.assertGreater(value[2], 1)
        self.assertGreater(value[3], 1)


if __name__ == "__main__":
    unittest.main()
