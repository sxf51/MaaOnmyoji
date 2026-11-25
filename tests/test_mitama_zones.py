"""日轮之陨 (FallenSun) and 永生之海 (EternitySea): config, navigation, team and stop contracts."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom import page_state

ZONES = {
    'FallenSun': dict(cn='日轮之陨', default='日蚀', layers=['壹层', '贰层', '叁层', '日蚀'], cap='50/50'),
    'EternitySea': dict(cn='永生之海', default='肆层', layers=['壹层', '贰层', '叁层', '肆层'], cap='30/30'),
}


class MitamaZoneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            if path.name != 'Example.json':
                cls.nodes.update(load_jsonc(path))
        cls.interface = load_jsonc(ROOT / 'assets/interface.json')

    def each(self):
        for P, z in ZONES.items():
            with self.subTest(zone=P):
                yield P, z, load_jsonc(ROOT / f'assets/tasks/{P}.json')

    def test_tasks_are_imported_with_expected_floors_and_defaults(self):
        for P, z, task in self.each():
            self.assertIn(f'tasks/{P}.json', self.interface['import'])
            option = task['option'][f"{z['cn']}层数"]
            self.assertEqual(option['default_case'], z['default'])
            self.assertEqual([c['name'] for c in option['cases']], z['layers'])
            roi = self.nodes[P + 'LayerText']['recognition']['param']['roi']
            for case in option['cases']:
                o = case['pipeline_override']
                expected = o[P + 'LayerText']['recognition']['param']['expected']
                self.assertIsNotNone(re.fullmatch(expected, case['name']))
                sel = o[P + 'LayerSelected']['recognition']['param']['all_of']
                self.assertEqual(sel[0], f'Page{P}Stage')
                self.assertEqual(sel[1]['recognition']['param']['expected'], expected)
                self.assertEqual(sel[1]['recognition']['param']['roi'], roi)
                self.assertEqual(sel[2]['recognition']['type'], 'ColorMatch')
                # Room target uses a contains-match on the same floor name.
                self.assertIsNotNone(re.search(o[P + 'RoomLayer']['recognition']['param']['expected'],
                                               f"目标 {z['cn']} {case['name']}"))

    def test_team_option_switches_to_the_shared_leader_flow(self):
        for P, z, task in self.each():
            cases = {c['name']: c for c in task['option'][f"{z['cn']}组队"]['cases']}
            self.assertEqual(task['option'][f"{z['cn']}组队"]['default_case'], 'No')
            self.assertEqual(cases['No']['pipeline_override'][P + 'BattleModeDestination']['next'][0], P + 'BattleEntry')
            yes = cases['Yes']['pipeline_override']
            self.assertEqual(yes[P + 'BattleModeDestination']['next'][0], P + 'TeamEntry')
            self.assertTrue(yes[P + 'TeamResume']['enabled'])
            self.assertEqual(cases['Yes']['option'], [f"{z['cn']}队友"])
            entry = self.nodes[P + 'TeamEntry']
            self.assertEqual(entry['anchor'], {'TeamLeaderFinishTarget': P + 'Finish',
                                               'TeamLeaderNoResourceTarget': P + 'NoResource'})
            self.assertIn('TeamLeaderOpen', entry['next'])
            self.assertFalse(self.nodes[P + 'TeamResume']['enabled'])
            self.assertEqual(self.nodes[P + 'RoomTargetDungeon']['recognition']['param']['expected'], z['cn'])

    def test_stage_pages_are_distinct_and_checked_before_the_orochi_stage(self):
        for P, z, task in self.each():
            stage = self.nodes[f'Page{P}Stage']['recognition']['param']['all_of']
            self.assertIn(P + 'ChallengeButton', stage)
            self.assertLess(page_state.PAGES.index(P + 'Stage'), page_state.PAGES.index('MitamaStage'))
        self.assertEqual(self.nodes['PageFallenSunStage']['recognition']['param']['all_of'][1]['recognition']['param']['expected'], '^日蚀$')
        # The orochi floor list also shows 肆层, so EternitySea is told apart by its 30-play weekly counter.
        self.assertIn('/30', self.nodes['PageEternitySeaStage']['recognition']['param']['all_of'][1]['recognition']['param']['expected'])

    def test_play_limit_ends_normally_and_stamina_shortage_fails(self):
        for P, z, task in self.each():
            cap = self.nodes[P + 'LimitFull']['recognition']['param']
            self.assertEqual(cap['expected'], z['cap'])
            self.assertTrue(cap['only_rec'])
            entry = self.nodes[P + 'BattleEntry']['next']
            self.assertLess(entry.index(P + 'LimitReached'), entry.index(P + 'Challenge'))
            self.assertLess(entry.index(P + 'NoResource'), entry.index(P + 'Challenge'))
            self.assertEqual(self.nodes[P + 'LimitReached']['next'][0], P + 'Finish')
            self.assertEqual(self.nodes[P + 'NoResource']['next'][0], 'NativeFlowFailed')
            self.assertEqual(self.nodes[P + 'NoResource']['recognition']['param']['all_of'], ['DailyNoResource'])
            self.assertIn(P + 'NoResource', self.nodes[P + 'WaitBattleStart']['next'])
            self.assertIn(P + 'NoResource', self.nodes[P + 'Battle']['next'])

    def test_navigation_checks_target_before_switching_tab_or_opening_panel(self):
        for P, z, task in self.each():
            nxt = self.nodes['Navigate' + P]['next']
            at = nxt.index('NativeAt' + P)
            self.assertLess(at, nxt.index(f'[JumpBack]NativeTo{P}SwitchTab'))
            self.assertLess(at, nxt.index(f'[JumpBack]NativeTo{P}OpenPanel'))
            self.assertIn('[JumpBack]NativeToMitamaStageNavOpenMitama', nxt)

    def test_success_counted_only_after_returning_to_selected_floor_and_no_buff(self):
        for P, z, task in self.each():
            self.assertNotIn(P + 'AfterResult', self.nodes[P + 'Battle']['next'])
            self.assertIn(P + 'AfterResult', self.nodes[P + 'BattleResult']['next'])
            self.assertEqual(self.nodes[P + 'AfterResult']['recognition']['param']['all_of'],
                             [f'Page{P}Stage', P + 'LayerSelected'])
            self.assertFalse([n for n in self.nodes if n.startswith(P) and 'Buff' in n])


if __name__ == '__main__':
    unittest.main()
