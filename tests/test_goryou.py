"""Goryou configuration, navigation and stop-condition contracts."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom import page_state


class GoryouTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            if path.name != 'Example.json':
                cls.nodes.update(load_jsonc(path))
        cls.task = load_jsonc(ROOT / 'assets/tasks/Goryou.json')

    def test_task_is_imported_with_four_spirits_defaulting_to_dark_dragon(self):
        interface = load_jsonc(ROOT / 'assets/interface.json')
        self.assertIn('tasks/Goryou.json', interface['import'])
        option = self.task['option']['御灵类型']
        self.assertEqual(option['default_case'], '暗神龙')
        cases = option['cases']
        self.assertEqual([c['name'] for c in cases], ['暗神龙', '暗白藏主', '暗黑豹', '暗孔雀'])
        for case in cases:
            override = case['pipeline_override']
            expected = override['GoryouTargetType']['recognition']['param']['expected']
            self.assertIsNotNone(re.fullmatch(expected, case['name']))
            for node in ('GoryouOpenType', 'GoryouSwitchType'):
                region = override[node]['action']['param']['target']
                self.assertGreater(region[2], 1)
                self.assertGreater(region[3], 1)

    def test_three_floors_default_third_and_update_both_checks(self):
        option = self.task['option']['御灵层数']
        self.assertEqual(option['default_case'], '叁层')
        cases = option['cases']
        self.assertEqual([c['name'] for c in cases], ['壹层', '贰层', '叁层'])
        roi = self.nodes['GoryouLayerText']['recognition']['param']['roi']
        for case in cases:
            override = case['pipeline_override']
            expected = override['GoryouLayerText']['recognition']['param']['expected']
            selected = override['GoryouLayerSelected']['recognition']['param']['all_of']
            self.assertEqual(selected[:2], ['PageGoryouStage', 'GoryouTargetType'])
            self.assertEqual(selected[2]['recognition']['param']['expected'], expected)
            self.assertEqual(selected[2]['recognition']['param']['roi'], roi)
            self.assertEqual(selected[3]['recognition']['type'], 'ColorMatch')
            self.assertIsNotNone(re.fullmatch(expected, case['name']))
        # Only three floors fit on one screen: no scrolling nodes.
        self.assertFalse([n for n in self.nodes if n.startswith('GoryouLayerScroll')])

    def test_challenge_button_roi_covers_the_left_shifted_button(self):
        roi = self.nodes['GoryouChallengeButton']['recognition']['param']['roi']
        # Live 1280x720 screenshot: the 挑战 text spans about x1052-1137, y600-635.
        self.assertLessEqual(roi[0], 1052)
        self.assertGreaterEqual(roi[0] + roi[2], 1137)
        self.assertLessEqual(roi[1], 600)
        self.assertGreaterEqual(roi[1] + roi[3], 635)
        self.assertIn('GoryouChallengeButton', self.nodes['PageGoryouStage']['recognition']['param']['all_of'])
        params = self.nodes['GoryouChallenge']['recognition']['param']
        self.assertEqual(params['all_of'][params['box_index']], 'GoryouChallengeButton')
        self.assertIn('GoryouLayerSelected', params['all_of'])

    def test_no_buff_no_team_and_no_stamina_handling(self):
        names = [n for n in self.nodes if n.startswith('Goryou')]
        for word in ('Buff', 'Team', 'Session', 'DropStatistics'):
            self.assertFalse([n for n in names if word in n and 'LockTeam' not in n
                              and 'LockedTeam' not in n and 'UnlockTeam' not in n], word)
        self.assertEqual(self.nodes['GoryouTask']['next'][0], 'GoryouNavigate')
        for name in names:
            for route in self.nodes[name].get('next', []):
                self.assertNotIn('DailyNoResource', route)

    def test_key_exhaustion_finishes_normally_before_each_challenge(self):
        entry = self.nodes['GoryouBattleEntry']['next']
        self.assertLess(entry.index('GoryouNoKey'), entry.index('GoryouChallenge'))
        self.assertEqual(self.nodes['GoryouNoKey']['recognition']['param']['all_of'],
                         ['PageGoryouStage', 'GoryouKeyEmpty'])
        self.assertEqual(self.nodes['GoryouNoKey']['next'][0], 'GoryouFinish')
        self.assertEqual(self.nodes['GoryouKeyEmpty']['recognition']['param']['expected'], '^0$')
        self.assertTrue(self.nodes['GoryouKeyEmpty']['recognition']['param']['only_rec'])
        self.assertEqual(self.nodes['GoryouFinish']['action']['param']['custom_action'], 'TargetCountFinish')
        self.assertIn('GoryouNoKeyPopup', self.nodes['GoryouWaitBattleStart']['next'])

    def test_success_counted_only_after_returning_to_selected_floor(self):
        self.assertNotIn('GoryouAfterResult', self.nodes['GoryouBattle']['next'])
        self.assertIn('GoryouAfterResult', self.nodes['GoryouBattleResult']['next'])
        self.assertEqual(self.nodes['GoryouAfterResult']['recognition']['param']['all_of'],
                         ['PageGoryouStage', 'GoryouLayerSelected'])
        params = self.nodes['GoryouAfterResult']['action']['param']['custom_action_param']
        self.assertEqual(params['continue_node'], 'GoryouBattleEntry')
        self.assertIn('NativeFlowFailed', self.nodes['GoryouFailedReturn']['next'])

    def test_navigation_and_page_detection_include_goryou(self):
        self.assertIn('GoryouMenu', page_state.PAGES)
        self.assertIn('GoryouStage', page_state.PAGES)
        self.assertIn('NativeAtGoryou', self.nodes['NavigateGoryou']['next'])
        self.assertTrue((ROOT / 'assets/resource/base/image/Navigation/GoryouEntry.png').is_file())


if __name__ == '__main__':
    unittest.main()
