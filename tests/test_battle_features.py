"""Contracts for task overrides and shared battle safety boundaries."""
import unittest
from pathlib import Path
from tools.validate_schema import load_jsonc

ROOT=Path(__file__).resolve().parents[1]


class BattleFeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes={}
        for p in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
            if p.name!='Example.json':cls.nodes.update(load_jsonc(p))

    def test_all_soul_floor_options_feed_the_same_selected_text(self):
        d=load_jsonc(ROOT/'assets/tasks/Mitama.json')
        selected=self.nodes['MitamaLayerSelected']['recognition']['param']['all_of']
        self.assertEqual(selected[1]['recognition']['param']['all_of'],['MitamaSelectBaqin'])
        self.assertEqual(selected[2]['recognition']['param']['roi'],'MitamaSelectedLayerText')
        for case in d['option']['选择关卡']['cases']:
            self.assertIn('expected',case['pipeline_override']['MitamaSelectBaqin']['recognition']['param'])
        for node in ('MitamaChallenge','MitamaFinishOnce'):
            self.assertIn('MitamaLayerSelected',self.nodes[node]['recognition']['param']['all_of'])
        self.assertEqual(self.nodes['SafeInput_MitamaSelectBaqin']['next'],['MitamaVerifyLayer'])
        self.assertEqual(self.nodes['SafeInput_MitamaSelectBaqin']['max_hit'],3)

    def test_both_tasks_offer_six_guarded_stations_and_default_off(self):
        for prefix,label in [('Mitama','御魂'),('Awakening','觉醒')]:
            d=load_jsonc(ROOT/'assets/tasks'/f'{prefix}.json')
            self.assertEqual(d['option'][label+'友方绿标']['default_case'],'No')
            cases=d['option'][label+'绿标站位']['cases']
            self.assertEqual({c['name'] for c in cases},{'左一','左二','左三','左四','左五','阴阳师'})
            for case in cases:
                region=case['pipeline_override']['SafeInput_CommonBattleMark']['action']['param']['target']
                self.assertEqual(len(region),4)
                self.assertGreater(region[2],1);self.assertGreater(region[3],1)
        self.assertEqual(self.nodes['CommonBattleMark']['recognition']['param']['all_of'],['PageBattle'])
        self.assertEqual(self.nodes['CommonBattleMark']['max_hit'],1)

    def test_mark_is_reset_in_solo_leader_member_and_awakening(self):
        for entry in ('MitamaBattleEntry','MitamaLeaderBattleEntry','MitamaMemberBattleEntry','AwakeningBattleEntry'):
            reset=self.nodes[entry]['action']['param']['custom_action_param']['nodes']
            self.assertIn('CommonBattleMark',reset)
            self.assertIn('SafeInput_CommonBattleMark',reset)
        for loop in ('MitamaBattle','MitamaNativeBattle','AwakeningBattle'):
            choices=self.nodes[loop]['next']
            self.assertLess(choices.index('[JumpBack]CommonBattleEnableAuto'),choices.index('[JumpBack]CommonBattleMark'))
            self.assertLess(choices.index('[JumpBack]CommonBattleMark'),choices.index('[JumpBack]CommonBattleWait'))

    def test_recovery_uses_native_bounded_wait_and_no_success_counter(self):
        for prefix in ('Mitama','Awakening'):
            choices=self.nodes[prefix+'AbortRecover']['next']
            self.assertEqual(choices[:2],['[JumpBack]CommonBountyInterrupt','[JumpBack]NativeNavCancelNoResource'])
            self.assertNotIn(prefix+'AfterResult',choices)
            self.assertIn('[JumpBack]BattleCleanupWait',choices)
            limits=self.nodes[prefix+'Session']['action']['param']['custom_action_param']['cleanup_override']
            resets=self.nodes[prefix+'AbortCleanup']['action']['param']['custom_action_param']['nodes']
            for node in resets:
                if node!='BattleCleanupWait':self.assertEqual(limits[node],{'max_hit':3})
        wait=self.nodes['BattleCleanupWait']
        self.assertEqual(wait['recognition']['param']['all_of'],['PageBattle'])
        self.assertEqual(wait['max_hit'],180)
        self.assertEqual(wait['post_delay'],1000)
        self.assertEqual(self.nodes['SafeInput_BattleCleanupResult']['next'],[])


if __name__=='__main__':unittest.main()
