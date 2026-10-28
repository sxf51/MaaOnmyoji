"""Cropped custom OCR must preserve global coordinates and reference bounds."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from tools.validate_schema import load_jsonc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.reco.general import ColorOCR, ColorOCRWithFallback, MultiRecognition


def result(hit=True, box=None):
    return SimpleNamespace(hit=hit, box=box if box is not None else [1, 2, 3, 4])


class BusinessROITest(unittest.TestCase):
    def test_production_ocr_never_scans_the_full_frame(self):
        allowed=set()
        def visit(value, node):
            if isinstance(value, dict):
                if value.get('type')=='OCR':
                    roi=value.get('param',{}).get('roi')
                    if node not in allowed:
                        self.assertIsNotNone(roi,node)
                        self.assertNotIn(roi,[True,[0,0,0,0],[0,0,1280,720]],node)
                for child in value.values():
                    visit(child,node)
            elif isinstance(value,list):
                for child in value:
                    visit(child,node)
        folder=Path(__file__).resolve().parents[1]/'assets/resource/base/pipeline'
        for path in folder.glob('*.json'):
            if path.name!='Example.json':
                for name,node in load_jsonc(path).items():
                    visit(node,name)


class CroppedColorOCRTest(unittest.TestCase):
    def setup_args(self, roi=(10, 20, 30, 20), node_roi=None):
        image=np.zeros((80, 100, 3), dtype=np.uint8)
        image[20:40, 10:40]=[20, 40, 60]
        args=SimpleNamespace(image=image, roi=roi,
            custom_recognition_param={'recognition':'Text', 'target_color':[60,40,20], 'tolerance':0})
        context=Mock()
        context.get_node_data.return_value={'recognition':{'type':'OCR','param':{'roi':node_roi}}}
        context.run_recognition.return_value=result()
        return context,args

    def test_crops_color_work_and_restores_full_image_coordinates(self):
        context,args=self.setup_args()
        original=args.image.copy()
        value=ColorOCR().analyze(context,args)
        self.assertEqual(value.box,[11,22,3,4])
        passed=context.run_recognition.call_args.args
        self.assertEqual(passed[1].shape,(20,30,3))
        self.assertTrue(np.all(passed[1]==0))
        self.assertEqual(passed[2]['Text']['recognition']['param']['roi'],[0,0,30,20])
        np.testing.assert_array_equal(args.image,original)
        context.override_pipeline.assert_not_called()

    def test_intersects_delegate_region_instead_of_widening_it(self):
        context,args=self.setup_args(node_roi=[20,25,50,30])
        value=ColorOCR().analyze(context,args)
        self.assertEqual(value.detail['roi'],[20,25,20,15])
        self.assertEqual(value.box,[21,27,3,4])

    def test_delegate_offset_is_applied_before_clipping_to_image(self):
        context,args=self.setup_args(roi=[0,0,100,80],node_roi=[-10,-10,20,20])
        context.get_node_data.return_value['recognition']['param']['roi_offset']=[10,10,0,0]
        value=ColorOCR().analyze(context,args)
        self.assertEqual(value.detail['roi'],[0,0,20,20])
        self.assertEqual(value.box,[1,2,3,4])

    def test_fallback_uses_same_crop_and_does_not_accept_false_hit_box(self):
        context,args=self.setup_args()
        context.run_recognition.side_effect=[result(False),result(True)]
        value=ColorOCRWithFallback().analyze(context,args)
        self.assertEqual(value.box,[11,22,3,4])
        self.assertFalse(value.detail['filtered'])
        first,second=context.run_recognition.call_args_list
        self.assertEqual(first.args[1].shape,second.args[1].shape)
        np.testing.assert_array_equal(second.args[1],args.image[20:40,10:40])

    def test_empty_or_disjoint_region_does_not_run_ocr(self):
        for roi,node_roi in [([200,200,10,10],None),([10,20,30,20],[70,60,10,10]),([0,0,-1,5],None)]:
            with self.subTest(roi=roi):
                context,args=self.setup_args(roi,node_roi)
                self.assertIsNone(ColorOCR().analyze(context,args))
                context.run_recognition.assert_not_called()

    def test_out_of_crop_result_cannot_produce_a_global_click_target(self):
        context,args=self.setup_args()
        context.run_recognition.return_value=result(True,[29,1,5,5])
        self.assertIsNone(ColorOCR().analyze(context,args))


class CombinedRecognitionTest(unittest.TestCase):
    def analyze(self, context, logic, mode='first'):
        image=np.zeros((20,20,3),dtype=np.uint8)
        args=SimpleNamespace(image=image,custom_recognition_param={'nodes':['A','B','C'],'logic':logic,'return':mode})
        return MultiRecognition().analyze(context,args)

    def test_and_stops_at_first_failure(self):
        context=Mock()
        context.run_recognition.return_value=result(False)
        self.assertIsNone(self.analyze(context,'AND'))
        self.assertEqual(context.run_recognition.call_count,1)

    def test_or_first_stops_after_first_hit_but_union_reads_all(self):
        context=Mock()
        context.run_recognition.side_effect=[result(False),result(True,[2,3,4,5])]
        self.assertEqual(self.analyze(context,'OR').box,[2,3,4,5])
        self.assertEqual(context.run_recognition.call_count,2)
        context.reset_mock()
        context.run_recognition.side_effect=[result(True,[1,1,2,2]),result(False),result(True,[10,10,2,2])]
        self.assertEqual(self.analyze(context,'OR','union').box,[1,1,11,11])
        self.assertEqual(context.run_recognition.call_count,3)
