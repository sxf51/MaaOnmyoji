"""Control dispatch skips the rare interrupt checks; visual candidates always end with them."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from custom.route_policy import is_control_route,with_interrupts


class ControlRouteTest(unittest.TestCase):
    def setUp(self):
        self.nodes={
            'Dispatch':{'next':['Reset','[JumpBack]CommonBountyInterrupt','CommonUnknownPopup']},
            'Reset':{'action':{'type':'Custom','param':{'custom_action':'ResetCount'}},'next':['Input','[JumpBack]CommonBountyInterrupt','CommonUnknownPopup']},
            'Input':{'recognition':{'type':'OCR','param':{'roi':[100,100,80,30],'expected':'挑战'}},'action':'Click','next':[]},
        }
        self.context=SimpleNamespace(get_node_data=self.nodes.get)

    def test_data_dispatch_skips_interrupts_but_visual_input_keeps_them(self):
        self.assertEqual(with_interrupts(['Dispatch'],self.context),['Dispatch'])
        self.assertEqual(with_interrupts(['Reset'],self.context),['Reset'])
        self.assertEqual(with_interrupts(['Input'],self.context),['Input','[JumpBack]CommonBountyInterrupt','CommonUnknownPopup'])

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
                self.assertEqual(with_interrupts(routes,self.context)[-2:],['[JumpBack]CommonBountyInterrupt','CommonUnknownPopup'])

    def test_unconditional_control_cycle_cannot_skip_ocr(self):
        self.nodes['Reset']['next']=['Dispatch']
        self.assertFalse(is_control_route(['Dispatch'],self.nodes.get))

    def test_control_chain_with_missing_destination_keeps_guards(self):
        self.nodes['Reset']['next']=['Missing']
        self.assertFalse(is_control_route(['Dispatch'],self.nodes.get))

    def test_normalized_framework_defaults_allow_bookkeeping(self):
        self.nodes['Reset'].update({'recognition':{'type':'DirectHit'},'enabled':True,'inverse':False,'max_hit':4294967295,
            'pre_wait_freezes':{'time':0},'post_wait_freezes':{'time':0}})
        self.assertEqual(with_interrupts(['Reset'],self.context),['Reset'])
