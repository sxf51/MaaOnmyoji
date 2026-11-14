"""Cooperative invitations are rare: checked after every ordinary candidate, before the unknown-popup stop."""
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.route_policy import is_control_route


class BountyRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes={}
        for path in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
            if path.name!='Example.json':cls.nodes.update(load_jsonc(path))

    def check_routes(self, routes):
        if routes[-2:] != ['[JumpBack]CommonBountyInterrupt', 'CommonUnknownPopup']:
            self.assertTrue(is_control_route(routes,self.nodes.get),routes)
        else:
            self.assertNotIn('[JumpBack]CommonBountyInterrupt', routes[:-2], routes)

    def test_all_production_continuations_put_bounty_and_popup_stop_last(self):
        exempt = {'CommonBountyInterrupt', 'CommonRefuseBounty', 'CommonActivityInsufficientAP'}
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            if path.name == 'Example.json':
                continue
            for name, node in load_jsonc(path).items():
                if name not in exempt and node.get('next'):
                    with self.subTest(node=name):
                        self.check_routes(node['next'])

    def test_task_option_overrides_cannot_remove_interrupt_priority(self):
        def visit(value):
            if isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, dict):
                if value.get('next'):
                    self.check_routes(value['next'])
                for child in value.values():
                    visit(child)
        for path in (ROOT / 'assets/tasks').rglob('*.json'):
            with self.subTest(file=path):
                visit(load_jsonc(path))

class BountyRefuseGeometryTest(unittest.TestCase):
    """Screenshot 2026-11-13: title 协助悬赏封印 ~(525,45,255,50), 接受 ~(836,460,70,65), 拒绝 text ~(935,570,50,30)."""

    def test_refuse_click_stays_on_the_refuse_row(self):
        import json as _json
        root = Path(__file__).resolve().parents[1] / "assets/resource/base/pipeline"
        common = _json.loads((root / "Common.json").read_text(encoding="utf-8"))
        title, refuse = common["CommonRefuseBounty"]["recognition"]["param"]["all_of"]
        x, y, w, h = title["recognition"]["param"]["roi"]
        self.assertTrue(x <= 525 and y <= 45 and x + w >= 780 and y + h >= 95)
        x, y, w, h = refuse["recognition"]["param"]["roi"]
        self.assertTrue(x <= 935 and y <= 570 and x + w >= 985 and y + h >= 600)
        self.assertEqual(refuse["recognition"]["param"]["expected"], ["^拒绝$"])
        action = common["CommonRefuseBounty"]["action"]
        self.assertNotIn("target", common["CommonRefuseBounty"])
        self.assertIs(action["param"]["target"], True)
        ox, oy, ow, oh = action["param"]["target_offset"]
        # Recognized 拒绝 box on the screenshot: [929,570,59,39].
        cx, cy, cw, ch = 929 + ox, 570 + oy, 59 + ow, 39 + oh
        self.assertGreater(cy, 525)                  # never reaches the 接受 button above
        self.assertTrue(cx <= 840 and cx + cw >= 985)  # covers the red cross and the text

