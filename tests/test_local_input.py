"""Actual touch ranges, not unrelated text on the page, determine purchase safety."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.input_safety import action_region, regions_safe, ensure_input_safe
from custom.reco.input_target import InputTarget
from custom.action.bezier_swipe import BezierSwipe


def context(*texts):
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.run_recognition_direct.return_value = NS(all_results=[NS(text=t, box=b) for t,b in texts])
    ctx.run_recognition.return_value = NS(hit=True, box=[500,400,100,40])
    ctx.get_node_data.return_value = {'action': 'Click'}
    return ctx


class LocalInputTest(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720,1280,3),dtype=np.uint8)
        self.region = [500,400,100,40]

    def test_whole_random_rectangle_and_offset_are_resolved(self):
        self.assertEqual(action_region({'action':{'type':'Click','param':{'target':[20,30,40,50],
            'target_offset':[10,20,-5,7]}}},self.region),[30,50,35,57])
        self.assertEqual(action_region({'action':'Click'},self.region),self.region)

    def test_empty_blank_area_is_valid_and_physically_cropped(self):
        ctx=context()
        self.assertTrue(regions_safe(ctx,self.image,[self.region],'MitamaChallenge'))
        args=ctx.run_recognition_direct.call_args.args
        self.assertEqual(args[0],'OCR')
        self.assertEqual(args[2].shape,(120,260,3))
        self.assertFalse(np.shares_memory(args[2],self.image))
        ctx.run_recognition.assert_not_called()
        ctx.tasker.post_stop.assert_not_called()

    def test_purchase_and_generic_confirmation_labels_block(self):
        for text in ('购买','免费购买','兑换','支付','充值','确认','确定','勾玉','魂玉'):
            with self.subTest(text=text):
                ctx=context((text,[90,45,60,25]))
                self.assertFalse(regions_safe(ctx,self.image,[self.region],'MitamaChallenge'))
                ctx.tasker.post_stop.assert_called_once()

    def test_hazard_at_edge_of_random_range_blocks_the_entire_click(self):
        ctx=context(('购买',[250,45,60,25]))
        self.assertFalse(regions_safe(ctx,self.image,[self.region],'MitamaChallenge'))

    def test_known_business_confirmation_allows_confirm_but_never_purchase(self):
        ctx=context(('确定',[90,45,60,25]))
        self.assertTrue(regions_safe(ctx,self.image,[self.region],'TeamPresetConfirm'))
        for label in ('确认购买','兑换','勾玉'):
            self.assertFalse(regions_safe(context((label,[90,45,60,25])),self.image,[self.region],'TeamPresetConfirm'))

    def test_invalid_region_and_ocr_failure_block(self):
        for region in ([0,0,0,20],[-1,20,30,40],[1270,20,30,40]):
            ctx=context()
            self.assertFalse(regions_safe(ctx,self.image,[region],'Test'))
        ctx=context()
        ctx.run_recognition_direct.return_value=None
        self.assertFalse(regions_safe(ctx,self.image,[self.region],'Test'))

    def test_positive_target_precedes_local_ocr_on_the_same_image(self):
        ctx=context()
        argv=NS(node_name='SafeInput_Test',image=self.image,custom_recognition_param={'target_node':'Test'})
        detail=InputTarget().analyze(ctx,argv)
        self.assertEqual(detail.box,self.region)
        ctx.run_recognition.assert_called_once_with('Test',self.image)
        self.assertEqual(ctx.method_calls[0][0],'run_recognition')

    def test_wrong_page_never_authorizes_confirmation_or_runs_ocr(self):
        ctx=context(('确定',[90,45,60,25]))
        ctx.run_recognition.return_value=NS(hit=False,box=None)
        argv=NS(node_name='SafeInput_TeamPresetConfirm',image=self.image,
            custom_recognition_param={'target_node':'TeamPresetConfirm'})
        self.assertIsNone(InputTarget().analyze(ctx,argv))
        ctx.run_recognition_direct.assert_not_called()

    def test_guard_uses_runtime_action_override_and_preserves_target_box(self):
        ctx=context()
        ctx.get_node_data.return_value={'action':{'type':'Click','param':{'target':[800,600,50,40]}}}
        argv=NS(node_name='SafeInput_Test',image=self.image,custom_recognition_param={'target_node':'Test'})
        detail=InputTarget().analyze(ctx,argv)
        self.assertEqual(detail.box,self.region)
        self.assertEqual(ctx.run_recognition_direct.call_args.args[2].shape,(120,210,3))

    def test_gesture_rechecks_target_even_without_invitation(self):
        ctx=context()
        ctx.run_recognition.side_effect=lambda n,i:NS(hit=n!='CommonRefuseBounty',box=self.region)
        self.assertTrue(ensure_input_safe(ctx,self.image,'SafeInput_TestSwipe',[self.region]))
        ctx.run_recognition.assert_any_call('TestSwipe',self.image)

    def test_blocked_actual_curve_sends_no_touch_events(self):
        ctx=context()
        argv=NS(node_name='SafeInput_TestSwipe',custom_action_param={
            'begin':[100,500,40,40],'end':[800,100,40,40],'duration':1})
        with patch('custom.action.bezier_swipe.ensure_input_safe',return_value=False) as guard:
            self.assertFalse(BezierSwipe().run(ctx,argv).success)
        region=guard.call_args.kwargs['regions'][0]
        self.assertGreater(region[2],650)
        self.assertGreater(region[3],350)
        ctx.tasker.controller.post_touch_down.assert_not_called()
        ctx.tasker.controller.post_touch_move.assert_not_called()

    def test_gesture_checks_new_frame_after_invitation_and_blocks_danger(self):
        ctx=context(('购买',[90,45,60,25]))
        fresh=self.image.copy()
        ctx.run_recognition.side_effect=lambda n,i:NS(hit=n!='CommonRefuseBounty' or i is self.image,box=self.region)
        ctx.run_task.return_value=NS(status=NS(succeeded=True))
        with patch('custom.input_safety.screenshot',return_value=fresh):
            self.assertFalse(ensure_input_safe(ctx,self.image,'SafeInput_TestSwipe',[self.region]))
        ctx.run_task.assert_called_once_with('CommonBountyInterrupt')
        ctx.run_recognition.assert_any_call('TestSwipe',fresh)


class LocalPipelineCoverageTest(unittest.TestCase):
    def test_every_production_touch_has_local_protection_and_no_global_ocr(self):
        folder=Path(os.environ.get('MAAYYS_PIPELINE_TEST_DIR',ROOT/'assets/resource/base/pipeline'))
        nodes={k:v for p in folder.glob('*.json') if p.name!='Example.json' for k,v in load_jsonc(p).items()}
        count=0
        for name,node in nodes.items():
            action=node.get('action','DoNothing')
            kind=action.get('type') if isinstance(action,dict) else action
            custom=action.get('param',{}).get('custom_action') if isinstance(action,dict) else None
            if kind in ('Click','Swipe','LongPress','TouchDown','TouchMove') or custom=='BezierSwipe':
                count+=1
                self.assertEqual(node['recognition']['param']['custom_recognition'],'InputTarget',name)
                public=node['recognition']['param']['custom_recognition_param']['target_node']
                self.assertEqual(public,name.removeprefix('SafeInput_'))
                self.assertNotEqual(nodes[public].get('recognition','DirectHit'),'DirectHit')
            self.assertNotIn('CommonPurchaseStop',node.get('next',[]),name)
            self.assertNotIn('CommonSafetyScreenReadable',str(node.get('recognition',{})),name)
        self.assertGreater(count,150)


if __name__=='__main__':unittest.main()
