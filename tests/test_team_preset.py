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
        for prefix,context in [('Mitama','PageMitamaStage'),('Awakening','PageAwakeningStage')]:
            node=self.nodes[prefix+'Preset']
            self.assertFalse(node['enabled'])
            self.assertEqual(node['anchor'],{'TeamPresetReturn':prefix+'PresetDone'})
            override=node['action']['param']['custom_action_param']
            self.assertEqual(override['TeamPresetContext']['recognition']['param']['all_of'],[context])
            self.assertEqual(self.nodes[prefix+'PresetDone']['action'],'DoNothing')

    def test_awakening_order_and_buff_options(self):
        # Native chain: each finished step lists the remaining steps; disabled ones are skipped.
        bounty='[JumpBack]CommonBountyInterrupt'
        self.assertEqual(self.nodes['AwakeningPreparation']['next'],
                         [bounty,'AwakeningLockTeam','AwakeningPreset','AwakeningEnableBuff','AwakeningBattleModeDestination'])
        self.assertEqual(self.nodes['AwakeningLockedTeam']['next'],[bounty,'AwakeningPreset','AwakeningEnableBuff','AwakeningBattleModeDestination'])
        self.assertEqual(self.nodes['AwakeningPresetDone']['next'],[bounty,'AwakeningEnableBuff','AwakeningBattleModeDestination'])
        self.assertEqual(self.nodes['AwakeningBuffClosedForPreparation']['next'],[bounty,'AwakeningBattleModeDestination'])
        task=load_jsonc(ROOT/'assets/tasks/Awakening.json')
        self.assertIn('觉醒材料加成',task['task'][0]['option'])
        self.assertIn('觉醒更换队伍预设',task['task'][0]['option'])
        self.assertEqual(task['option']['觉醒材料加成']['default_case'],'Yes')

    def test_mitama_and_awakening_lock_options_match(self):
        bounty='[JumpBack]CommonBountyInterrupt'
        self.assertEqual(self.nodes['MitamaPreparation']['next'],
                         [bounty,'MitamaLockTeam','MitamaPreset','MitamaEnableBuff','MitamaPreparationDestination'])
        for prefix,label in (('Mitama','御魂'),('Awakening','觉醒')):
            task=load_jsonc(ROOT/f'assets/tasks/{prefix}.json')
            options=task['task'][0]['option']
            # Lock comes before preset, matching the native preparation order.
            self.assertLess(options.index(label+'锁定阵容'),options.index(label+'更换队伍预设'))
            option=task['option'][label+'锁定阵容']
            self.assertEqual(option['default_case'],'Yes')
            self.assertEqual({c['name']:c['pipeline_override'] for c in option['cases']},
                             {'No':{prefix+'LockTeam':{'enabled':False}},'Yes':{prefix+'LockTeam':{'enabled':True}}})
            self.assertEqual(self.nodes[prefix+'LockedTeam']['recognition']['param']['template'] if prefix=='Awakening'
                             else self.nodes[prefix+'LockedTeam']['recognition']['param']['template'][0],'Common/FormationLocked.png')
            click=self.nodes['SafeInput_'+prefix+'UnlockTeam']
            self.assertEqual((click['max_hit'],click['post_delay'],click['next']),(3,1000,[]))

    def test_options_update_all_group_and_team_recognitions(self):
        for prefix in ('Mitama','Awakening'):
            options=load_jsonc(ROOT/f'assets/tasks/{prefix}.json')['option']
            task_name={'Mitama':'御魂','Awakening':'觉醒'}[prefix]
            switch=options[task_name+'更换队伍预设']
            self.assertEqual(switch['default_case'],'No')
            selected=options[switch['cases'][1]['option'][0]]['pipeline_override']
            self.assertEqual(set(selected),{'TeamPresetSelectGroup','TeamPresetGroupSelected','TeamPresetSelectTeamConfigured','TeamPresetApplyIcon'})
            self.assertEqual(selected['TeamPresetApplyIcon']['recognition']['param']['all_of'][0]['recognition']['param']['roi'],[544,160,376,320])

    def test_shared_flow_verifies_window_before_and_after_input(self):
        self.assertEqual(self.nodes['TeamPresetWindow']['recognition']['param']['all_of'],['TeamPresetContext','TeamPresetFrame','TeamPresetFooter'])
        self.assertTrue(self.nodes['TeamPresetWindowClosed']['inverse'])
        self.assertEqual(self.nodes['SafeInput_TeamPresetOut']['next'],['TeamPresetVerifyClosed'])
        self.assertTrue(is_control_route(self.nodes['SafeInput_TeamPresetOut']['next'],self.nodes.get))
        self.assertEqual(self.nodes['SafeInput_TeamPresetClose']['next'],['TeamPresetVerifyClosed'])
        self.assertEqual(self.nodes['TeamPresetVerifyClosed']['next'][:1],['[JumpBack]CommonBountyInterrupt'])
        self.assertEqual(self.nodes['TeamPresetDone']['next'],["[JumpBack]CommonBountyInterrupt",'[Anchor]TeamPresetReturn'])
        self.assertEqual(self.nodes['TeamPresetOpen']['max_hit'],3)
        self.assertEqual(self.nodes['TeamPresetOut']['max_hit'],3)
        self.assertEqual(self.nodes['TeamPresetClose']['max_hit'],3)

    def test_common_activity_has_no_team_preset(self):
        self.assertNotIn('CommonActivityPreset',self.nodes)
        self.assertNotIn('CommonActivityPresetDone',self.nodes)
        self.assertEqual(self.nodes['CommonActivityPreparation']['next'],
                         ['[JumpBack]CommonBountyInterrupt','SafeInput_CommonActivityChallenge'])
        task=load_jsonc(ROOT/'assets/tasks/CommonActivity.json')
        self.assertEqual(task['task'][0]['option'],['重复挑战自定义作战次数'])
        self.assertNotIn('重复挑战更换队伍预设',task['option'])

    def test_group_and_team_lists_are_searched_with_bezier_swipes(self):
        bounty='[JumpBack]CommonBountyInterrupt'
        self.assertEqual(self.nodes['TeamPresetWindowReady']['next'][-1],'TeamPresetGroupRemember')
        self.assertEqual(self.nodes['TeamPresetGroupSelected']['next'],[bounty,'TeamPresetFindTeam'])
        self.assertEqual(self.nodes['TeamPresetFindTeam']['next'],[bounty,'SafeInput_TeamPresetSelectTeamConfigured','TeamPresetTeamRemember'])
        resets=self.nodes['TeamPresetRun']['action']['param']['custom_action_param']['nodes']
        for side,roi,budget in (('Group',[411,161,112,372],5),('Team',[544,160,376,320],6)):
            remember=self.nodes[f'TeamPreset{side}Remember']
            self.assertEqual(remember['action']['param']['custom_action'],'RememberFrame')
            self.assertEqual(remember['max_hit'],budget)
            self.assertIn(f'TeamPreset{side}Remember',resets)
            swipe=self.nodes[f'SafeInput_TeamPreset{side}Scroll']['action']['param']
            self.assertEqual(swipe['custom_action'],'BezierSwipe')
            begin,end=swipe['custom_action_param']['begin'],swipe['custom_action_param']['end']
            for x,y,w,h in (begin,end):
                self.assertTrue(roi[0]<=x and x+w<=roi[0]+roi[2] and roi[1]<=y and y+h<=roi[1]+roi[3],side)
            self.assertGreater(begin[1],end[1])
            bottom=self.nodes[f'TeamPreset{side}AtBottom']['recognition']['param']['custom_recognition_param']
            self.assertEqual(bottom['regions'],[roi])
            after=self.nodes[f'TeamPreset{side}AfterScroll']['next']
            self.assertEqual(after[-3:],[f'TeamPreset{side}AtBottom',f'TeamPreset{side}Remember',f'TeamPreset{side}Missing'])
            self.assertEqual(self.nodes[f'TeamPreset{side}Missing']['next'],['NativeFlowFailed'])

    def test_solo_sorties_and_team_mode_closes_after_in_use(self):
        # Solo needs 出战 to set the lineup; team mode only applies the preset mitama.
        bounty='[JumpBack]CommonBountyInterrupt'
        self.assertEqual(self.nodes['TeamPresetInUse']['next'],[bounty,'TeamPresetOut','TeamPresetShortTeam'])
        self.assertEqual(self.nodes['TeamPresetCloseRetry']['next'],[bounty,'TeamPresetWindowClosed','TeamPresetOut'])
        self.assertEqual(self.nodes['TeamPresetShortTeam']['next'],['NativeFlowFailed'])
        # Blank area right of the window; the window's scrollbar sits at x 938.
        x,y,w,h=self.nodes['SafeInput_TeamPresetClose']['action']['param']['target']
        self.assertEqual([x,y,w,h],[969,424,284,120])
        self.assertGreater(x,960)      # outside the window's right frame
        self.assertLess(y+h,560)       # above the 组队/挑战 buttons
        self.assertLessEqual(x+w,1280)
        for rel,switch in (('Mitama','是否组队'),('Awakening','觉醒组队')):
            cases={c['name']:c['pipeline_override'] for c in load_jsonc(ROOT/f'assets/tasks/{rel}.json')['option'][switch]['cases']}
            self.assertEqual(cases['Yes']['TeamPresetInUse']['next'],[bounty,'TeamPresetClose'])
            self.assertEqual(cases['Yes']['TeamPresetCloseRetry']['next'],[bounty,'TeamPresetWindowClosed','TeamPresetClose'])
            self.assertNotIn('TeamPresetInUse',cases['No'])

    def test_window_is_recognized_by_either_footer(self):
        # Screenshot: a 4-shikigami team shows 预设队伍中式神数量必须满5人才能出战 where 出战 would be.
        self.assertEqual(self.nodes['TeamPresetWindow']['recognition']['param']['all_of'],['TeamPresetContext','TeamPresetFrame','TeamPresetFooter'])
        footer=self.nodes['TeamPresetFooter']['recognition']['param']
        self.assertEqual(footer['expected'],['出战','必须满'])
        x,y,w,h=footer['roi']; ox,oy,ow,oh=self.nodes['TeamPresetOut']['recognition']['param']['roi']
        self.assertTrue(x<=578 and x+w>=890 and x<=ox and x+w>=ox+ow and y<=oy and y+h>=oy+oh)
        self.assertIn('TeamPresetFooter',self.nodes['MitamaLockPresetWindow']['recognition']['param']['all_of'])
        # RealmRaid's window sits 226 right and 21 lower; every new area moves with it.
        raid=self.nodes['RealmRaidPreset']['action']['param']['custom_action_param']
        dx,dy=226,21
        self.assertEqual(raid['TeamPresetFooter']['recognition']['param']['roi'],[x+dx,y+dy,w,h])
        for side in ('Group','Team'):
            base=self.nodes[f'TeamPreset{side}AtBottom']['recognition']['param']['custom_recognition_param']['regions'][0]
            moved=raid[f'TeamPreset{side}AtBottom']['recognition']['param']['custom_recognition_param']['regions'][0]
            self.assertEqual(moved,[base[0]+dx,base[1]+dy,base[2],base[3]])
            swipe=self.nodes[f'SafeInput_TeamPreset{side}Scroll']['action']['param']['custom_action_param']
            raid_swipe=raid[f'SafeInput_TeamPreset{side}Scroll']['action']['param']['custom_action_param']
            for key in ('begin','end'):
                bx,by,bw,bh=swipe[key]
                self.assertEqual(raid_swipe[key],[bx+dx,by+dy,bw,bh])
            self.assertEqual({k:v for k,v in raid_swipe.items() if k not in ('begin','end')},
                             {k:v for k,v in swipe.items() if k not in ('begin','end')})

    def test_in_use_toast_is_inside_its_ocr_area(self):
        # Log 2026-11-13 00:47: 全队正在使用预设御魂 at [502,237,273,19]; the old [440,160,400,55] missed it.
        # Screenshot 2026-11-13 03:14: stacked with 替换阴阳师契灵成功 [519,222,241,31], it moves up to about (470,175,340,30).
        x,y,w,h=self.nodes['TeamPresetInUse']['recognition']['param']['roi']
        for bx,by,bw,bh in ((502,237,273,19),(470,175,340,30)):
            self.assertTrue(x<=bx and x+w>=bx+bw and y<=by and y+h>=by+bh,(bx,by))
        self.assertIn('全队正在使用',self.nodes['TeamPresetInUse']['recognition']['param']['expected'])

