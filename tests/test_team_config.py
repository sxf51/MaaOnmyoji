import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from validate_schema import load_jsonc



def _all_nodes():
    """Clicks now live on the recognized nodes; read every production pipeline."""
    root = Path(__file__).resolve().parents[1] / "assets/resource/base/pipeline"
    nodes = {}
    for path in root.glob("*.json"):
        if path.name != "Example.json":
            nodes.update(json.loads(path.read_text(encoding="utf-8")))
    return nodes

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
        self.assertEqual(cases["Yes"]["pipeline_override"]["MitamaBattleModeDestination"]["next"][-3], "MitamaTeamEntry")
        awakening = load_jsonc(self.ROOT / "assets/tasks/Awakening.json")
        self.assertNotIn("身份", awakening["option"])
        cases = {case["name"]: case for case in awakening["option"]["觉醒组队"]["cases"]}
        self.assertEqual(cases["Yes"]["option"], ["觉醒队友"])
        self.assertEqual(cases["Yes"]["pipeline_override"]["AwakeningBattleModeDestination"]["next"][-3], "AwakeningTeamEntry")
        self.assertEqual(cases["No"]["pipeline_override"]["AwakeningBattleModeDestination"]["next"][-3], "AwakeningBattleEntry")
        self.assertIn("觉醒组队", awakening["task"][0]["option"])
        self.assertEqual(awakening["option"]["觉醒队友"]["pipeline_override"]["TeamLeaderTeammateName"]["recognition"]["param"]["expected"],
                         "^{觉醒队友昵称}$")
        for path in (self.ROOT / "assets/tasks").glob("*.json"):
            if path.name != "TeamMember.json":
                self.assertNotIn("TeamMember", path.read_text(encoding="utf-8"), path.name)

    def test_team_count_routes_are_complete_and_continue_as_leader(self):
        nodes = self.nodes("Mitama.json")
        self.assertEqual(nodes["TeamLeaderComplete"]["next"][-3], "TeamLeaderCountDetermine")
        self.assertEqual(nodes["TeamLeaderCountDetermine"]["action"]["param"]["custom_action_param"],
                         {"decision_node": "TeamLeaderCountDetermine", "continue_node": "TeamLeaderBattleEntry",
                          "finish_node": "TeamLeaderFinish"})
        self.assertEqual(nodes["TeamLeaderFinish"]["next"][-3], "[Anchor]TeamLeaderFinishTarget")
        self.assertEqual(nodes["TeamLeaderBattleEntry"]["next"], ["TeamLeaderFight"])

    def test_each_dungeon_sets_where_the_shared_flow_ends(self):
        mitama, awakening = self.nodes("Mitama.json"), self.nodes("Awakening.json")
        expected = (("MitamaTeamEntry", mitama, "Mitama", "MitamaFinish", "MitamaFinish"),
                    ("AwakeningTeamEntry", awakening, "Awakening", "AwakeningFinish", "AwakeningNoResource"))
        for name, nodes, scope, finish, no_resource in expected:
            entry = nodes[name]
            self.assertEqual(entry["anchor"], {"TeamLeaderFinishTarget": finish, "TeamLeaderNoResourceTarget": no_resource})
            self.assertEqual(entry["next"][-3], "TeamLeaderOpen")
            statistics = entry["action"]["param"]["custom_action_param"]["TeamLeaderDropStatistics"]
            self.assertEqual(statistics["action"]["param"]["custom_action_param"]["scope"], scope)

    def test_shared_leader_flow_has_no_dungeon_specific_routes(self):
        leader = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamLeader.json").read_text(encoding="utf-8"))
        routes = [item for node in leader.values() for item in node.get("next", [])]
        self.assertFalse([item for item in routes if "Awakening" in item or ("Mitama" in item and "Treasure" not in item)])

    def test_awakening_buff_cleanup_works_from_explore(self):
        nodes = self.nodes("Awakening.json")
        self.assertEqual(nodes["AwakeningDisableBuff"]["next"],
                         ["AwakeningDisableBuffReady", "[JumpBack]NavigateExplore", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertEqual(nodes["AwakeningDisableBuffReady"]["recognition"]["param"]["all_of"], ["AwakeningBuffPage"])
        self.assertEqual(nodes["AwakeningBuffPage"]["recognition"]["param"]["any_of"], ["PageAwakeningStage", "PageExplore"])
        for name in ("AwakeningOpenBuffMenu", "AwakeningBuffOff", "AwakeningBuffOn", "AwakeningCloseBuffAfterDisable",
                     "AwakeningCloseBuffAfterEnable"):
            items = nodes[name]["recognition"]["param"]["all_of"]
            self.assertIn("AwakeningBuffAnyPage", items, name)
            self.assertNotIn("PageAwakeningStage", items, name)

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

    def test_task_options_offer_an_optional_second_teammate_and_enable_resume(self):
        for file, option, switch, resume in (("Mitama.json", "队友", "是否组队", "MitamaTeamResume"),
                                             ("Awakening.json", "觉醒队友", "觉醒组队", "AwakeningTeamResume")):
            task = load_jsonc(self.ROOT / "assets/tasks" / file)
            inputs = task["option"][option]["inputs"]
            self.assertEqual(len(inputs), 2)
            self.assertRegex("", inputs[1]["verify"])
            self.assertNotRegex("", inputs[0]["verify"])
            # The nickname is used as typed, so edge spaces would break the exact invite match.
            for verify in (inputs[0]["verify"], inputs[1]["verify"]):
                self.assertRegex("甜奶酱 兔心", verify)
                self.assertNotRegex(" 甜奶酱", verify)
                self.assertNotRegex("甜奶酱 ", verify)
            first, second = "{" + inputs[0]["name"] + "}", "{" + inputs[1]["name"] + "}"
            expected = {node: value["recognition"]["param"]["expected"]
                        for node, value in task["option"][option]["pipeline_override"].items()}
            self.assertEqual(expected, {"TeamLeaderTeammateName": "^" + first + "$", "TeamLeaderTeammate2Name": "^" + second + "$",
                                        "TeamLeaderRoomTeammate": first, "TeamLeaderRoomTeammate2": second})
            cases = {case["name"]: case for case in task["option"][switch]["cases"]}
            self.assertEqual(cases["Yes"]["pipeline_override"][resume], {"enabled": True})
            self.assertNotIn(resume, cases["No"]["pipeline_override"])

    def test_resume_requires_room_target_and_every_teammate(self):
        mitama, awakening = self.nodes("Mitama.json"), self.nodes("Awakening.json")
        self.assertEqual(mitama["MitamaTeamResume"]["recognition"]["param"]["all_of"],
                         ["PageTeamRoom", "MitamaRoomTargetDungeon", "MitamaRoomTargetLayer", "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2"])
        self.assertEqual(awakening["AwakeningTeamResume"]["recognition"]["param"]["all_of"],
                         ["PageTeamRoom", "AwakeningRoomTargetDungeon", "AwakeningRoomType", "AwakeningRoomLayer",
                          "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2"])
        for nodes, prefix in ((mitama, "Mitama"), (awakening, "Awakening")):
            resume = nodes[prefix + "TeamResume"]
            self.assertFalse(resume["enabled"])
            self.assertEqual(resume["max_hit"], 1)
            self.assertNotIn(prefix + "TeamResumeConfigure", nodes)
            self.assertEqual(nodes[prefix + "Navigate"]["next"][0], prefix + "TeamResume")
            self.assertEqual(nodes[prefix + "TeamResume"]["anchor"], nodes[prefix + "TeamEntry"]["anchor"])
        self.assertEqual(mitama["TeamLeaderStart"]["recognition"]["param"]["all_of"],
                         ["PageTeamRoom", "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2", "TeamLeaderStartButton"])

    def test_member_nodes_left_the_dungeon_pipeline(self):
        names = set(self.nodes("Mitama.json")) | set(_all_nodes())
        for removed in ("MitamaTeamMember", "MitamaMemberFight", "MitamaMemberBattleEntry", "MitamaRoomMemberReady",
                        "MitamaAcceptNamedInvitation", "MitamaAcceptNamedInvitation", "MitamaRoomMemberReady"):
            self.assertNotIn(removed, names)

    def test_member_task_is_registered_and_generic(self):
        interface = load_jsonc(self.ROOT / "assets/interface.json")
        self.assertIn("tasks/TeamMember.json", interface["import"])
        task = load_jsonc(self.ROOT / "assets/tasks/TeamMember.json")
        self.assertEqual(task["task"][0]["entry"], "TeamMemberTask")
        self.assertEqual(task["task"][0]["option"], ["队长昵称", "组队队员次数"])
        self.assertEqual(task["option"]["队长昵称"]["pipeline_override"],
                         {"TeamMemberLeaderName": {"recognition": {"param": {"expected": "^\\d*\\s*{完整队长昵称}$"}}},
                          "TeamMemberInviteTarget": {"recognition": {"param": {"expected": "{邀请目标副本}"}}}})
        for language in ("zh", "en"):
            text = json.loads((self.ROOT / f"assets/language/interface_{language}.json").read_text(encoding="utf-8"))
            self.assertIn("team_member_task", text)
            self.assertIn("team_member_task_doc", text)

    def test_member_task_does_not_navigate_or_select_a_stage(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        routes = [item for node in nodes.values() for item in node.get("next", [])]
        self.assertFalse([item for item in routes if "Navigate" in item or "MitamaSelect" in item])
        self.assertNotIn("TeamMemberConfigure", nodes)
        self.assertEqual(nodes["TeamMemberTask"]["next"], ["TeamMemberBattleEntry"])

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
        self.assertLess(routes.index("TeamMemberAcceptDefault"), routes.index("TeamMemberAccept"))
        self.assertEqual(nodes["TeamMemberAcceptDefault"]["recognition"]["param"]["all_of"],
                         ["TeamMemberLeaderName", "TeamMemberInviteTarget", "TeamMemberAcceptDefaultIcon"])

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

    def test_settlement_click_runs_after_drop_statistics(self):
        # The result node clicks on the same recognition; statistics must read the page first.
        mitama, awakening, safety = self.nodes("Mitama.json"), self.nodes("Awakening.json"), _all_nodes()
        for nodes, loop, public, stats in ((mitama, "TeamLeaderBattle", "TeamLeaderBattleResult", "TeamLeaderDropStatistics"),
                                           (mitama, "MitamaBattle", "MitamaBattleResult", "MitamaSoloDropStatistics"),
                                           (awakening, "AwakeningBattle", "AwakeningBattleResult", "AwakeningDropStatistics")):
            click = public
            for routes in (nodes[loop]["next"], safety[click]["next"]):
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
                         ["MitamaTeamResume", "[JumpBack]MitamaNavigateOnce", "MitamaNavigateReady", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
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
            self.assertLess(routes.index("[JumpBack]TeamLeaderReinviteTick"), routes.index("[JumpBack]TeamLeaderReinvite"))

    def test_buff_cleanup_never_requires_the_stage_page(self):
        # The simulation stubs composite checks, so walk the real references here.
        nodes = self.nodes("Mitama.json")

        def references(name, seen):
            if name in ("MitamaBuffPage", "MitamaBuffAnyPage") or name in seen or name not in nodes:
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
        self.assertEqual(nodes["MitamaDisableBuff"]["next"][-3], "[JumpBack]NavigateExplore")
        self.assertEqual(nodes["MitamaBuffPage"]["recognition"]["param"]["any_of"], ["PageMitamaStage", "PageExplore"])
        # Finishing decides where to close by MitamaBuffPage, so a room is left before closing.
        self.assertEqual(nodes["MitamaDisableBuffReady"]["recognition"]["param"]["all_of"], ["MitamaBuffPage"])
        self.assertEqual(nodes["MitamaBuffAnyPage"]["recognition"]["param"]["any_of"], ["PageMitamaStage", "PageExplore", "PageTeamRoom"])
        for name in ("MitamaOpenBuffMenu", "MitamaDisableBuffClick", "MitamaCheckBuffStatusClose", "MitamaFinishCloseBuffPanel"):
            items = nodes[name]["recognition"]["param"]["all_of"]
            self.assertIn("MitamaBuffAnyPage", items, name)
            self.assertNotIn("PageMitamaStage", items, name)
        # Enabling happens on the stage page, or in the room when an existing team is resumed.
        self.assertIn("MitamaBuffAnyPage", nodes["MitamaEnableBuffClick"]["recognition"]["param"]["all_of"])
        support = self.nodes("BattleSupport.json")
        self.assertEqual(support["MitamaAbortRecover"]["next"][-3], "[JumpBack]NavigateExplore")

    def test_reinvite_is_checked_before_the_generic_cancel(self):
        nodes = self.nodes("Mitama.json")
        routes = nodes["TeamLeaderComplete"]["next"]
        self.assertLess(routes.index("[JumpBack]TeamLeaderReinvite"), routes.index("[JumpBack]CommonConfirm"))
        routes = nodes["TeamLeaderFight"]["next"]
        self.assertLess(routes.index("[JumpBack]TeamLeaderReinvite"), routes.index("TeamLeaderStart"))
        self.assertEqual(nodes["TeamLeaderReinvite"]["recognition"]["param"]["box_index"], 1)


def substitute(text, values):
    """Same as MaaPiCli: input values are pasted into the override text as typed."""
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


class NativeTeamConfigTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def leader(self):
        return json.loads((self.ROOT / "assets/resource/base/pipeline/TeamLeader.json").read_text(encoding="utf-8"))

    def overrides(self, first, second=""):
        option = load_jsonc(self.ROOT / "assets/tasks/Mitama.json")["option"]["队友"]
        text = substitute(json.dumps(option["pipeline_override"], ensure_ascii=False), {"队友昵称": first, "第二个队友昵称": second})
        return {node: value["recognition"]["param"]["expected"] for node, value in json.loads(text).items()}

    def test_no_python_configures_team_recognition(self):
        self.assertFalse((self.ROOT / "agent/custom/action/team_config.py").exists())
        for path in list((self.ROOT / "assets").rglob("*.json")) + [self.ROOT / "agent/main.py", self.ROOT / "agent/custom/route_policy.py"]:
            text = path.read_text(encoding="utf-8")
            for name in ("ConfigureTeamRecognition", "TeamInviteNext", "team_config"):
                self.assertNotIn(name, text, path.name)

    def test_invite_picks_exactly_and_room_matches_by_containment(self):
        expected = self.overrides("甜奶酱", "兔心")
        self.assertRegex("甜奶酱", expected["TeamLeaderTeammateName"])
        self.assertNotRegex("甜奶酱兔", expected["TeamLeaderTeammateName"])
        self.assertRegex("兔心", expected["TeamLeaderTeammate2Name"])
        self.assertRegex("甜奶酱 全球国际区", expected["TeamLeaderRoomTeammate"])
        self.assertNotRegex("兔心", expected["TeamLeaderRoomTeammate"])
        self.assertRegex("兔心60", expected["TeamLeaderRoomTeammate2"])

    def test_missing_second_teammate_counts_as_present(self):
        # MaaFramework OCR uses regex_search: an empty pattern hits any text in the room's name area.
        expected = self.overrides("甜奶酱")
        self.assertEqual(expected["TeamLeaderRoomTeammate2"], "")
        self.assertIsNotNone(re.search(expected["TeamLeaderRoomTeammate2"], "任意文字"))

    def test_period_stands_in_for_a_symbol_in_a_nickname(self):
        expected = self.overrides("队友.1.")
        self.assertRegex("队友+1]", expected["TeamLeaderTeammateName"])
        self.assertRegex("队友(1)", expected["TeamLeaderRoomTeammate"])

    def test_each_invite_round_selects_its_own_teammate(self):
        nodes = self.leader()
        search = nodes["TeamLeaderInviteSearch"]["next"]
        self.assertIn("[Anchor]TeamLeaderSelectTarget", search)
        self.assertNotIn("TeamLeaderSelectTeammate", search)
        self.assertEqual(nodes["TeamLeaderRoomReady"]["anchor"], {"TeamLeaderSelectTarget": "TeamLeaderSelectTeammate"})
        self.assertEqual(nodes["TeamLeaderInviteAgain"]["anchor"], {"TeamLeaderSelectTarget": "TeamLeaderSelectTeammate2"})
        self.assertEqual(nodes["TeamLeaderSelectTeammate2"]["recognition"]["param"]["all_of"], ["PageTeamInvite", "TeamLeaderTeammate2Name"])
        self.assertEqual(nodes["TeamLeaderTeammate2Name"]["recognition"]["param"]["roi"],
                         nodes["TeamLeaderTeammateName"]["recognition"]["param"]["roi"])
        safety = _all_nodes()
        for field in ("action", "next", "max_hit", "post_delay"):
            self.assertEqual(safety["TeamLeaderSelectTeammate2"].get(field), safety["TeamLeaderSelectTeammate"].get(field), field)

    def test_second_round_runs_only_while_the_second_teammate_is_missing(self):
        nodes = self.leader()
        again = nodes["TeamLeaderInviteAgain"]
        self.assertNotIn("enabled", again)
        self.assertTrue(again["inverse"])
        self.assertEqual(again["recognition"]["param"]["all_of"], ["TeamLeaderRoomTeammate2"])
        self.assertEqual(again["max_hit"], 1)
        # A new room may invite again.
        self.assertEqual(nodes["TeamLeaderRoomReady"]["action"]["param"]["custom_action_param"]["nodes"], ["TeamLeaderInviteAgain"])
        # The name area is only read once the room is back; Fight is a direct hit and must not be reachable earlier.
        self.assertEqual(nodes["TeamLeaderInviteNext"]["next"], ["TeamLeaderInviteRoundCheck", "TeamLeaderInviteStillOpen", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        check = nodes["TeamLeaderInviteRoundCheck"]
        self.assertEqual(check["recognition"]["param"]["all_of"], ["PageTeamRoom"])
        self.assertEqual(check["next"], ["TeamLeaderInviteAgain", "TeamLeaderFight", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])

    def test_room_target_follows_each_selected_layer(self):
        mitama = load_jsonc(self.ROOT / "assets/tasks/Mitama.json")["option"]["选择关卡"]["cases"]
        for case in mitama:
            o = case["pipeline_override"]
            self.assertEqual(o["MitamaRoomTargetLayer"], o["MitamaSelectBaqin"], case["name"])
        options = load_jsonc(self.ROOT / "assets/tasks/Awakening.json")["option"]
        pairs = {"AwakeningTargetType": "AwakeningRoomType", "AwakeningLayerText": "AwakeningRoomLayer"}
        seen = set()
        for option in options.values():
            for case in option.get("cases", []):
                o = case.get("pipeline_override", {})
                for source, target in pairs.items():
                    if source in o:
                        seen.add(target)
                        pattern = o[target]["recognition"]["param"]["expected"]
                        self.assertEqual("^" + pattern + "$", o[source]["recognition"]["param"]["expected"], case["name"])
        self.assertEqual(seen, set(pairs.values()))
        layer = next(c for o in options.values() for c in o.get("cases", []) if c["name"] == "壹层")
        self.assertTrue(re.search(layer["pipeline_override"]["AwakeningRoomLayer"]["recognition"]["param"]["expected"], "觉醒 火麒麟 一层"))

    def test_room_name_area_covers_every_slot(self):
        # Screenshot: own name at x 130-245 (left slot), leader at x 560-680 (middle), names on y 113-140.
        nodes = self.leader()
        for name in ("TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2"):
            x, y, width, height = nodes[name]["recognition"]["param"]["roi"]
            self.assertLessEqual(x, 130, name)
            self.assertGreaterEqual(x + width, 1200, name)
            self.assertLessEqual(y, 113, name)
            self.assertGreaterEqual(y + height, 140, name)

    def test_invite_panel_opens_from_an_empty_slot(self):
        nodes = self.leader()
        self.assertEqual(nodes["TeamLeaderInviteSlot"]["next"],
                         ["TeamLeaderInviteSlotRight", "TeamLeaderInviteSlotMiddle",
                          "TeamLeaderInviteSlotFull", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        for side, click in (("Right", "TeamLeaderOpenInvite"), ("Middle", "TeamLeaderOpenInviteMiddle")):
            slot = nodes["TeamLeaderInviteSlot" + side]
            self.assertTrue(slot["inverse"])
            self.assertNotIn("expected", slot["recognition"]["param"])
            self.assertEqual(slot["next"][-3], click)
        for name in ("TeamLeaderRoomReady", "TeamLeaderInviteAgain", "TeamLeaderReinviteFirst"):
            self.assertEqual(nodes[name]["next"][-3], "TeamLeaderInviteSlot", name)

    def test_missing_teammates_are_invited_again_with_a_limit(self):
        nodes = self.leader()
        fight = nodes["TeamLeaderFight"]["next"]
        self.assertEqual(fight[-5:], ["[JumpBack]TeamLeaderWaitTeammate", "TeamLeaderInviteMissing", "TeamLeaderTeammateNoShow", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertLess(fight.index("TeamLeaderStart"), fight.index("[JumpBack]TeamLeaderWaitTeammate"))
        wait = nodes["TeamLeaderWaitTeammate"]
        self.assertEqual((wait["max_hit"], wait["post_delay"]), (10, 1000))
        missing = nodes["TeamLeaderInviteMissing"]
        self.assertEqual(missing["max_hit"], 3)
        self.assertEqual(missing["next"], ["TeamLeaderReinviteFirst", "TeamLeaderInviteAgain", "TeamLeaderFight", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        resets = missing["action"]["param"]["custom_action_param"]["nodes"]
        for name in ("TeamLeaderWaitTeammate", "TeamLeaderInviteAgain", "TeamLeaderInviteOpenFriends"):
            self.assertIn(name, resets)
        first = nodes["TeamLeaderReinviteFirst"]
        self.assertTrue(first["inverse"])
        self.assertEqual(first["anchor"], {"TeamLeaderSelectTarget": "TeamLeaderSelectTeammate"})
        entry = nodes["TeamLeaderBattleEntry"]["action"]["param"]["custom_action_param"]["nodes"]
        self.assertIn("TeamLeaderWaitTeammate", entry)
        self.assertIn("TeamLeaderInviteMissing", entry)
        self.assertEqual(nodes["TeamLeaderTeammateNoShow"]["next"], ["NativeFlowFailed"])

    def test_member_accepts_only_the_leader_for_the_chosen_dungeon(self):
        task = load_jsonc(self.ROOT / "assets/tasks/TeamMember.json")["option"]["队长昵称"]
        text = substitute(json.dumps(task["pipeline_override"], ensure_ascii=False),
                          {"完整队长昵称": "阿离不离谱", "邀请目标副本": "御魂悲鸣"})
        expected = {node: value["recognition"]["param"]["expected"] for node, value in json.loads(text).items()}
        name, target = expected["TeamMemberLeaderName"], expected["TeamMemberInviteTarget"]
        for text in ("阿离不离谱", "60阿离不离谱", "60 阿离不离谱"):
            self.assertRegex(text, name)
        self.assertNotRegex("阿离不离谱谱", name)
        self.assertRegex("御魂悲鸣", target)
        self.assertNotRegex("御魂神罚", target)
        empty = substitute(json.dumps(task["pipeline_override"], ensure_ascii=False), {"完整队长昵称": "阿离", "邀请目标副本": ""})
        self.assertEqual(json.loads(empty)["TeamMemberInviteTarget"]["recognition"]["param"]["expected"], "")
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        # Screenshots: names start at x 230 (no gear) or 320 (gear) and run long; target line at y 258-285.
        x, y, width, height = nodes["TeamMemberLeaderName"]["recognition"]["param"]["roi"]
        self.assertTrue(x <= 230 and x + width >= 575 and y <= 225 and y + height >= 252)
        x, y, width, height = nodes["TeamMemberInviteTarget"]["recognition"]["param"]["roi"]
        self.assertTrue(x <= 230 and x + width >= 575 and y <= 258 and y + height >= 285)
        for icon, center in (("TeamMemberAcceptIcon", (140, 255)), ("TeamMemberAcceptDefaultIcon", (240, 255))):
            x, y, width, height = nodes[icon]["recognition"]["param"]["roi"]
            self.assertTrue(x <= center[0] <= x + width and y <= center[1] <= y + height, icon)
        plain = nodes["TeamMemberAcceptIcon"]["recognition"]["param"]["roi"]
        self.assertLessEqual(plain[0] + plain[2], nodes["TeamMemberAcceptDefaultIcon"]["recognition"]["param"]["roi"][0])

    def test_teammate_joining_after_being_judged_missing_does_not_stop(self):
        nodes = self.leader()
        self.assertEqual(nodes["TeamLeaderInviteSlot"]["next"][-3], "TeamLeaderInviteSlotFull")
        self.assertEqual(nodes["TeamLeaderInviteSlotFull"]["next"][-3], "TeamLeaderFight")
        missing = nodes["TeamLeaderInviteTeammateMissing"]
        self.assertEqual(missing["recognition"]["param"]["all_of"], ["PageTeamInvite", "NavCancelInvite"])
        self.assertEqual(missing["next"], ["TeamLeaderInviteClosed", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        safety = _all_nodes()
        self.assertEqual(safety["TeamLeaderInviteTeammateMissing"]["next"][-3], "TeamLeaderInviteClosed")
        self.assertEqual(nodes["TeamLeaderInviteClosed"]["next"][-3], "TeamLeaderInviteRoundCheck")
        still_open = nodes["TeamLeaderInviteStillOpen"]
        self.assertEqual(nodes["TeamLeaderInviteNext"]["next"][-4:], ["TeamLeaderInviteRoundCheck", "TeamLeaderInviteStillOpen", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertEqual(still_open["next"][-4:], ["TeamLeaderInviteRoundCheck", "TeamLeaderInviteTeammateMissing", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertEqual(still_open["max_hit"], 3)

    def test_member_taps_speak_while_waiting_in_the_room(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        loop = nodes["TeamMemberLoop"]["next"]
        self.assertEqual(loop[-5:], ["[JumpBack]TeamMemberRoomTick", "[JumpBack]TeamMemberIdleEmoteReady", "[JumpBack]TeamMemberRoomWait", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertEqual((nodes["TeamMemberRoomTick"]["max_hit"], nodes["TeamMemberRoomTick"]["post_delay"]), (15, 1000))
        emote = nodes["TeamMemberIdleEmoteReady"]
        self.assertEqual(emote["action"]["param"]["custom_action_param"]["nodes"], ["TeamMemberRoomTick"])
        self.assertEqual(emote["next"][-3], "TeamMemberIdleEmote")
        # Screenshot: the speak text sits at x 30-75, y 580-602 with its icon right above (OAS roi 27,526,55,51).
        x, y, width, height = nodes["TeamRoomSpeakButton"]["recognition"]["param"]["roi"]
        self.assertTrue(x <= 30 and x + width >= 75 and y <= 580 and y + height >= 602)
        safety = _all_nodes()
        self.assertEqual(safety["TeamMemberIdleEmote"]["action"]["param"]["target_offset"], [0, -48, 0, 22])
        self.assertEqual(safety["TeamMemberIdleEmote"]["next"], [])

    def test_member_defaults_accept_any_chinese_leader_and_target(self):
        option = load_jsonc(self.ROOT / "assets/tasks/TeamMember.json")["option"]["队长昵称"]
        name_input = option["inputs"][0]
        self.assertEqual(name_input["default"], "[一-龥]+")
        self.assertRegex(name_input["default"], name_input["verify"])
        target_input = option["inputs"][1]
        self.assertEqual(target_input["default"], "[一-龥]+")
        self.assertRegex(target_input["default"], target_input["verify"])
        text = substitute(json.dumps(option["pipeline_override"], ensure_ascii=False),
                          {"完整队长昵称": name_input["default"], "邀请目标副本": target_input["default"]})
        expected = json.loads(text)
        pattern = expected["TeamMemberLeaderName"]["recognition"]["param"]["expected"]
        target = expected["TeamMemberInviteTarget"]["recognition"]["param"]["expected"]
        for line in ("御魂悲鸣", "觉醒火麒麟", "目标：御魂 悲鸣"):
            self.assertRegex(line, target)
        for name in ("阿离不离谱", "60阿离不离谱", "60 潮生云雾"):
            self.assertRegex(name, pattern)
        for name in ("Alice", "阿离abc", "阿离_01"):
            self.assertNotRegex(name, pattern)

    def test_member_confirms_auto_ready_after_default_accept(self):
        # Screenshot: prompt line 并在战前自动准备 at x 543-783, y 305-332; 确定 at x 665-842, y 408-470.
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        loop = nodes["TeamMemberLoop"]["next"]
        # Invites and the prompt expire in seconds, so they lead the loop.
        self.assertEqual(loop[0:3], ["TeamMemberAutoReadyConfirm", "TeamMemberAcceptDefault", "TeamMemberAccept"])
        self.assertEqual(nodes["TeamMemberAfterAccept"]["next"], ["TeamMemberAutoReadyConfirm", "TeamMemberAcceptJoined", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertEqual(nodes["TeamMemberAfterAccept"]["on_error"], ["TeamMemberLoop"])
        self.assertLessEqual(nodes["TeamMemberAfterAccept"]["timeout"], 5000)
        x, y, w, h = nodes["TeamMemberAutoReadyText"]["recognition"]["param"]["roi"]
        self.assertTrue(x <= 543 and x + w >= 783 and y <= 305 and y + h >= 332)
        x, y, w, h = nodes["TeamMemberAutoReadyButton"]["recognition"]["param"]["roi"]
        self.assertTrue(x <= 722 and x + w >= 785 and y <= 425 and y + h >= 452 and x >= 620)
        self.assertEqual(nodes["TeamMemberAutoReadyConfirm"]["recognition"]["param"]["box_index"], 1)
        safety = _all_nodes()
        self.assertEqual(safety["TeamMemberAutoReadyConfirm"]["next"], ["TeamMemberLoop"])
        for name in ("TeamMemberAccept", "TeamMemberAcceptDefault"):
            # A tap that did not register leaves the invite visible: tap again, bounded by max_hit.
            self.assertEqual(safety[name]["next"], ["TeamMemberAfterAccept", name, "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
            self.assertLessEqual(safety[name]["post_delay"], 500)

    def test_member_counts_one_win_per_battle(self):
        # Log 2026-11-13 01:45: the win and reward screens both counted (32.6s and 46.1s) for one battle.
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        self.assertEqual(nodes["TeamMemberWon"]["max_hit"], 1)
        in_battle = nodes["TeamMemberInBattle"]
        self.assertEqual(in_battle["max_hit"], 1)
        self.assertEqual(in_battle["action"]["param"]["custom_action_param"]["nodes"], ["TeamMemberWon"])
        loop = nodes["TeamMemberLoop"]["next"]
        self.assertLess(loop.index("[JumpBack]TeamMemberInBattle"), loop.index("[JumpBack]DailyPrepare"))
        resets = nodes["TeamMemberBattleEntry"]["action"]["param"]["custom_action_param"]["nodes"]
        self.assertIn("TeamMemberInBattle", resets)
        self.assertNotIn("TeamMemberWon", resets)
        safety = _all_nodes()
        self.assertEqual(safety["TeamMemberBattleResult"]["next"][-4:], ["TeamMemberWon", "TeamMemberResultCleared", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertEqual(nodes["TeamMemberResultCleared"]["next"], ["TeamMemberAfterResult"])

    def test_member_after_result_short_route(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        self.assertEqual(nodes["TeamMemberBattleEntry"]["next"], ["TeamMemberAfterResult"])
        after = nodes["TeamMemberAfterResult"]
        self.assertEqual(after["next"], ["TeamMemberAutoReadyConfirm", "TeamMemberAcceptDefault", "TeamMemberAccept",
                                         "TeamMemberWinResult", "TeamMemberResultSeen", "TeamMemberAfterResultRoom", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
        self.assertLess(len(after["next"]), len(nodes["TeamMemberLoop"]["next"]) // 2)
        self.assertEqual(after["on_error"], ["TeamMemberLoop"])
        self.assertLessEqual(after["timeout"], 8000)
        self.assertEqual(nodes["TeamMemberAfterResultRoom"]["next"], ["TeamMemberLoop"])

    def test_member_loop_drops_pure_waits_and_post_result_prompts(self):
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        loop = nodes["TeamMemberLoop"]["next"]
        self.assertNotIn("[JumpBack]CommonBattleEnteringWait", loop)
        self.assertNotIn("[JumpBack]CommonRewardOverflow", loop)
        safety = _all_nodes()
        self.assertIn("[JumpBack]CommonRewardOverflow", safety["TeamMemberBattleResult"]["next"])

    def test_member_checks_defeat_only_on_a_result_screen(self):
        # DailyBattleFailed reads 694x444; it runs only after the small result prompt is seen.
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        for router in ("TeamMemberLoop", "TeamMemberAfterResult"):
            self.assertNotIn("TeamMemberBattleFailure", nodes[router]["next"], router)
            self.assertIn("TeamMemberResultSeen", nodes[router]["next"], router)
        seen = nodes["TeamMemberResultSeen"]
        self.assertEqual(seen["recognition"]["param"]["all_of"], ["DailyBattleResult"])
        self.assertEqual(seen["next"], ["TeamMemberBattleFailure", "TeamMemberBattleResult", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])

    def test_member_clicks_win_pages_without_the_defeat_check(self):
        # Screenshots: page one shows 胜利 at (700,75), page two the reward bag at (560,470); both only on a win.
        common = json.loads((self.ROOT / "assets/resource/base/pipeline/Common.json").read_text(encoding="utf-8"))
        self.assertEqual(common["CommonBattleWinPage"]["recognition"]["param"]["any_of"], ["CommonBattleWinDrum", "CommonBattleRewardBag"])
        drum = common["CommonBattleWinDrum"]["recognition"]["param"]
        self.assertEqual(common["CommonBattleWinDrum"]["recognition"]["type"], "ColorMatch")
        # Small drum early in the animation (445,175,70,60) and the settled big drum (395,95,175,180) both fit.
        x, y, w, h = drum["roi"]
        for x0, y0, w0, h0 in ((445, 175, 70, 60), (395, 95, 175, 180), (395, 120, 175, 170)):
            self.assertTrue(x <= x0 and y <= y0 and x + w >= x0 + w0 and y + h >= y0 + h0)
        # Bright-red pixel counts measured on screenshots: win 1111, early win 144, defeat 0.
        self.assertTrue(0 < drum["count"] < 144)
        for name, (x0, y0, w0, h0) in (("CommonBattleRewardBag", (560, 470, 180, 120)),):
            param = common[name]["recognition"]["param"]
            self.assertTrue((self.ROOT / "assets/resource/base/image" / param["template"]).is_file(), name)
            x, y, w, h = param["roi"]
            self.assertTrue(x <= x0 and y <= y0 and x + w >= x0 + w0 and y + h >= y0 + h0, name)
            self.assertLessEqual(w * h, 400 * 230, name)
        nodes = json.loads((self.ROOT / "assets/resource/base/pipeline/TeamMember.json").read_text(encoding="utf-8"))
        self.assertEqual(nodes["TeamMemberWinResult"]["recognition"]["param"]["all_of"], ["DailyBattleResult", "CommonBattleWinPage"])
        for router in ("TeamMemberLoop", "TeamMemberAfterResult"):
            nxt = nodes[router]["next"]
            self.assertLess(nxt.index("TeamMemberWinResult"), nxt.index("TeamMemberResultSeen"), router)
        safety = _all_nodes()
        win_next = safety["TeamMemberWinResult"]["next"]
        self.assertNotIn("TeamMemberBattleFailure", win_next)
        self.assertEqual(win_next[0], "TeamMemberWinResult")
        self.assertEqual(win_next[-4:], ["TeamMemberWon", "TeamMemberResultCleared", "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])

    def test_empty_invite_tab_counts_as_fitting_one_page(self):
        import re as _re
        nodes = self.leader()
        param = nodes["TeamLeaderInviteListFits"]["recognition"]["param"]
        for text in ("在线：0", "在线:0", "在线 : 0", "在线：8"):
            self.assertTrue(_re.search(param["expected"], text), text)
        self.assertFalse(_re.search(param["expected"], "在线：12"))
        # Screenshot: 在线：0 sits at about (848,108,82,24).
        x, y, w, h = param["roi"]
        self.assertTrue(x <= 848 and y <= 108 and x + w >= 930 and y + h >= 132)

    def test_invite_search_recovers_when_the_game_closes_the_panel(self):
        nodes = self.leader()
        gone = nodes["TeamLeaderInvitePanelGone"]
        self.assertTrue(gone["inverse"])
        self.assertEqual(gone["recognition"]["param"]["all_of"], ["PageTeamInvite"])
        self.assertEqual(gone["next"][-3], "TeamLeaderInviteClosed")
        for name in ("TeamLeaderInviteSearch", "TeamLeaderInviteAdvance"):
            nxt = nodes[name]["next"]
            self.assertIn("TeamLeaderInvitePanelGone", nxt, name)
        safety = _all_nodes()
        for tab in ("Friends", "Recent", "Guild", "Cross"):
            self.assertEqual(safety["TeamLeaderInviteOpen" + tab]["next"][-3], "TeamLeaderInvitePanelGone", tab)

