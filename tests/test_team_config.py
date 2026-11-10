import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from custom.action.team_config import ConfigureTeamRecognition, teammate
from custom.action.target_count import reset_state_for_test
from validate_schema import load_jsonc


class TeamRouteTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def nodes(self, name):
        def read(file):
            return json.loads((self.ROOT / "assets/resource/base/pipeline" / file).read_text(encoding="utf-8"))
        # The leader flow moved out of Mitama.json and is shared by every dungeon.
        return {**read(name), **read("TeamLeader.json")} if name == "Mitama.json" else read(name)

    def test_dungeon_tasks_can_only_lead_a_team(self):
        task = load_jsonc(self.ROOT / "assets/tasks/Mitama.json")
        self.assertNotIn("身份", task["option"])
        cases = {case["name"]: case for case in task["option"]["是否组队"]["cases"]}
        self.assertEqual(cases["Yes"]["option"], ["队友"])
        self.assertEqual(cases["Yes"]["pipeline_override"]["MitamaBattleModeDestination"]["next"][-1], "MitamaTeamEntry")
        awakening = load_jsonc(self.ROOT / "assets/tasks/Awakening.json")
        self.assertNotIn("身份", awakening["option"])
        cases = {case["name"]: case for case in awakening["option"]["觉醒组队"]["cases"]}
        self.assertEqual(cases["Yes"]["option"], ["觉醒队友"])
        self.assertEqual(cases["Yes"]["pipeline_override"]["AwakeningBattleModeDestination"]["next"][-1], "AwakeningTeamEntry")
        self.assertEqual(cases["No"]["pipeline_override"]["AwakeningBattleModeDestination"]["next"][-1], "AwakeningBattleEntry")
        self.assertIn("觉醒组队", awakening["task"][0]["option"])
        self.assertEqual(awakening["option"]["觉醒队友"]["pipeline_override"]["AwakeningTask"]["attach"], {"teammate": "{觉醒队友昵称}"})
        for path in (self.ROOT / "assets/tasks").glob("*.json"):
            if path.name != "TeamMember.json":
                self.assertNotIn("TeamMember", path.read_text(encoding="utf-8"), path.name)

    def test_team_count_routes_are_complete_and_continue_as_leader(self):
        nodes = self.nodes("Mitama.json")
        self.assertEqual(nodes["TeamLeaderComplete"]["next"][-1], "TeamLeaderCountDetermine")
        self.assertEqual(nodes["TeamLeaderCountDetermine"]["action"]["param"]["custom_action_param"],
                         {"decision_node": "TeamLeaderCountDetermine", "continue_node": "TeamLeaderBattleEntry",
                          "finish_node": "TeamLeaderFinish"})
        self.assertEqual(nodes["TeamLeaderFinish"]["next"][-1], "[Anchor]TeamLeaderFinishTarget")
        self.assertEqual(nodes["TeamLeaderBattleEntry"]["next"], ["TeamLeaderFight"])

    def test_each_dungeon_sets_where_the_shared_flow_ends(self):
        mitama, awakening = self.nodes("Mitama.json"), self.nodes("Awakening.json")
        expected = (("MitamaTeamEntry", mitama, "Mitama", "MitamaFinish", "MitamaFinish"),
                    ("AwakeningTeamEntry", awakening, "Awakening", "AwakeningFinish", "AwakeningNoResource"))
        for name, nodes, scope, finish, no_resource in expected:
            entry = nodes[name]
            self.assertEqual(entry["anchor"], {"TeamLeaderFinishTarget": finish, "TeamLeaderNoResourceTarget": no_resource})
            self.assertEqual(entry["next"][-1], "SafeInput_TeamLeaderOpen")
            statistics = entry["action"]["param"]["custom_action_param"]["TeamLeaderDropStatistics"]
            self.assertEqual(statistics["action"]["param"]["custom_action_param"]["scope"], scope)

    def test_shared_leader_flow_has_no_dungeon_specific_routes(self):
        leader = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamLeader.json").read_text(encoding="utf-8"))
        routes = [item for node in leader.values() for item in node.get("next", [])]
        self.assertFalse([item for item in routes if "Awakening" in item or ("Mitama" in item and "Treasure" not in item)])

    def test_awakening_buff_cleanup_works_from_explore(self):
        nodes = self.nodes("Awakening.json")
        self.assertEqual(nodes["AwakeningDisableBuff"]["next"][-1], "[JumpBack]NavigateExplore")
        self.assertEqual(nodes["AwakeningBuffPage"]["recognition"]["param"]["any_of"], ["PageAwakeningStage", "PageExplore"])
        for name in ("AwakeningOpenBuffMenu", "AwakeningBuffOff", "AwakeningBuffOn", "AwakeningCloseBuffAfterDisable"):
            items = nodes[name]["recognition"]["param"]["all_of"]
            self.assertIn("AwakeningBuffPage", items, name)
            self.assertNotIn("PageAwakeningStage", items, name)
        # Enabling still requires the stage page.
        self.assertIn("PageAwakeningStage", nodes["AwakeningCloseBuffAfterEnable"]["recognition"]["param"]["all_of"])

    def test_room_creation_accepts_both_layouts(self):
        nodes = self.nodes("Mitama.json")
        x, y, width, height = nodes["TeamLeaderPrivacyText"]["recognition"]["param"]["roi"]
        for point in ((435, 428), (790, 510)):  # Label beside each layout's checkbox.
            self.assertTrue(x <= point[0] <= x + width and y <= point[1] <= y + height, point)
        x, y, width, height = nodes["TeamLeaderCreateButton"]["recognition"]["param"]["roi"]
        for point in ((573, 516), (877, 591)):  # Each layout's confirm button.
            self.assertTrue(x <= point[0] <= x + width and y <= point[1] <= y + height, point)
        self.assertEqual(nodes["TeamLeaderPrivacySelected"]["recognition"]["param"]["all_of"][2]["recognition"]["param"]["roi"],
                         "TeamLeaderPrivacyLabel")

    def test_member_nodes_left_the_dungeon_pipeline(self):
        names = set(self.nodes("Mitama.json")) | set(self.nodes("InputSafety.json"))
        for removed in ("MitamaTeamMember", "MitamaMemberFight", "MitamaMemberBattleEntry", "MitamaRoomMemberReady",
                        "MitamaAcceptNamedInvitation", "SafeInput_MitamaAcceptNamedInvitation", "SafeInput_MitamaRoomMemberReady"):
            self.assertNotIn(removed, names)

    def test_member_task_is_registered_and_generic(self):
        interface = load_jsonc(self.ROOT / "assets/interface.json")
        self.assertIn("tasks/TeamMember.json", interface["import"])
        task = load_jsonc(self.ROOT / "assets/tasks/TeamMember.json")
        self.assertEqual(task["task"][0]["entry"], "TeamMemberTask")
        self.assertEqual(task["task"][0]["option"], ["队长昵称", "组队队员次数"])
        attach = task["option"]["队长昵称"]["pipeline_override"]["TeamMemberTask"]["attach"]
        self.assertEqual(attach, {"teammate": "{完整队长昵称}"})
        for language in ("zh", "en"):
            text = json.loads((self.ROOT / f"assets/language/interface_{language}.json").read_text(encoding="utf-8"))
            self.assertIn("team_member_task", text)
            self.assertIn("team_member_task_doc", text)

    def test_member_task_does_not_navigate_or_select_a_stage(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        routes = [item for node in nodes.values() for item in node.get("next", [])]
        self.assertFalse([item for item in routes if "Navigate" in item or "MitamaSelect" in item])
        self.assertEqual(nodes["TeamMemberConfigure"]["action"]["param"]["custom_action_param"], {"nodes": ["TeamMemberLeaderName"]})

    def test_accept_icons_stay_in_their_own_columns(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        x, _, width, _ = nodes["TeamMemberAcceptIcon"]["recognition"]["param"]["roi"]
        self.assertGreaterEqual(x, 12 + 64)
        self.assertLessEqual(x + width, 205)
        default_x = nodes["TeamMemberAcceptDefaultIcon"]["recognition"]["param"]["roi"][0]
        self.assertGreaterEqual(default_x, x + width)

    def test_member_prefers_default_accept_over_plain_accept(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        routes = nodes["TeamMemberLoop"]["next"]
        self.assertLess(routes.index("SafeInput_TeamMemberAcceptDefault"), routes.index("SafeInput_TeamMemberAccept"))
        self.assertEqual(nodes["TeamMemberAcceptDefault"]["recognition"]["param"]["all_of"],
                         ["TeamMemberLeaderName", "TeamMemberAcceptDefaultIcon"])

    def test_defeat_reports_and_never_reaches_the_next_battle(self):
        nodes = self.nodes("Mitama.json")
        for name in ("TeamLeaderBattleFailure", "MitamaBattleFailure"):
            self.assertIn("战斗失败", nodes[name]["focus"]["Node.Recognition.Succeeded"])
        self.assertEqual(nodes["TeamLeaderBattleFailedReturn"]["next"], ["NativeFlowFailed"])

    def test_start_button_accepts_what_ocr_reads_from_the_calligraphy(self):
        expected = self.nodes("Mitama.json")["TeamLeaderStartButton"]["recognition"]["param"]["expected"]
        for text in ("挑", "挑战", "挑铭", "开始"):
            self.assertRegex(text, expected)
        for text in ("x8", "快", "五享受回归特权加成", "挑战次数"):
            self.assertNotRegex(text, expected)

    def test_settlement_clicks_never_go_through_the_public_node(self):
        # Recognizing twice costs about 1.5 seconds per click and let the page change in between.
        mitama, awakening, safety = self.nodes("Mitama.json"), self.nodes("Awakening.json"), self.nodes("InputSafety.json")
        for nodes, loop, public, stats in ((mitama, "TeamLeaderBattle", "TeamLeaderBattleResult", "TeamLeaderDropStatistics"),
                                           (mitama, "MitamaBattle", "MitamaBattleResult", "MitamaSoloDropStatistics"),
                                           (awakening, "AwakeningBattle", "AwakeningBattleResult", "AwakeningDropStatistics")):
            click = "SafeInput_" + public
            for routes in (nodes[loop]["next"], safety[click]["next"]):
                self.assertNotIn(public, routes)
                self.assertLess(routes.index(stats), routes.index(click), public)
            self.assertEqual(safety[click]["post_delay"], 600)

    def test_team_loop_control_nodes_do_not_wait(self):
        nodes = self.nodes("Mitama.json")
        for name in ("TeamLeaderBattleAfterResult", "TeamLeaderComplete", "TeamLeaderCountDetermine", "TeamLeaderBattleEntry",
                     "TeamLeaderFight", "TeamLeaderWaitBattleStart", "TeamLeaderBattle"):
            self.assertEqual((nodes[name]["pre_delay"], nodes[name]["post_delay"]), (0, 0), name)

    def test_mitama_entry_navigates_once_and_never_rescans(self):
        nodes = self.nodes("Mitama.json")
        self.assertEqual(nodes["MitamaNavigate"]["next"],
                         ["[JumpBack]CommonBountyInterrupt", "[JumpBack]MitamaNavigateOnce", "MitamaNavigateReady"])
        self.assertEqual((nodes["MitamaNavigateOnce"]["max_hit"], nodes["MitamaNavigateOnce"]["next"]), (1, ["NavigateMitamaStage"]))

    def test_preparation_control_nodes_do_not_wait(self):
        nodes = self.nodes("Mitama.json")
        for name in ("MitamaNavigate", "MitamaNavigateOnce", "MitamaNavigateReady", "MitamaLockTeam", "MitamaEnableBuff",
                     "MitamaCheckBuffStatusOpen", "MitamaCheckBuffStatusClose", "MitamaDisableBuff", "MitamaDisableBuffReady"):
            self.assertEqual((nodes[name]["pre_delay"], nodes[name]["post_delay"]), (0, 0), name)

    def test_default_invite_ticks_only_a_confirmed_unchecked_box(self):
        nodes = self.nodes("Mitama.json")
        tick = nodes["TeamLeaderReinviteTick"]["recognition"]["param"]
        self.assertEqual(tick["all_of"], ["TeamLeaderReinvitePrompt", "TeamLeaderReinviteLabel", "TeamLeaderReinviteUnchecked"])
        self.assertEqual(tick["box_index"], 2)
        unchecked = nodes["TeamLeaderReinviteUnchecked"]["recognition"]["param"]
        # Between OAS's checked (288) and the real unchecked box (530) dark pixel counts.
        self.assertTrue(288 < unchecked["count"] < 530)
        x, y, width, height = unchecked["roi"]
        self.assertLessEqual(x, 548)
        self.assertLessEqual(y, 348)
        self.assertGreaterEqual(x + width, 573)
        self.assertLessEqual(x + width, 583)
        self.assertGreaterEqual(y + height, 373)
        for name in ("TeamLeaderComplete", "TeamLeaderFight"):
            routes = nodes[name]["next"]
            self.assertLess(routes.index("[JumpBack]SafeInput_TeamLeaderReinviteTick"), routes.index("[JumpBack]SafeInput_TeamLeaderReinvite"))

    def test_buff_cleanup_never_requires_the_stage_page(self):
        # The simulation stubs composite checks, so walk the real references here.
        nodes = self.nodes("Mitama.json")

        def references(name, seen):
            if name == "MitamaBuffPage" or name in seen or name not in nodes:
                return seen
            seen.add(name)
            param = nodes[name].get("recognition", {}).get("param", {})
            for item in param.get("all_of", []) + param.get("any_of", []):
                if isinstance(item, str):
                    if item == "PageMitamaStage":
                        seen.add("!" + name)
                    else:
                        references(item, seen)
            return seen

        for start in ("MitamaDisableBuffClick", "MitamaCheckBuffStatusClose", "MitamaOpenBuffMenu",
                      "MitamaFinishCloseBuffPanel", "MitamaDisableBuffReady"):
            self.assertEqual({item for item in references(start, set()) if item.startswith("!")}, set(), start)

    def test_buff_cleanup_stays_out_of_the_mitama_pages(self):
        nodes = self.nodes("Mitama.json")
        self.assertEqual(nodes["MitamaDisableBuff"]["next"][-1], "[JumpBack]NavigateExplore")
        self.assertEqual(nodes["MitamaBuffPage"]["recognition"]["param"]["any_of"], ["PageMitamaStage", "PageExplore"])
        for name in ("MitamaOpenBuffMenu", "MitamaDisableBuffClick", "MitamaCheckBuffStatusClose", "MitamaFinishCloseBuffPanel", "MitamaDisableBuffReady"):
            items = nodes[name]["recognition"]["param"]["all_of"]
            self.assertIn("MitamaBuffPage", items, name)
            self.assertNotIn("PageMitamaStage", items, name)
        # Enabling still happens on the stage page, where the battle starts.
        self.assertIn("PageMitamaStage", nodes["MitamaEnableBuffClick"]["recognition"]["param"]["all_of"])
        support = self.nodes("BattleSupport.json")
        self.assertEqual(support["MitamaAbortRecover"]["next"][-1], "[JumpBack]NavigateExplore")

    def test_reinvite_is_checked_before_the_generic_cancel(self):
        nodes = self.nodes("Mitama.json")
        routes = nodes["TeamLeaderComplete"]["next"]
        self.assertLess(routes.index("[JumpBack]SafeInput_TeamLeaderReinvite"), routes.index("[JumpBack]SafeInput_CommonConfirm"))
        routes = nodes["TeamLeaderFight"]["next"]
        self.assertLess(routes.index("[JumpBack]SafeInput_TeamLeaderReinvite"), routes.index("SafeInput_TeamLeaderStart"))
        self.assertEqual(nodes["TeamLeaderReinvite"]["recognition"]["param"]["box_index"], 1)


class TeamConfigTest(unittest.TestCase):
    @patch("custom.action.team_config.get_runtime_params", return_value={"teammate": "  队友.+[1]  "})
    def test_exact_nickname_override_has_no_screen_input(self, runtime):
        context = Mock()
        context.override_pipeline.return_value = True
        arg = SimpleNamespace(custom_action_param=json.dumps({"nodes": ["LeaderName", "RoomName"]}))
        result = ConfigureTeamRecognition().run(context, arg)
        self.assertTrue(result.success)
        overrides = context.override_pipeline.call_args.args[0]
        self.assertEqual(set(overrides), {"LeaderName", "RoomName"})
        for node in overrides.values():
            self.assertEqual(node["recognition"]["param"]["expected"], r"^队友\.\+\[1\]$")
        context.run_recognition.assert_not_called()
        context.tasker.controller.post_touch_down.assert_not_called()

    @patch("custom.action.team_config.get_runtime_params", return_value={"teammate": " "})
    def test_blank_nickname_rejected_without_override(self, runtime):
        context = Mock()
        arg = SimpleNamespace(custom_action_param=json.dumps({"nodes": ["LeaderName"]}))
        self.assertFalse(ConfigureTeamRecognition().run(context, arg).success)
        context.override_pipeline.assert_not_called()

    @patch("custom.action.team_config.get_runtime_params", return_value={"teammate": "队友"})
    def test_missing_node_list_is_rejected(self, runtime):
        context = Mock()
        self.assertFalse(ConfigureTeamRecognition().run(context, SimpleNamespace(custom_action_param="{}")).success)
        context.override_pipeline.assert_not_called()

    def test_missing_nickname_rejected(self):
        reset_state_for_test()
        with self.assertRaises(ValueError):
            teammate()
