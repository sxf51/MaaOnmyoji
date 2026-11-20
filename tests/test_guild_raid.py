"""Guild realm raid reuses personal raid templates and battle checks on its own scrolling list flow."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))


class GuildRaidTest(unittest.TestCase):
    def setUp(self):
        self.nodes = load_jsonc(ROOT / 'assets/resource/base/pipeline/GuildRaid.json')
        self.raid = load_jsonc(ROOT / 'assets/resource/base/pipeline/RealmRaid.json')
        self.task = load_jsonc(ROOT / 'assets/tasks/GuildRaid.json')

    def test_templates_exist_and_card_reuses_personal_mask(self):
        card = self.nodes['GuildRaidCard']['recognition']['param']
        personal = self.raid['RealmRaidCard']['recognition']['param']
        for key in ('template', 'green_mask', 'method', 'threshold'):
            self.assertEqual(card[key], personal[key], key)
        for name in ('GuildRaidCard', 'GuildRaidTab', 'GuildRaidBrokenAny', 'GuildRaidTeamLocked',
                     'GuildRaidTeamUnlocked', 'GuildRaidFailedCornerScan'):
            template = self.nodes[name]['recognition']['param']['template']
            self.assertTrue((ROOT / 'assets/resource/base/image' / template).is_file(), name)

    def test_task_reaches_guild_tab_through_personal_raid(self):
        routes = self.nodes['GuildRaidTask']['next']
        ready, open_tab, navigate = (routes.index(n) for n in
                                     ('GuildRaidReady', '[JumpBack]GuildRaidOpenTab', '[JumpBack]NavigateRaid'))
        self.assertLess(ready, open_tab)
        self.assertLess(open_tab, navigate)
        self.assertIn('PageRaid', self.nodes['GuildRaidOpenTab']['recognition']['param']['all_of'])

    def test_click_and_corner_are_pinned_to_chosen_card(self):
        self.assertEqual(self.nodes['GuildRaidUnmarkedCorner']['recognition']['param']['roi'], 'GuildRaidChoose')
        self.assertEqual(self.nodes['GuildRaidUnmarkedCorner']['recognition']['param']['roi_offset'],
                         self.raid['RealmRaidUnmarkedCorner']['recognition']['param']['roi_offset'])
        self.assertEqual(self.nodes['GuildRaidFailedCorner']['recognition']['param']['roi'], 'GuildRaidChoose')
        click = self.nodes['GuildRaidSelect']['action']['param']
        self.assertEqual(click['target'], 'GuildRaidChoose')
        self.assertIn('GuildRaidChosenAvailable', self.nodes['GuildRaidSelect']['recognition']['param']['all_of'])
        self.assertEqual(self.nodes['GuildRaidSelect']['max_hit'], 3)

    def test_ticket_zero_only_stops_before_21_beijing_time(self):
        reco = self.nodes['GuildRaidNoTicket']['recognition']['param']['all_of']
        self.assertIn('GuildRaidTicketEmpty', reco)
        self.assertIn('GuildRaidTicketsCounted', reco)
        window = self.nodes['GuildRaidTicketsCounted']['recognition']['param']
        self.assertEqual(window['custom_recognition'], 'BeijingTimeWindow')
        self.assertEqual(window['custom_recognition_param'], {'start_hour': 5, 'end_hour': 21})

    def test_beijing_window(self):
        from custom.reco.beijing_time import in_window
        utc = lambda h, m=0: datetime(2026, 11, 21, h, m, tzinfo=timezone.utc)
        self.assertTrue(in_window(utc(12, 59), 5, 21))    # 20:59 Beijing
        self.assertFalse(in_window(utc(13), 5, 21))       # 21:00 Beijing
        self.assertFalse(in_window(utc(20, 30), 5, 21))   # 04:30 Beijing
        self.assertTrue(in_window(utc(21), 5, 21))        # 05:00 Beijing
        self.assertTrue(in_window(utc(16), 21, 5))        # wrapping window, 00:00 Beijing
        self.assertFalse(in_window(utc(4), 21, 5))        # 12:00 Beijing

    def test_scan_order_prefers_cards_then_done_then_scroll(self):
        scan = self.nodes['GuildRaidScan']['next']
        order = [scan.index(n) for n in ('[JumpBack]GuildRaidDismissTarget', 'GuildRaidNoTicket', 'GuildRaidChoose',
                                         'GuildRaidAllDone', 'GuildRaidScrollRemember', 'GuildRaidScrollSpent')]
        self.assertEqual(order, sorted(order))
        self.assertIn('GuildRaidAllDoneConfirmed', self.nodes['GuildRaidAllDone']['next'])
        self.assertIn('GuildRaidListEnd', self.nodes['GuildRaidAfterScroll']['next'])

    def test_every_swipe_is_bezier_between_regions(self):
        def kind(node):
            action = node.get('action')
            if not isinstance(action, dict):
                return action
            return action.get('param', {}).get('custom_action') if action.get('type') == 'Custom' else action.get('type')
        swipes = [name for name, node in self.nodes.items() if 'Swipe' in str(kind(node))]
        self.assertEqual(set(swipes), {'GuildRaidScroll', 'GuildRaidTopSwipe'})
        for name in swipes:
            param = self.nodes[name]['action']['param']
            self.assertEqual(param['custom_action'], 'BezierSwipe', name)
            for end in ('begin', 'end'):
                region = param['custom_action_param'][end]
                self.assertEqual(len(region), 4, name)
                self.assertTrue(region[2] > 1 and region[3] > 1, name)
                # Stay inside the two-column list.
                self.assertTrue(430 <= region[0] and region[0] + region[2] <= 1080, name)
                self.assertTrue(130 <= region[1] and region[1] + region[3] <= 650, name)
        down = self.nodes['GuildRaidScroll']['action']['param']['custom_action_param']
        self.assertGreater(down['begin'][1], down['end'][1])
        up = self.nodes['GuildRaidTopSwipe']['action']['param']['custom_action_param']
        self.assertLess(up['begin'][1], up['end'][1])
        self.assertEqual(self.nodes['GuildRaidScrollRemember']['action']['param']['custom_action'], 'RememberFrame')

    def test_win_counts_only_back_on_guild_page(self):
        self.assertIn('GuildRaidBattleAfterResult', self.nodes['GuildRaidBattleResult']['next'])
        after = self.nodes['GuildRaidBattleAfterResult']
        self.assertEqual(after['recognition']['param']['all_of'], ['PageGuildRaid'])
        self.assertEqual(after['action']['param']['custom_action'], 'TargetCountProgress')
        self.assertNotIn('GuildRaidBattleAfterResult', self.nodes['GuildRaidBattleSettle']['next'])

    def test_rejected_attack_reloads_list_with_a_budget(self):
        wait = self.nodes['GuildRaidWaitBattleStart']['next']
        self.assertLess(wait.index('GuildRaidAttack'), wait.index('GuildRaidAttackRejected'))
        rejected = self.nodes['GuildRaidAttackRejected']
        self.assertGreaterEqual(rejected['post_delay'], 1000)
        self.assertEqual(rejected['next'][0], 'GuildRaidBattleStarted')
        confirm = self.nodes['GuildRaidAttackRejectedConfirm']
        self.assertEqual(confirm['max_hit'], 3)
        for name in ('GuildRaidAttackRejected', 'GuildRaidRejectTip'):
            nxt = self.nodes[name]['next']
            self.assertLess(nxt.index('GuildRaidAttackRejectedConfirm'), nxt.index('GuildRaidRejectSpent'))
        # Tab switching does not reload the list; the X closes Realm Raid and NavigateRaid reopens it.
        self.assertIn('NavClose', confirm['recognition']['param']['all_of'])
        reopen = self.nodes['GuildRaidRefreshReopen']['next']
        self.assertEqual(reopen[:3], ['GuildRaidRefreshed', '[JumpBack]GuildRaidOpenTab', '[JumpBack]NavigateRaid'])

    def test_choice_resets_only_per_card_budgets(self):
        nodes = self.nodes['GuildRaidChoose']['action']['param']['custom_action_param']['nodes']
        for budget in ('GuildRaidBattleFailedReturn', 'GuildRaidScrollRemember', 'GuildRaidTopRemember',
                       'GuildRaidAttackRejectedConfirm', 'GuildRaidNotOpen'):
            self.assertNotIn(budget, nodes)
        for budget in ('GuildRaidSelect', 'GuildRaidBattleMark', 'GuildRaidFailRetry'):
            self.assertIn(budget, nodes)

    def test_fail_options(self):
        self.assertEqual(self.nodes['GuildRaidChoose']['recognition']['param']['any_of'], ['GuildRaidChooseFresh'])
        self.assertEqual(self.nodes['GuildRaidFailRetry']['max_hit'], 0)
        self.assertFalse(self.nodes['GuildRaidFailStop']['enabled'])
        option = self.task['option']['寮突破战斗失败处理']
        cases = {c['name']: c['pipeline_override'] for c in option['cases']}
        self.assertEqual(option['default_case'], '跳过失败结界')
        self.assertEqual(cases['也挑战失败结界']['GuildRaidChoose']['recognition']['param']['any_of'],
                         ['GuildRaidChooseFresh', 'GuildRaidChooseFailed'])
        self.assertTrue(cases['结束本轮']['GuildRaidFailStop']['enabled'])
        failed = self.nodes['GuildRaidBattleFailedResult']['next']
        self.assertLess(failed.index('GuildRaidFailStop'), failed.index('GuildRaidBattleFailedReturn'))

    def test_conquered_guild_stops_before_any_attack(self):
        stamp = self.nodes['GuildRaidConqueredStamp']['recognition']['param']
        self.assertTrue((ROOT / 'assets/resource/base/image' / stamp['template']).is_file())
        self.assertEqual(self.nodes['GuildRaidConquered']['next'][0], 'GuildRaidFinish')
        self.assertEqual(self.nodes['GuildRaidReady']['next'][0], 'GuildRaidConquered')
        self.assertEqual(self.nodes['GuildRaidWaitTarget']['next'][0], 'GuildRaidConquered')
        scan = self.nodes['GuildRaidScan']['next']
        self.assertLess(scan.index('GuildRaidConquered'), scan.index('GuildRaidChoose'))
        # The emblem stamp means 90% of cards fell; remaining cards stay attackable, so stopping is opt-in.
        self.assertFalse(self.nodes['GuildRaidConquered']['enabled'])
        option = self.task['option']['寮突破已攻破后']
        self.assertIn('寮突破已攻破后', self.task['task'][0]['option'])
        self.assertEqual(option['default_case'], '继续挑战')
        cases = {c['name']: c['pipeline_override']['GuildRaidConquered']['enabled'] for c in option['cases']}
        self.assertEqual(cases, {'继续挑战': False, '结束本轮': True})

    def test_stale_toast_closes_popup_before_reloading(self):
        wait = self.nodes['GuildRaidWaitBattleStart']['next']
        self.assertLess(wait.index('GuildRaidAttackStale'), wait.index('GuildRaidAttack'))
        self.assertEqual(self.nodes['GuildRaidStaleToast']['recognition']['param']['expected'], '已被攻破')
        for name in ('GuildRaidAttackStale', 'GuildRaidAttackRejected', 'GuildRaidRejectTip', 'GuildRaidRejectDismiss'):
            nxt = self.nodes[name]['next']
            # 该结界已被攻破 is about this one card, not the guild-wide stamp.
            self.assertNotIn('GuildRaidConquered', nxt, name)
            if name != 'GuildRaidRejectDismiss':
                self.assertLess(nxt.index('GuildRaidRejectDismiss'), nxt.index('GuildRaidAttackRejectedConfirm'), name)

    def test_task_options(self):
        self.assertEqual(self.task['task'][0]['entry'], 'GuildRaidTask')
        for name in self.task['task'][0]['option']:
            self.assertIn(name, self.task['option'])
        preset = self.task['option']['寮突破更换队伍预设']
        self.assertFalse(self.nodes['GuildRaidPreset']['enabled'])
        self.assertEqual(preset['cases'][1]['option'], ['结界突破预设分组与队伍'])
        lock = {c['name'] for c in self.task['option']['寮突破锁定阵容']['cases']}
        self.assertEqual(lock, {'保持现状', '锁定', '解锁'})
        self.assertIn('tasks/GuildRaid.json', (ROOT / 'assets/interface.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
