"""Example entries only route to nodes that exist."""
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]


class ExampleRouteTest(unittest.TestCase):
    def test_remaining_example_routes_resolve(self):
        nodes = {}
        for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
            nodes.update(load_jsonc(path))
        examples = load_jsonc(ROOT / "assets/resource/base/pipeline/Example.json")
        for name, node in examples.items():
            for field in ("next", "on_error"):
                for route in node.get(field, []):
                    target = route.removeprefix("[JumpBack]")
                    self.assertIn(target, nodes, (name, target))
