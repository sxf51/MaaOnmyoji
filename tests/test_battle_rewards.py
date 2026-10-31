import unittest
from pathlib import Path
from tools.validate_schema import load_jsonc
ROOT=Path(__file__).resolve().parents[1]
class BattleRewardContracts(unittest.TestCase):
 def setUp(self):
  self.nodes={k:v for p in (ROOT/'assets/resource/base/pipeline').glob('*.json') if p.name!='Example.json' for k,v in load_jsonc(p).items()}
 def test_mitama_retries_challenge_without_reselecting_layer(self):
  self.assertEqual(self.nodes['SafeInput_MitamaChallenge']['max_hit'],3)
  self.assertIn('SafeInput_MitamaChallenge',self.nodes['MitamaBattleEntry']['action']['param']['custom_action_param']['nodes'])
  self.assertEqual(self.nodes['MitamaFinishOnce']['action']['param']['custom_action_param']['continue_node'],'MitamaBattleEntry')
  self.assertNotIn('MitamaSelectBaqin',str(self.nodes['MitamaBattleEntry']))
 def test_auto_and_network_are_shared_by_both_modes(self):
  for name in ['MitamaBattle','MitamaNativeBattle','AwakeningBattle']:
   for branch in ['CommonBattleEnteringWait','CommonBattleWait','CommonBattleEnableAuto','CommonNetworkRetry']:
    self.assertIn('[JumpBack]'+branch,self.nodes[name]['next'])
  self.assertEqual(self.nodes['CommonNetworkRetry']['recognition']['param']['all_of'][0],'CommonNetworkPrompt')
  self.assertEqual(self.nodes['SafeInput_CommonNetworkRetry']['max_hit'],3)
 def test_buff_only_toggles_the_confirmed_opposite_state(self):
  for mode,state in [('Enable','Close'),('Disable','Open')]:
   reco=self.nodes['Mitama'+mode+'BuffClick']['recognition']['param']
   self.assertIn('MitamaCheckBuffStatus'+state,reco['all_of'])
   self.assertEqual(self.nodes['MitamaCheckBuffStatus'+state]['recognition']['param']['all_of'][2]['recognition']['param']['roi'],'MitamaBuffPanel')
  self.assertEqual(self.nodes['SafeInput_MitamaCloseBuffPanel']['next'],['MitamaCloseBuffPanelVerify'])
 def test_local_statistics_and_optional_reporting_are_independent(self):
  for scope in ['Mitama','Awakening']:
   label={'Mitama':'御魂','Awakening':'觉醒'}[scope]
   task=load_jsonc(ROOT/f'assets/tasks/{scope}.json')
   self.assertEqual(task['option'][label+'奖励掉落统计']['default_case'],'No')
   option=task['option'][label+'掉落统计上报']
   self.assertEqual(option['default_case'],'No')
   for case in option['cases']:
    self.assertEqual(set(case['pipeline_override']),{scope+'Task'})
 def test_statistics_runs_before_closing_successful_settlement(self):
  for scope,stats in [('Mitama','MitamaSoloDropStatistics'),('Awakening','AwakeningDropStatistics')]:
   self.assertIn(stats,self.nodes[scope+'BattleResult']['next'])
   param=self.nodes[stats]['action']['param']
   self.assertEqual(param['custom_action'],'RewardDropStatistics')
   self.assertEqual(param['custom_action_param']['scope'],scope)
   self.assertNotIn(stats,self.nodes[scope+'BattleFailure']['next'])

 def test_default_statistics_nodes_are_disabled_and_private_creation_is_verified(self):
  for name in ['MitamaSoloDropStatistics','MitamaTeamDropStatistics','AwakeningDropStatistics']:
   self.assertFalse(self.nodes[name]['enabled'])
  self.assertIn('MitamaTeamPrivacySelected',self.nodes['MitamaTeamCreate']['recognition']['param']['all_of'])
  self.assertEqual(self.nodes['SafeInput_MitamaCreateTeam']['next'],['MitamaVerifyTeamPrivacy'])
  self.assertEqual(self.nodes['SafeInput_MitamaTeamCreate']['next'],['MitamaWaitCreatedRoom'])

 def test_reward_capture_waits_for_reveal_before_matching(self):
  for name in ['MitamaSoloDropStatistics','MitamaTeamDropStatistics','AwakeningDropStatistics']:
   self.assertGreaterEqual(self.nodes[name]['pre_delay'],2000)
