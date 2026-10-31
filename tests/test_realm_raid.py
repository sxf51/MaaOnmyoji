"""Realm raid uses native visual recognition and remembers one selected box."""
import re
import unittest
from pathlib import Path
from tools.validate_schema import load_jsonc
ROOT=Path(__file__).resolve().parents[1]

class RaidTest(unittest.TestCase):
    def setUp(self):
        self.nodes=load_jsonc(ROOT/'assets/resource/base/pipeline/RealmRaid.json')
        self.safe=load_jsonc(ROOT/'assets/resource/base/pipeline/InputSafety.json')

    def test_card_uses_masked_native_template(self):
        reco=self.nodes['RealmRaidCard']['recognition']
        self.assertEqual(reco['type'],'TemplateMatch')
        self.assertTrue(reco['param']['green_mask'])
        self.assertTrue((ROOT/'assets/resource/base/image'/reco['param']['template']).is_file())

    def test_nickname_is_checked_only_inside_chosen_card(self):
        param=self.nodes['RealmRaidNickname']['recognition']['param']
        self.assertEqual(param['roi'],'RealmRaidChoose')
        self.assertEqual(param['roi_offset'],[90,15,-137,-80])
        self.assertIn('RealmRaidValidateChoice',self.nodes['RealmRaidChoose']['next'])
        self.assertIn('RealmRaidUnmarkedCorner',self.nodes['RealmRaidSelect']['recognition']['param']['all_of'])

    def test_selection_and_stamp_are_pinned_to_same_card(self):
        for name in ('RealmRaidSelectedCard','RealmRaidBroken'):
            self.assertEqual(self.nodes[name]['recognition']['param']['roi'],'RealmRaidChoose')
        self.assertIn('RealmRaidBroken',self.nodes['RealmRaidSuccess']['recognition']['param']['all_of'])
        offset=self.safe['SafeInput_RealmRaidSelect']['action']['param']['target_offset']
        self.assertEqual(offset,[90,30,-157,-85])

    def test_each_choice_resets_only_click_budgets(self):
        param=self.nodes['RealmRaidValidateChoice']['action']['param']['custom_action_param']
        self.assertEqual(param,{'nodes':['SafeInput_RealmRaidSelect','SafeInput_RealmRaidAttack',
                                         'RealmRaidBattleMark','SafeInput_RealmRaidBattleMark',
                                         'RealmRaidFailRetry'],'strict':True})

    def test_no_target_finish_requires_confirmed_page(self):
        self.assertEqual(self.nodes['RealmRaidExhaustedPage']['recognition']['param']['all_of'],['PageRaid'])
        self.assertTrue(self.nodes['RealmRaidExhausted']['inverse'])
        self.assertNotIn('RealmRaidExhausted',self.nodes['RealmRaidScan']['next'])
        self.assertIn('RealmRaidExhaustedConfirmed',self.nodes['RealmRaidExhausted']['next'])
        self.assertEqual(self.nodes['RealmRaidExhaustedConfirmed']['recognition']['param']['all_of'],['PageRaid'])

    def test_retreat_is_off_by_default_and_option_sets_both_budgets(self):
        for name in ('RealmRaidRetreatBegin','RealmRaidRetreatRetry'):
            self.assertEqual(self.nodes[name]['max_hit'],0)
        task=load_jsonc(ROOT/'assets/tasks/RealmRaid.json')
        option=task['option']['结界突破主动退出次数']
        self.assertIn('结界突破主动退出次数',task['task'][0]['option'])
        self.assertEqual(option['inputs'][0]['default'],'0')
        self.assertEqual(option['pipeline_override'],{'RealmRaidRetreatBegin':{'max_hit':'{退出次数}'},
                                                     'RealmRaidRetreatRetry':{'max_hit':'{退出次数}'}})

    def test_retreat_order_in_battle_loop(self):
        battle=self.nodes['RealmRaidBattle']['next']
        # The exit button only exists after the battle starts and strong teams win in ~5s: check it first.
        self.assertEqual(battle[:3],['[JumpBack]CommonBountyInterrupt','RealmRaidRetreatBegin','[JumpBack]RealmRaidBattleMark'])
        # MaaFW batches every OCR in a next list (~1.2s here): while the exit button shows, only templates run.
        self.assertEqual(battle,['[JumpBack]CommonBountyInterrupt','RealmRaidRetreatBegin',
                                 '[JumpBack]RealmRaidBattleMark','RealmRaidBattleSettle'])
        settle=self.nodes['RealmRaidBattleSettle']
        self.assertTrue(settle['inverse'])
        self.assertEqual(settle['recognition']['param']['all_of'],['RealmRaidBattleExitIcon'])
        self.assertEqual(settle['next'][-1],'RealmRaidBattleResume')
        self.assertEqual(self.nodes['RealmRaidBattleResume']['next'],['RealmRaidBattle'])
        slow=settle['next']
        self.assertLess(slow.index('RealmRaidRetreatRetry'),slow.index('RealmRaidBattleFailure'))
        self.assertNotIn('[JumpBack]CommonBattleWait',slow)
        self.assertLessEqual(self.nodes['RealmRaidBattle']['rate_limit'],300)
        for name in ('RealmRaidRetreatBegin','RealmRaidRetreatExit'):
            self.assertEqual(self.nodes[name]['recognition']['param']['all_of'],['RealmRaidBattleExitIcon'])
        self.assertEqual(self.nodes['RealmRaidRetreatBegin']['pre_delay'],0)
        self.assertEqual(self.nodes['RealmRaidRetreatBegin']['post_delay'],0)
        self.assertEqual(self.safe['SafeInput_RealmRaidRetreatExit']['post_delay'],0)
        templates=self.nodes['RealmRaidBattleExitIcon']['recognition']['param']['template']
        # One template per button skin, named BattleExit_<n>.png.
        self.assertEqual(templates,[f'RealmRaid/BattleExit_{i}.png' for i in range(1,7)])
        # The confirm dialog animates in for ~1.3s: wait for it before clicking the exit again.
        wait=self.nodes['RealmRaidRetreatExitWait']
        self.assertNotIn('SafeInput_RealmRaidRetreatExit',wait['next'])
        self.assertEqual(wait['on_error'],['RealmRaidRetreatExitRetry'])
        self.assertIn('SafeInput_RealmRaidRetreatExit',self.nodes['RealmRaidRetreatExitRetry']['next'])
        # A failure page that is still visible must not count as a restarted battle.
        self.assertNotIn('DailyBattleFailed',self.nodes['RealmRaidRetreatRestarted']['recognition']['param']['any_of'])

    def test_board_auto_refresh_after_ninth_win_still_counts(self):
        verify=self.nodes['RealmRaidVerify']['next']
        self.assertLess(verify.index('RealmRaidSuccess'),verify.index('RealmRaidSuccessBoardReset'))
        reset=self.nodes['RealmRaidSuccessBoardReset']
        self.assertEqual(reset['recognition']['param']['all_of'],
                         ['PageRaid','RealmRaidBreakRecordZero','RealmRaidSelectedCard','RealmRaidUnmarkedCorner'])
        self.assertEqual(reset['action'],self.nodes['RealmRaidSuccess']['action'])
        pattern=re.compile(self.nodes['RealmRaidBreakRecordZero']['recognition']['param']['expected'])
        for text,zero in [('0',True),('1',False),('3',False),('9',False),('10',False)]:
            self.assertEqual(bool(pattern.search(text)),zero,text)

    def test_mark_is_exclusive_with_retreat(self):
        mark=self.nodes['RealmRaidBattleMark']
        self.assertFalse(mark['enabled'])
        self.assertEqual(mark['max_hit'],1)
        # Same exit-button requirement as the retreat gate, which is listed first.
        self.assertEqual(mark['recognition']['param']['all_of'],['RealmRaidBattleExitIcon'])
        options=load_jsonc(ROOT/'assets/tasks/RealmRaid.json')['option']
        self.assertEqual(options['结界突破友方绿标']['default_case'],'No')
        cases=options['结界突破绿标站位']['cases']
        self.assertEqual({c['name'] for c in cases},{'左一','左二','左三','左四','左五','阴阳师'})
        for case in cases:
            self.assertEqual(list(case['pipeline_override']),['SafeInput_RealmRaidBattleMark'])

    def test_preset_runs_on_raid_page_before_scanning(self):
        self.assertEqual(self.nodes['RealmRaidReady']['next'],['[JumpBack]CommonBountyInterrupt','RealmRaidPreset','RealmRaidScan'])
        preset=self.nodes['RealmRaidPreset']
        self.assertFalse(preset['enabled'])
        self.assertEqual(preset['anchor'],{'TeamPresetReturn':'RealmRaidPresetDone'})
        override=preset['action']['param']['custom_action_param']
        self.assertEqual(override['TeamPresetContext']['recognition']['param']['all_of'],['PageRaid'])
        self.assertEqual(self.nodes['RealmRaidPresetDone']['next'][-1],'RealmRaidScan')
        selected=load_jsonc(ROOT/'assets/tasks/RealmRaid.json')['option']['结界突破预设分组与队伍']['pipeline_override']
        # The raid window is the Mitama window moved by (+226,+21).
        self.assertEqual(selected['TeamPresetSelectGroup']['recognition']['param']['roi'],[637,182,112,372])
        self.assertEqual(selected['TeamPresetApplyIcon']['recognition']['param']['all_of'][0]['recognition']['param']['roi'],[770,181,376,320])
        self.assertEqual(override['TeamPresetOut']['recognition']['param']['roi'],[894,503,120,44])

    def test_defeat_retry_is_per_card_and_after_retreat(self):
        battle=self.nodes['RealmRaidBattleSettle']['next']
        # Retreat failure pages come first; only a real defeat reaches the retry, then the failure options.
        self.assertLess(battle.index('RealmRaidRetreatRetry'),battle.index('RealmRaidFailRetry'))
        self.assertLess(battle.index('RealmRaidFailRetry'),battle.index('RealmRaidBattleFailure'))
        retry=self.nodes['RealmRaidFailRetry']
        self.assertEqual(retry['max_hit'],0)
        self.assertEqual(retry['recognition']['param']['all_of'],['RealmRaidRetreatAgain'])
        self.assertIn('RealmRaidBattleMark',retry['action']['param']['custom_action_param']['nodes'])
        option=load_jsonc(ROOT/'assets/tasks/RealmRaid.json')['option']['结界突破战败重试次数']
        self.assertEqual(option['inputs'][0]['default'],'0')
        self.assertEqual(option['pipeline_override'],{'RealmRaidFailRetry':{'max_hit':'{重试次数}'}})

    def test_refresh_mode_also_refreshes_an_exhausted_board(self):
        self.assertEqual(self.nodes['RealmRaidExhaustedConfirmed']['next'],
                         ['[JumpBack]CommonBountyInterrupt','RealmRaidExhaustedRefresh','RealmRaidFinish'])
        option=load_jsonc(ROOT/'assets/tasks/RealmRaid.json')['option']['结界突破战斗失败处理']
        enabled={c['name']:c['pipeline_override']['RealmRaidExhaustedRefresh']['enabled'] for c in option['cases']}
        self.assertEqual(enabled,{'跳过失败目标':False,'刷新后继续':True,'结束本轮':False})

    def test_real_defeat_option_selects_one_route(self):
        self.assertEqual(self.nodes['RealmRaidBattleFailedReturn']['next'],[
            '[JumpBack]CommonBountyInterrupt','RealmRaidFailStop','RealmRaidFailRefresh',
            'RealmRaidFailRefreshUnavailable','RealmRaidScan'])
        gates=('RealmRaidFailStop','RealmRaidFailRefresh','RealmRaidFailRefreshUnavailable','RealmRaidExhaustedRefresh')
        for name in gates:
            self.assertFalse(self.nodes[name]['enabled'])
        option=load_jsonc(ROOT/'assets/tasks/RealmRaid.json')['option']['结界突破战斗失败处理']
        self.assertEqual(option['default_case'],'跳过失败目标')
        for case in option['cases']:
            self.assertEqual(set(case['pipeline_override']),set(gates))
        prompt,button=self.nodes['RealmRaidRefreshConfirm']['recognition']['param']['all_of']
        self.assertTrue(re.search(prompt['recognition']['param']['expected'],'刷新后攻破记录进度将重置，确定吗?'))
        self.assertFalse(re.search(prompt['recognition']['param']['expected'],'确认购买'))
        self.assertEqual(self.safe['SafeInput_RealmRaidRefresh']['max_hit'],3)
        self.assertEqual(self.safe['SafeInput_RealmRaidRefreshConfirm']['max_hit'],3)

    def test_retreat_resets_click_budgets_and_confirms_with_prompt(self):
        for gate,clicks in (('RealmRaidRetreatBegin',['SafeInput_RealmRaidRetreatExit','SafeInput_RealmRaidRetreatConfirm']),
                            ('RealmRaidRetreatRetry',['SafeInput_RealmRaidRetreatAgain','SafeInput_RealmRaidRetreatAgainConfirm'])):
            self.assertEqual(self.nodes[gate]['action']['param']['custom_action_param'],{'nodes':clicks,'strict':True})
            for click in clicks:
                self.assertEqual(self.safe[click]['max_hit'],3)
        for name in ('RealmRaidRetreatConfirm','RealmRaidRetreatAgainConfirm'):
            prompt,button=self.nodes[name]['recognition']['param']['all_of']
            self.assertEqual(self.nodes[name]['recognition']['param']['box_index'],1)
            self.assertEqual(button['recognition']['param']['expected'],'^(确定|确认)$')
            self.assertFalse(re.search(prompt['recognition']['param']['expected'],'确认购买'))
        for i in range(1,7):
            self.assertTrue((ROOT/f'assets/resource/base/image/RealmRaid/BattleExit_{i}.png').is_file())

    def test_zero_ticket_finishes_before_choosing_a_card(self):
        scan=self.nodes['RealmRaidScan']['next']
        self.assertLess(scan.index('RealmRaidNoTicket'),scan.index('RealmRaidChoose'))
        node=self.nodes['RealmRaidNoTicket']
        self.assertEqual(node['recognition']['param']['all_of'],['PageRaid','RealmRaidTicketEmpty'])
        self.assertEqual(node['next'][-1],'RealmRaidFinish')
        pattern=re.compile(self.nodes['RealmRaidTicketEmpty']['recognition']['param']['expected'])
        for text,empty in [('0/30',True),('0 / 30',True),('30/30',False),('10/30',False),('突破券',False)]:
            self.assertEqual(bool(pattern.search(text)),empty,text)
