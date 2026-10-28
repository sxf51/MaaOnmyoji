"""Removed flow implementations must not return through imports or examples."""
import ast
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]


class LegacyCleanupTest(unittest.TestCase):
    def test_removed_flow_files_and_registrations_are_absent(self):
        for name in ("custom/navigation.py", "custom/action/navigation.py", "custom/action/daily.py", "custom/action/team.py"):
            self.assertFalse((ROOT / "agent" / name).exists(), name)
        removed = {"NavigateTo", "ExploreBattles", "RaidBattles", "ClaimDailyRewards",
                   "SelectTeammate", "WaitLeaderInvitation", "TeamBattle"}
        for path in (ROOT / "agent").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "custom_action":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        self.assertNotIn(node.args[0].value, removed, str(path))

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
