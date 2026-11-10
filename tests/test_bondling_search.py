"""契灵之境-探查: configuration, OCR search button and settlement safety contracts."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom import page_state

BOUNTY = '[JumpBack]CommonBountyInterrupt'


class BondlingSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            if path.name != 'Example.json':
                cls.nodes.update(load_jsonc(path))
        cls.task = load_jsonc(ROOT / 'assets/tasks/BondlingSearch.json')

    def test_task_is_imported_without_buff_or_team_options(self):
        interface = load_jsonc(ROOT / 'assets/interface.json')
        self.assertIn('tasks/BondlingSearch.json', interface['import'])
        entry = self.task['task'][0]
        self.assertEqual(entry['entry'], 'BondlingSearchTask')
        self.assertEqual(entry['option'], ['契灵探查次数', '契灵锁定阵容', '契灵更换队伍预设', '契灵友方绿标'])
        for name in self.task['option']:
            self.assertNotIn('加成', name)
            self.assertNotIn('组队', name)
        own = [n for n in self.nodes if n.startswith(('BondlingSearch', 'SafeInput_BondlingSearch'))]
        self.assertFalse([n for n in own if 'Buff' in n or 'TeamEntry' in n or 'ModeDestination' in n])

    def test_search_button_is_ocr_and_gates_the_click(self):
        button = self.nodes['BondlingSearchButton']['recognition']
        self.assertEqual(button['type'], 'OCR')
        self.assertIsNotNone(re.fullmatch(button['param']['expected'], '探查'))
        self.assertIsNone(re.fullmatch(button['param']['expected'], '挑战'))
        param = self.nodes['BondlingSearchStart']['recognition']['param']
        self.assertEqual(param['all_of'], ['PageBondling', 'BondlingSearchButton'])
        self.assertEqual(param['all_of'][param['box_index']], 'BondlingSearchButton')
        self.assertIs(self.nodes['SafeInput_BondlingSearchStart']['action']['param']['target'], True)

    def test_preparation_order_is_lock_then_preset_then_battle(self):
        self.assertEqual(self.nodes['BondlingSearchPreparation']['next'],
                         [BOUNTY, 'BondlingSearchLockTeam', 'BondlingSearchPreset', 'BondlingSearchBattleEntry'])
        self.assertEqual(self.nodes['BondlingSearchLockedTeam']['next'], [BOUNTY, 'BondlingSearchPreset', 'BondlingSearchBattleEntry'])
        self.assertEqual(self.nodes['BondlingSearchPresetDone']['next'], [BOUNTY, 'BondlingSearchBattleEntry'])
        preset = self.nodes['BondlingSearchPreset']
        self.assertFalse(preset['enabled'])
        self.assertEqual(preset['anchor'], {'TeamPresetReturn': 'BondlingSearchPresetDone'})
        override = preset['action']['param']['custom_action_param']
        self.assertEqual(override['TeamPresetContext']['recognition']['param']['all_of'], ['PageBondling'])
        lock = self.task['option']['契灵锁定阵容']
        self.assertEqual(lock['default_case'], 'Yes')
        self.assertEqual({c['name']: c['pipeline_override'] for c in lock['cases']},
                         {'No': {'BondlingSearchLockTeam': {'enabled': False}}, 'Yes': {'BondlingSearchLockTeam': {'enabled': True}}})

    def test_success_counts_only_after_settlement_returns_to_search_page(self):
        self.assertNotIn('BondlingSearchAfterResult', self.nodes['BondlingSearchBattle']['next'])
        self.assertIn('BondlingSearchAfterResult', self.nodes['SafeInput_BondlingSearchBattleResult']['next'])
        after = self.nodes['BondlingSearchAfterResult']
        self.assertEqual(after['recognition']['param']['all_of'], ['PageBondling'])
        params = after['action']['param']['custom_action_param']
        self.assertEqual((params['continue_node'], params['finish_node']), ('BondlingSearchBattleEntry', 'BondlingSearchFinish'))
        self.assertIn('NativeFlowFailed', self.nodes['BondlingSearchFailedReturn']['next'])

    def test_full_bondlings_end_after_bounded_clicks(self):
        # OAS click_search: three clicks that never leave the page mean five bondlings are waiting.
        self.assertEqual(self.nodes['SafeInput_BondlingSearchStart']['max_hit'], 3)
        reset = self.nodes['BondlingSearchBattleEntry']['action']['param']['custom_action_param']['nodes']
        for node in ('SafeInput_BondlingSearchStart', 'SafeInput_BondlingSearchBattleResult', 'SafeInput_BondlingSearchGatherLater'):
            self.assertIn(node, reset)
        wait = self.nodes['BondlingSearchWaitBattleStart']
        self.assertEqual(wait['on_error'], ['BondlingSearchFull'])
        self.assertEqual(self.nodes['BondlingSearchFull']['recognition']['param']['all_of'], ['PageBondling'])
        self.assertEqual(self.nodes['BondlingSearchFull']['next'], [BOUNTY, 'BondlingSearchFinish'])

    def test_navigation_and_page_detection_include_bondling(self):
        self.assertIn('Bondling', page_state.PAGES)
        self.assertLess(page_state.PAGES.index('Bondling'), page_state.PAGES.index('Explore'))
        self.assertIn('NativeAtBondling', self.nodes['NavigateBondling']['next'])
        title = self.nodes['PageBondling']['recognition']['param']['all_of'][0]['recognition']['param']['expected']
        self.assertIsNotNone(re.fullmatch(title, '契灵之境'))


if __name__ == '__main__':
    unittest.main()
