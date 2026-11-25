"""Sougenbi configuration, navigation and stop-condition contracts."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom import page_state


class SougenbiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            if path.name != 'Example.json':
                cls.nodes.update(load_jsonc(path))
        cls.task = load_jsonc(ROOT / 'assets/tasks/Sougenbi.json')

    def test_task_is_imported_with_three_difficulties_defaulting_to_foolery(self):
        interface = load_jsonc(ROOT / 'assets/interface.json')
        self.assertIn('tasks/Sougenbi.json', interface['import'])
        option = self.task['option']['业原火难度']
        self.assertEqual(option['default_case'], '痴之阵')
        self.assertEqual([c['name'] for c in option['cases']], ['贪之阵', '嗔之阵', '痴之阵'])

    def test_each_difficulty_updates_text_selection_and_its_own_ticket_counter(self):
        roi = self.nodes['SougenbiLayerText']['recognition']['param']['roi']
        ticket_rois = []
        for case in self.task['option']['业原火难度']['cases']:
            override = case['pipeline_override']
            expected = override['SougenbiLayerText']['recognition']['param']['expected']
            self.assertIsNotNone(re.fullmatch(expected, case['name']))
            selected = override['SougenbiLayerSelected']['recognition']['param']['all_of']
            self.assertEqual(selected[0], 'PageSougenbiStage')
            self.assertEqual(selected[1]['recognition']['param']['expected'], expected)
            self.assertEqual(selected[1]['recognition']['param']['roi'], roi)
            self.assertEqual(selected[2]['recognition']['type'], 'ColorMatch')
            ticket_rois.append(override['SougenbiTicketEmpty']['recognition']['param']['roi'])
        # Three separate counters left to right along the top bar.
        self.assertEqual(len({tuple(r) for r in ticket_rois}), 3)
        self.assertEqual(sorted(ticket_rois), ticket_rois)
        default = self.nodes['SougenbiTicketEmpty']['recognition']['param']['roi']
        self.assertEqual(default, ticket_rois[2])
        self.assertFalse([n for n in self.nodes if n.startswith('SougenbiLayerScroll')])

    def test_stage_page_is_distinct_from_the_orochi_stage(self):
        stage = self.nodes['PageSougenbiStage']['recognition']['param']['all_of']
        self.assertIn('SougenbiChallengeButton', stage)
        self.assertEqual(stage[1]['recognition']['param']['expected'], '^嗔之阵$')
        roi = self.nodes['SougenbiChallengeButton']['recognition']['param']['roi']
        # Live screenshot: 挑战 text box about x1037-1164, y585-649.
        self.assertLessEqual(roi[0], 1037)
        self.assertGreaterEqual(roi[0] + roi[2], 1164)
        self.assertLess(page_state.PAGES.index('SougenbiStage'), page_state.PAGES.index('MitamaStage'))

    def test_navigation_checks_target_before_switching_tab_or_opening_panel(self):
        nxt = self.nodes['NavigateSougenbi']['next']
        at = nxt.index('NativeAtSougenbi')
        self.assertLess(at, nxt.index('[JumpBack]NativeToSougenbiSwitchTab'))
        self.assertLess(at, nxt.index('[JumpBack]NativeToSougenbiOpenPanel'))
        self.assertIn('[JumpBack]NativeToMitamaStageNavOpenMitama', nxt)
        self.assertEqual(self.nodes['NativeToSougenbiOpenPanel']['recognition']['param']['all_of'], ['PageMitamaMenu'])
        self.assertEqual(self.nodes['NativeToSougenbiSwitchTab']['recognition']['param']['all_of'], ['PageMitamaZoneTab'])

    def test_ticket_exhaustion_finishes_normally_before_each_challenge(self):
        entry = self.nodes['SougenbiBattleEntry']['next']
        self.assertLess(entry.index('SougenbiNoTicket'), entry.index('SougenbiChallenge'))
        self.assertEqual(self.nodes['SougenbiNoTicket']['recognition']['param']['all_of'],
                         ['PageSougenbiStage', 'SougenbiTicketEmpty'])
        self.assertEqual(self.nodes['SougenbiNoTicket']['next'][0], 'SougenbiFinish')
        self.assertEqual(self.nodes['SougenbiTicketEmpty']['recognition']['param']['expected'], '^0$')
        # Batched full-screen OCR detection misses the lone digit; read the ROI directly.
        self.assertTrue(self.nodes['SougenbiTicketEmpty']['recognition']['param']['only_rec'])
        wait = self.nodes['SougenbiWaitBattleStart']['next']
        self.assertLess(wait.index('SougenbiNoTicketToast'), wait.index('SougenbiChallenge'))
        self.assertLess(wait.index('SougenbiNoTicket'), wait.index('SougenbiChallenge'))
        self.assertEqual(self.nodes['SougenbiNoTicketToast']['next'][0], 'SougenbiFinish')

    def test_success_counted_only_after_returning_to_selected_difficulty(self):
        self.assertNotIn('SougenbiAfterResult', self.nodes['SougenbiBattle']['next'])
        self.assertIn('SougenbiAfterResult', self.nodes['SougenbiBattleResult']['next'])
        self.assertEqual(self.nodes['SougenbiAfterResult']['recognition']['param']['all_of'],
                         ['PageSougenbiStage', 'SougenbiLayerSelected'])
        self.assertIn('NativeFlowFailed', self.nodes['SougenbiFailedReturn']['next'])

    def test_no_buff_team_or_stamina_handling(self):
        names = [n for n in self.nodes if n.startswith('Sougenbi')]
        self.assertFalse([n for n in names if 'Buff' in n or 'Session' in n or 'DropStatistics' in n])
        for name in names:
            for route in self.nodes[name].get('next', []):
                self.assertNotIn('DailyNoResource', route)
                self.assertNotIn('TeamLeader', route)


if __name__ == '__main__':
    unittest.main()
