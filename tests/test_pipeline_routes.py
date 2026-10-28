import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc, validate_route_uniqueness


class PipelineRouteTest(unittest.TestCase):
    def test_normal_and_error_destinations_must_differ(self):
        errors = validate_route_uniqueness({"A": {"next": ["B"], "on_error": ["B"]}})
        self.assertEqual(len(errors), 1)
        self.assertEqual(list(errors[0].path), ["A", "on_error", 0])

    def test_same_list_cannot_repeat_a_destination_with_jumpback(self):
        errors = validate_route_uniqueness({"A": {"next": ["B", "[JumpBack]B"]}})
        self.assertEqual(len(errors), 1)

    def test_object_route_cannot_repeat_string_route(self):
        errors = validate_route_uniqueness({"A": {"next": ["B"], "on_error": [{"name": "B", "jump_back": True}]}})
        self.assertEqual(len(errors), 1)

    def test_anchor_and_static_node_are_separate_references(self):
        self.assertFalse(validate_route_uniqueness({"A": {"next": ["[Anchor]B"], "on_error": ["B"]}}))

    def test_all_resource_routes_are_unique(self):
        root = Path(__file__).resolve().parents[1] / "assets/resource"
        for path in root.rglob("*.json"):
            with self.subTest(path=path):
                self.assertEqual(validate_route_uniqueness(load_jsonc(path)), [])


if __name__ == "__main__":
    unittest.main()
