"""Optional exploration stop once realm-raid tickets reach the chosen threshold."""
import re
import unittest
from pathlib import Path
from tools.validate_schema import load_jsonc
ROOT=Path(__file__).resolve().parents[1]
PAGES={'Map':'PageExplore','Entrance':'PageChapter','Field':'PageChapterField'}

class ExplorationTicketTest(unittest.TestCase):
    def setUp(self):
        self.nodes=load_jsonc(ROOT/'assets/resource/base/pipeline/Exploration.json')
        self.task=load_jsonc(ROOT/'assets/tasks/Exploration.json')

    def test_disabled_by_default_and_each_page_reads_its_own_area(self):
        node=self.nodes['ExplorationTicketThreshold']
        self.assertFalse(node['enabled'])
        self.assertEqual(node['next'][-1],'ExplorationFinish')
        self.assertEqual(node['recognition']['param']['any_of'],['ExplorationTicket'+s for s in PAGES])
        for suffix,page in PAGES.items():
            self.assertEqual(self.nodes['ExplorationTicket'+suffix]['recognition']['param']['all_of'],
                             [page,'ExplorationTicketCount'+suffix])
            self.assertEqual(self.nodes['ExplorationTicketCount'+suffix]['recognition']['type'],'OCR')
        run=self.nodes['ExplorationRun']['next']
        for ready in ('ExplorationFieldReady','ExplorationChapterReady','ExplorationMapReady'):
            self.assertLess(run.index('ExplorationTicketThreshold'),run.index(ready))

    def test_option_cases_switch_threshold_patterns_for_every_page(self):
        option=self.task['option']['探索突破券阈值']
        self.assertIn('探索突破券阈值',self.task['task'][0]['option'])
        cases={case['name']:case['pipeline_override'] for case in option['cases']}
        self.assertFalse(cases[option['default_case']]['ExplorationTicketThreshold']['enabled'])
        samples={'24/30':set(),'25/30':{'25张及以上'},'29 / 30':{'25张及以上'},'30/30':{'25张及以上','30张（已满）'},'4328':set()}
        for name in ('25张及以上','30张（已满）'):
            self.assertTrue(cases[name]['ExplorationTicketThreshold']['enabled'])
            patterns={cases[name]['ExplorationTicketCount'+s]['recognition']['param']['expected'] for s in PAGES}
            self.assertEqual(len(patterns),1,name)
            pattern=re.compile(patterns.pop())
            for text,hits in samples.items():
                self.assertEqual(bool(pattern.search(text)),name in hits,(name,text))
