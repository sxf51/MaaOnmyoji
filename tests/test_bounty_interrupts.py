"""Cooperative invitations mask task pages; touch ranges are rechecked afterward."""
import numpy as np
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.input_safety import ensure_input_safe
from custom.action.target_count import with_purchase_stop
from custom.route_policy import is_control_route


class BountyRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes={}
        for path in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
            if path.name!='Example.json':cls.nodes.update(load_jsonc(path))

    def check_routes(self, routes):
        if routes[:1] != ['[JumpBack]CommonBountyInterrupt']:
            self.assertTrue(is_control_route(routes,self.nodes.get),routes)

    def test_all_production_continuations_put_bounty_before_background(self):
        exempt = {'CommonPurchaseStop', 'CommonBountyInterrupt', 'CommonRefuseBounty', 'SafeInput_CommonRefuseBounty'}
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

    def test_runtime_routes_have_one_interrupt_even_if_old_branch_supplied(self):
        self.assertEqual(with_purchase_stop(['CommonPurchaseStop', '[JumpBack]CommonRefuseBounty',
            '[JumpBack]CommonBountyInterrupt', 'Target']),
            ['[JumpBack]CommonBountyInterrupt', 'Target'])


class FreshTouchBountyTest(unittest.TestCase):
    def context(self, *, target=True, risk_after=False, remains=False, closes=True):
        old = np.zeros((720,1280,3),dtype=np.uint8)
        fresh = old.copy()
        context = Mock()
        context.tasker.stopping = False
        context.run_task.return_value = SimpleNamespace(status=SimpleNamespace(succeeded=closes))
        def recognition(name, image):
            if name == 'CommonRefuseBounty':
                matched = image is old or remains
            elif name == 'TestSwipe':
                matched = target
            else:
                matched = True
            return SimpleNamespace(hit=matched, box=[10, 20, 30, 40] if matched else None)
        context.run_recognition.side_effect = recognition
        context.run_recognition_direct.return_value = SimpleNamespace(all_results=[
            SimpleNamespace(text='购买',box=[10,20,30,20])] if risk_after else [])
        return context, old, fresh

    def test_dismisses_once_and_rechecks_fresh_target(self):
        context, old, fresh = self.context()
        with patch('custom.input_safety.screenshot', return_value=fresh):
            self.assertTrue(ensure_input_safe(context, old, 'SafeInput_TestSwipe', [[10,20,30,40]]))
        context.run_task.assert_called_once_with('CommonBountyInterrupt')
        context.run_recognition.assert_any_call('TestSwipe', fresh)
        context.tasker.post_stop.assert_not_called()

    def test_changed_target_or_new_purchase_risk_blocks_touch(self):
        for kwargs in ({'target': False}, {'risk_after': True}, {'remains': True}, {'closes': False}):
            with self.subTest(kwargs=kwargs):
                context, old, fresh = self.context(**kwargs)
                with patch('custom.input_safety.screenshot', return_value=fresh):
                    self.assertFalse(ensure_input_safe(context, old, 'SafeInput_TestSwipe', [[10,20,30,40]]))
                context.tasker.post_stop.assert_called_once()
                context.run_task.assert_called_once_with('CommonBountyInterrupt')
