"""Shared native preset dependencies and caller-specific configuration."""
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc
from custom.route_policy import is_control_route

ROOT=Path(__file__).resolve().parents[1]


class TeamPresetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes={}
        for path in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
            if path.name!='Example.json':cls.nodes.update(load_jsonc(path))

    def test_each_task_configures_context_and_own_return(self):
        for prefix,context in [('Awakening','PageAwakeningStage'),('CommonActivity','CommonActivityChallenge'),('CurrentActivity','CurrentActivityChallenge')]:
            node=self.nodes[prefix+'Preset']
            self.assertFalse(node['enabled'])
            self.assertEqual(node['anchor'],{'TeamPresetReturn':prefix+'PresetDone'})
            override=node['action']['param']['custom_action_param']
            self.assertEqual(override['TeamPresetContext']['recognition']['param']['all_of'],[context])
            self.assertEqual(self.nodes[prefix+'PresetDone']['action']['param']['custom_action_param']['node_name'],prefix+'Preset')

    def test_awakening_order_and_buff_options(self):
        params=self.nodes['AwakeningPreparation']['action']['param']['custom_action_param']
        self.assertEqual(params['sub'],['AwakeningLockTeam','AwakeningPreset','AwakeningEnableBuff'])
        task=load_jsonc(ROOT/'assets/tasks/Awakening.json')
        self.assertIn('觉醒材料加成',task['task'][0]['option'])
        self.assertIn('觉醒更换队伍预设',task['task'][0]['option'])
        self.assertEqual(task['option']['觉醒材料加成']['default_case'],'No')

    def test_options_update_all_group_and_team_recognitions(self):
        for prefix in ('Awakening','CommonActivity','CurrentActivity'):
            options=load_jsonc(ROOT/f'assets/tasks/{prefix}.json')['option']
            task_name={'Awakening':'觉醒','CommonActivity':'重复挑战','CurrentActivity':'当期活动'}[prefix]
            switch=options[task_name+'更换队伍预设']
            self.assertEqual(switch['default_case'],'No')
            selected=options[switch['cases'][1]['option'][0]]['pipeline_override']
            self.assertEqual(set(selected),{'TeamPresetSelectGroup','TeamPresetGroupSelected','TeamPresetSelectTeamConfigured','TeamPresetApplyIcon'})
            self.assertEqual(selected['TeamPresetApplyIcon']['recognition']['param']['all_of'][0]['recognition']['param']['roi'],[544,160,376,320])

    def test_shared_flow_verifies_window_before_and_after_input(self):
        self.assertEqual(self.nodes['TeamPresetWindow']['recognition']['param']['all_of'],['TeamPresetContext','TeamPresetFrame','TeamPresetOut'])
        self.assertTrue(self.nodes['TeamPresetWindowClosed']['inverse'])
        self.assertEqual(self.nodes['SafeInput_TeamPresetOut']['next'],['TeamPresetVerifyClosed'])
        self.assertTrue(is_control_route(self.nodes['SafeInput_TeamPresetOut']['next'],self.nodes.get))
        self.assertEqual(self.nodes['TeamPresetVerifyClosed']['next'][:1],['[JumpBack]CommonBountyInterrupt'])
        self.assertEqual(self.nodes['TeamPresetDone']['next'],["[JumpBack]CommonBountyInterrupt",'[Anchor]TeamPresetReturn'])
        self.assertEqual(self.nodes['TeamPresetOpen']['max_hit'],3)
        self.assertEqual(self.nodes['TeamPresetOut']['max_hit'],3)

    def test_activity_repeats_skip_completed_preset_not_preparation_gate(self):
        for prefix in ('CommonActivity','CurrentActivity'):
            params=self.nodes[prefix+'FinishOnce']['action']['param']['custom_action_param']
            self.assertEqual(params['continue_node'],prefix+'Preparation')
            self.assertEqual(self.nodes[prefix+'Preparation']['action']['param']['custom_action_param']['sub'],[prefix+'Preset'])
            self.assertEqual(self.nodes[prefix+'Preparation']['action']['param']['custom_action_param']['completion_next'],['SafeInput_'+prefix+'Challenge'])

