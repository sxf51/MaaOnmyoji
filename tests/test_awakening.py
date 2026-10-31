"""Awakening configuration, navigation and settlement safety contracts."""
import re
import sys
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from custom import page_state

class AwakeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes={}
        for path in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
            if path.name!='Example.json':cls.nodes.update(load_jsonc(path))
        cls.task=load_jsonc(ROOT/'assets/tasks/Awakening.json')

    def test_task_is_imported_and_four_types_are_complete(self):
        interface=load_jsonc(ROOT/'assets/interface.json')
        self.assertIn('tasks/Awakening.json',interface['import'])
        cases=self.task['option']['觉醒麒麟类型']['cases']
        self.assertEqual({case['name'] for case in cases},{'火麒麟','风麒麟','水麒麟','雷麒麟'})
        for case in cases:
            override=case['pipeline_override']
            expected=override['AwakeningTargetType']['recognition']['param']['expected']
            self.assertIsNotNone(re.fullmatch(expected,case['name']))
            for node in ('SafeInput_AwakeningOpenType','SafeInput_AwakeningSwitchType'):
                region=override[node]['action']['param']['target']
                self.assertGreater(region[2],1)
                self.assertGreater(region[3],1)

    def test_floor_options_update_both_target_and_selected_checks(self):
        cases=self.task['option']['觉醒层数']['cases']
        self.assertEqual(len(cases),10)
        for case in cases:
            override=case['pipeline_override']
            expected=override['AwakeningLayerText']['recognition']['param']['expected']
            selected=override['AwakeningLayerSelected']['recognition']['param']['all_of']
            self.assertEqual(selected[:2],['PageAwakeningStage','AwakeningTargetType'])
            self.assertEqual(selected[2]['recognition']['param']['expected'],expected)
            self.assertIsNotNone(re.fullmatch(expected,case['name']))
            self.assertEqual(selected[2]['recognition']['param']['roi'],[125,85,120,615])
            self.assertEqual(selected[3]['recognition']['type'],'ColorMatch')

    def test_fight_requires_selected_floor_and_type(self):
        params=self.nodes['AwakeningChallenge']['recognition']['param']
        self.assertIn('AwakeningLayerSelected',params['all_of'])
        selected=self.nodes['AwakeningLayerSelected']['recognition']['param']['all_of']
        self.assertIn('AwakeningTargetType',selected)
        self.assertIn('PageAwakeningStage',selected)
        self.assertEqual(params['all_of'][params['box_index']],'AwakeningChallengeButton')

    def test_success_only_after_settlement_and_correct_stage(self):
        self.assertNotIn('AwakeningAfterResult',self.nodes['AwakeningBattle']['next'])
        self.assertIn('AwakeningAfterResult',self.nodes['SafeInput_AwakeningBattleResult']['next'])
        self.assertEqual(self.nodes['AwakeningAfterResult']['recognition']['param']['all_of'],
            ['PageAwakeningStage','AwakeningLayerSelected'])
        self.assertEqual(self.nodes['AwakeningLayerSelected']['recognition']['param']['all_of'][:2],
            ['PageAwakeningStage','AwakeningTargetType'])
        self.assertIn('NativeFlowFailed',self.nodes['AwakeningFailedReturn']['next'])

    def test_retry_limit_resets_per_battle(self):
        reset=self.nodes['AwakeningBattleEntry']['action']['param']['custom_action_param']['nodes']
        self.assertIn('SafeInput_AwakeningChallenge',reset)
        self.assertEqual(self.nodes['SafeInput_AwakeningChallenge']['max_hit'],3)
        params=self.nodes['AwakeningAfterResult']['action']['param']['custom_action_param']
        self.assertEqual(params['continue_node'],'AwakeningBattleEntry')

    def test_active_battle_regex_is_shared_and_handles_merged_skill_labels(self):
        param=self.nodes['PageBattle']['recognition']['param']
        for label in ('妖术','普攻','妖术普攻妖术','妖 术','普 攻','妖术参'):
            self.assertIsNotNone(re.search(param['expected'],label),label)
        for label in ('准备','加成','点击屏幕继续'):
            self.assertIsNone(re.search(param['expected'],label),label)
        self.assertEqual(param['roi'],[600,676,680,44])
        for name in ('AwakeningChallenging','MitamaChallenging','CommonActivityChallenging','CurrentActivityChallenging'):
            self.assertEqual(self.nodes[name]['recognition']['param']['all_of'],
                             ['CommonBattleWait'] if name in ('AwakeningChallenging','MitamaChallenging') else ['PageBattle'])

    def test_deployment_wait_precedes_preparation_and_never_counts_or_clicks(self):
        choices=self.nodes['AwakeningBattle']['next']
        self.assertLess(choices.index('[JumpBack]CommonBattleEnteringWait'),choices.index('[JumpBack]DailyPrepare'))
        self.assertIn('[JumpBack]CommonBattleWait',choices)
        wait=self.nodes['CommonBattleEnteringWait']
        self.assertEqual(wait['action'],'DoNothing')
        self.assertEqual(wait['timeout'],60000)
        self.assertEqual(wait['next'],['[JumpBack]CommonBountyInterrupt','CommonBattleEnteringGone'])
        self.assertTrue(self.nodes['CommonBattleEnteringGone']['inverse'])
        for name in ('PageLoading','PageBattleEntering'):
            self.assertIn(name,self.nodes['AwakeningBattleStarted']['recognition']['param']['any_of'])

    def test_navigation_and_page_detection_include_awakening(self):
        self.assertIn('AwakeningMenu',page_state.PAGES)
        self.assertIn('AwakeningStage',page_state.PAGES)
        self.assertIn('NativeAtAwakening',self.nodes['NavigateAwakening']['next'])

    def test_buff_is_optional_and_checks_only_the_awakening_row(self):
        option=self.task['option']['觉醒材料加成']
        self.assertEqual(option['default_case'],'Yes')
        enabled=option['cases'][1]['pipeline_override']
        self.assertTrue(enabled['AwakeningEnableBuff']['enabled'])
        self.assertTrue(enabled['AwakeningDisableBuff']['enabled'])
        for name,state in [('AwakeningEnableBuffClick','AwakeningBuffOff'),('AwakeningDisableBuffClick','AwakeningBuffOn')]:
            self.assertEqual(self.nodes[name]['recognition']['param']['all_of'],[state])
        self.assertEqual(self.nodes['AwakeningBuffPanel']['recognition']['param']['expected'],'^觉醒副本掉落额外的觉醒材料$')
        self.assertIn('AwakeningFinishCleanup',self.nodes['AwakeningFinish']['next'])

if __name__=='__main__':unittest.main()
