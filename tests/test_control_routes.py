"""Control dispatch must never promote a visual check or input to a bare route."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from custom.route_policy import is_control_route,with_purchase_stop


class ControlRouteTest(unittest.TestCase):
    def setUp(self):
        self.nodes={
            'Dispatch':{'next':['[JumpBack]CommonBountyInterrupt','Reset']},
            'Reset':{'action':{'type':'Custom','param':{'custom_action':'ResetCount'}},'next':['[JumpBack]CommonBountyInterrupt','Input']},
            'Input':{'recognition':{'type':'OCR','param':{'roi':[100,100,80,30],'expected':'挑战'}},'action':'Click','next':[]},
        }
        self.context=SimpleNamespace(get_node_data=self.nodes.get)

    def test_data_dispatch_skips_ocr_but_input_keeps_both_guards(self):
        self.assertEqual(with_purchase_stop(['Dispatch'],self.context),['Dispatch'])
        self.assertEqual(with_purchase_stop(['Reset'],self.context),['Reset'])
        self.assertEqual(with_purchase_stop(['Input'],self.context),['[JumpBack]CommonBountyInterrupt','Input'])

    def test_visual_and_unknown_custom_nodes_are_never_bare(self):
        for node in [
            {'recognition':'OCR','next':['Input']},
            {'action':{'type':'Custom','param':{'custom_action':'UnknownMayClick'}},'next':['Input']},
            {'action':'Click','next':['Input']},
            {'inverse':True,'next':['Input']},
            {'enabled':False,'next':['Input']},
            {'max_hit':3,'next':['Input']},
            {'post_wait_freezes':{'time':300},'next':['Input']},
            {'next':[]},
        ]:
            with self.subTest(node=node):
                self.nodes['Candidate']=node
                self.assertFalse(is_control_route(['Candidate'],self.nodes.get))

    def test_mixed_and_jump_back_queues_keep_guards(self):
        for routes in [['Dispatch','Input'],['[JumpBack]Dispatch'],[{'name':'Dispatch'}],['Missing']]:
            with self.subTest(routes=routes):
                self.assertEqual(with_purchase_stop(routes,self.context)[:1],['[JumpBack]CommonBountyInterrupt'])

    def test_unconditional_control_cycle_cannot_skip_ocr(self):
        self.nodes['Reset']['next']=['Dispatch']
        self.assertFalse(is_control_route(['Dispatch'],self.nodes.get))

    def test_control_chain_with_missing_destination_keeps_guards(self):
        self.nodes['Reset']['next']=['Missing']
        self.assertFalse(is_control_route(['Dispatch'],self.nodes.get))

    def test_normalized_framework_defaults_allow_bookkeeping(self):
        self.nodes['Reset'].update({'recognition':{'type':'DirectHit'},'enabled':True,'inverse':False,'max_hit':4294967295,
            'pre_wait_freezes':{'time':0},'post_wait_freezes':{'time':0}})
        self.assertEqual(with_purchase_stop(['Reset'],self.context),['Reset'])
