"""Exercise production Pipeline routing with MaaFramework and a simulated screen.

Run separately from Agent unit tests: python tools/check_native_pipeline.py
No emulator is connected. Only image predicates, input actions and counters are
stubbed; next/JumpBack/anchor/max_hit/error handling use the actual framework.
"""
import json
import argparse
import struct
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agent"))
from custom.route_policy import with_interrupts
from custom.battle_session import run_battle_session
from probe_pages import ScreenshotController
from validate_schema import load_jsonc
from maa.context import ContextEventSink
from maa.custom_action import CustomAction
from maa.event_sink import NotificationType
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from maa.pipeline import JClick, JSwipe


def blank_png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1280, 720, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" * (1280 * 3 + 1)) * 720)) + chunk(b"IEND", b""))


class Scenario:
    def __init__(self, flags, events):
        self.flags = set(flags)
        self.events = list(events)
        self.clicks = []
        self.count = 0
        self.target = 1
        self.error = None
        self.readable = True
        self.risk_after_probe = None
        self.completed_steps = []
        self.probe_events = []
        self.statistics = 0
        self.pending_statistics = False
        self.frame = 0
        self.probe_frames = {}
        self.touch_blocked = False


class CountingController(ScreenshotController):
    """Count framework captures without changing recognition or routing."""
    captures = 0

    def screencap(self):
        self.captures += 1
        if hasattr(self, 'scenario'):
            self.scenario.frame = self.captures
        return super().screencap()


class RegionController(ScreenshotController):
    """Capture actual coordinates chosen by the framework; never send real input."""
    def __init__(self, path):
        super().__init__(path)
        self.clicks, self.swipes = [], []

    def click(self, x, y):
        self.clicks.append((x, y))
        return True

    def swipe(self, x1, y1, x2, y2, duration):
        self.swipes.append((x1, y1, x2, y2))
        return True


class RegionApiProbe(CustomAction):
    def run(self, context, argv):
        for _ in range(8):
            result = context.run_action_direct("Click", JClick(target=[100, 100, 40, 30]))
            assert result is not None and result.success
            result = context.run_action_direct("Swipe", JSwipe(begin=[100, 500, 30, 30], end=[[800, 100, 30, 30]], duration=[1]))
            assert result is not None and result.success
        return self.RunResult(success=True)


class Screen(CustomRecognition):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def analyze(self, context, argv):
        params = json.loads(argv.custom_recognition_param)
        name = params["probe"]
        self.state.probe_frames[name] = self.state.frame
        matched = name in self.state.flags
        if name == 'CommonBuffMenuIcon':
            matched = bool(self.state.flags & {'CommonBuffMenuIcon', 'MitamaOpenBuffMenu', 'AwakeningOpenBuffMenu'})
        if name == 'DailyPrepare':
            matched = bool(self.state.flags & {'DailyPrepare','MitamaPrepare'})
        if name in ('DailyBattleResult','MitamaBattleResult'):
            matched = bool(self.state.flags & {'DailyBattleResult','MitamaBattleResult'})
        if name in ('MitamaChallenge', 'MitamaFinishOnce'):
            matched = name in self.state.flags and 'WrongMitamaLayer' not in self.state.flags
        if name == 'PageBattle':
            matched = bool(self.state.flags & {'PageBattle', 'MitamaChallenging', 'CommonActivityChallenging',
                'AwakeningChallenging'})
        if name in ('RealmRaidSelectedCard', 'RealmRaidNickname', 'RealmRaidUnmarkedCorner'):
            matched = 'RealmRaidCard' in self.state.flags
            if name == 'RealmRaidNickname' and 'RaidNicknameMissing' in self.state.flags:
                matched = False
            if name == 'RealmRaidUnmarkedCorner' and 'RaidCornerMarked' in self.state.flags:
                matched = False
        if name == 'PageRaidTarget':
            matched = bool(self.state.flags & {'PageRaidTarget', 'RealmRaidChallenge'})
        if matched and self.state.probe_events and self.state.probe_events[0][0] == name:
            _, flags = self.state.probe_events.pop(0)
            self.state.flags = set(flags)
        if matched and name == self.state.risk_after_probe:
            self.state.flags.add("TouchAreaRisk")
            self.state.risk_after_probe = None
        if name == 'CommonUnknownPopup':
            # A confirm/cancel popup no rule handles (purchase prompts included) stops the task.
            matched = bool(self.state.flags & {'TouchAreaRisk', 'CommonUnknownPopup'})
            if matched:
                self.state.touch_blocked = True
        return [100, 100, 10, 10] if matched else None


class Input(CustomAction):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def run(self, context, argv):
        state = self.state
        name = argv.node_name
        # The click must use the frame on which its own node was recognized.
        if json.loads(argv.custom_action_param or '{}').get('check_frame', True):
            if name in state.probe_frames and state.probe_frames[name] != state.frame:
                state.error = f"Input {name} used a stale frame {state.frame}"
                return self.RunResult(success=False)
        state.clicks.append(name)
        if not state.events or state.events[0][0] != name:
            state.error = f"Unexpected input {name}; remaining {state.events}"
            return self.RunResult(success=False)
        _, flags = state.events.pop(0)
        state.flags = set(flags)
        return self.RunResult(success=True)


# Nodes that finish one preparation or cleanup step in the native chains.
STEP_DONE = {
    "MitamaLockedTeam", "MitamaPresetDone", "MitamaCheckBuffStatusOpen", "MitamaCheckBuffStatusClose",
    "AwakeningLockedTeam", "AwakeningPresetDone", "AwakeningBuffEnabled", "AwakeningBuffDisabled",
}


class StepRecorder(ContextEventSink):
    """Record finished preparation steps, including those inside run_task sessions."""
    def __init__(self, state):
        super().__init__()
        self.state = state

    def on_node_pipeline_node(self, context, noti_type, detail):
        if noti_type == NotificationType.Succeeded and detail.name in STEP_DONE:
            self.state.completed_steps.append(detail.name)


class Counter(CustomAction):
    def __init__(self, state, kind):
        super().__init__()
        self.state, self.kind = state, kind

    def run(self, context, argv):
        params = json.loads(argv.custom_action_param or "{}")
        if self.kind == "RunBattleSession":
            return self.RunResult(success=run_battle_session(context, params))
        elif self.kind == "TargetCountInit":
            params.update(context.get_node_data(argv.node_name).get("attach") or {})
            self.state.target = int(params.get("target_count", 1))
            self.state.count = 0
        elif self.kind == "TargetCountProgress":
            self.state.count += params.get("count", 1)
            if self.state.pending_statistics:
                self.state.statistics += 1
                self.state.pending_statistics = False
            if "continue_node" in params:
                done = self.state.target != -1 and self.state.count >= self.state.target
                context.override_next(argv.node_name, with_interrupts([params["finish_node"] if done else params["continue_node"]], context))
        elif self.kind == "TargetCountDetermine":
            done = self.state.target != -1 and self.state.count >= self.state.target
            context.override_next(params["decision_node"], with_interrupts([params["finish_node"] if done else params["continue_node"]], context))
        elif self.kind == "ResetCount":
            for node in params["nodes"]:
                if not context.clear_hit_count(node):
                    return self.RunResult(success=False)
        elif self.kind == "RewardDropStatistics":
            self.state.pending_statistics = True
        elif self.kind == "NodeOverride":
            return self.RunResult(success=context.override_pipeline(params))
        return self.RunResult(success=True)


def run_case(resource, controller, nodes, name, entry, flags, events=(), expected_count=0, succeeds=True,
             readable=True, risk_after_probe=None, prepare_only=False, expected_steps=None, probe_events=(),
             enable_drop_statistics=False, expected_statistics=0, requested_count=1, awakening_buff=False,
             preset_task=None, stop_after_input=None, expected_captures=None, pipeline_override=None,
             expect_popup_stop=None):
    state = Scenario(flags, events)
    controller.scenario = state
    state.target = requested_count
    state.probe_events = list(probe_events)
    state.readable = readable
    state.risk_after_probe = risk_after_probe
    # Framework 5.13 rejects duplicate registration instead of replacing it.
    assert resource.clear_custom_recognition()
    assert resource.clear_custom_action()
    assert resource.register_custom_recognition("SimScreen", Screen(state))
    assert resource.register_custom_action("SimInput", Input(state))
    for kind in ("TargetCountInit", "TargetCountProgress", "TargetCountDetermine", "TargetCountFinish",
                 "ResetCount", "RememberFrame", "RewardDropStatistics", "NodeOverride", "RunBattleSession",
                 "ResolveGamePackage"):
        assert resource.register_custom_action(kind, Counter(state, kind))
    override = {}
    for key, original in nodes.items():
        node = {"pre_delay": 0, "post_delay": 0, "rate_limit": 1, "timeout": 100,
                "pre_wait_freezes": 0, "post_wait_freezes": 0}
        recognition = original.get("recognition", {})
        if isinstance(recognition, dict) and "type" in recognition:
            param = recognition.get("param", {})
            children = param.get("all_of", []) + param.get("any_of", [])
            if key in ('MitamaChallenge', 'MitamaFinishOnce') or key.startswith("Page") or recognition["type"] not in ("And", "Or") or any(isinstance(c, dict) for c in children):
                node["recognition"] = {"type": "Custom", "param": {
                    "custom_recognition": "SimScreen", "custom_recognition_param": {"probe": key}}}
        action = original.get("action", "DoNothing")
        kind = action.get("type") if isinstance(action, dict) else action
        bezier = isinstance(action, dict) and action.get("param", {}).get("custom_action") == "BezierSwipe"
        if kind in ("Click", "Swipe", "StartApp") or bezier:
            node["action"] = {"type": "Custom", "param": {"custom_action": "SimInput", "custom_action_param": {"check_frame": kind != "StartApp"}}}
        # A previous scenario may have overridden this decision's next list.
        if "next" in original:
            node["next"] = original["next"]
        override[key] = node
    for entry_node in ("MitamaTask", "ExplorationTask", "RealmRaidTask", "CommonActivityTask", "AwakeningTask", "BondlingSearchTask", "TeamMemberTask"):
        # Same shape as task options: the report option replaces custom_action_param whole, the count lives in attach.
        override[entry_node]["attach"] = {"target_count": requested_count}
        override[entry_node]["action"] = {"param": {"custom_action_param": {"report_drops": False}}}
    if prepare_only:
        override["MitamaLockTeam"]["enabled"] = False
        override["MitamaPreset"]["enabled"] = True
        override["MitamaEnableBuff"]["enabled"] = False
        override["MitamaPreparationDestination"]["next"] = []
    for stats in ("MitamaSoloDropStatistics", "TeamLeaderDropStatistics", "AwakeningDropStatistics"):
        override[stats]["enabled"] = enable_drop_statistics
    for step in ("AwakeningEnableBuff", "AwakeningDisableBuff"):
        override[step]["enabled"] = awakening_buff
    override["AwakeningPreset"]["enabled"] = preset_task == "Awakening"
    if stop_after_input:
        override[stop_after_input]['next'] = []
    # Cases entering the shared leader flow midway never pass an entry node, so give them Mitama's ending.
    override["TeamLeaderFinish"]["next"] = ["MitamaFinish"]
    override["TeamLeaderBattleNoResource"]["next"] = ["[JumpBack]CommonBountyInterrupt", "MitamaFinish"]
    if pipeline_override:
        # Keep structural fields from the normal simulation and merge explicit
        # scenario overrides just as the task UI does.
        def merge(dst, src):
            for key, value in src.items():
                if isinstance(value, dict) and isinstance(dst.get(key), dict):
                    merge(dst[key], value)
                else:
                    dst[key] = value
        merge(override, pipeline_override)
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    tasker.add_context_sink(StepRecorder(state))
    captures_before = controller.captures
    result = tasker.post_task(entry, override).wait()
    succeeded = result.succeeded and not state.touch_blocked
    if not succeeded:
        tasker.post_stop().wait()
    assert succeeded == succeeds, (name, result.status, state.error, state.events, state.count)
    if expect_popup_stop is not None:
        assert state.touch_blocked == expect_popup_stop, (name, "popup stop", state.touch_blocked)
    assert not state.error, (name, state.error)
    assert not state.events, (name, state.events)
    assert not state.probe_events, (name, state.probe_events)
    assert state.count == expected_count, (name, state.count)
    assert state.statistics == expected_statistics, (name, state.statistics)
    if expected_captures is not None:
        assert controller.captures - captures_before == expected_captures, (name, controller.captures - captures_before)
    if expected_steps is not None:
        assert state.completed_steps == expected_steps, (name, state.completed_steps)
    print(f"PASS {name}: {len(state.clicks)} inputs, {state.count} successful battles, {controller.captures - captures_before} captures", flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipeline-dir',type=Path,default=ROOT/'assets/resource/base/pipeline')
    args=parser.parse_args()
    Toolkit.init_option(ROOT / "debug/native_pipeline_check")
    nodes = {}
    for filename in args.pipeline_dir.glob("*.json"):
        if filename.name != "Example.json":
            nodes.update(load_jsonc(filename))
    resource = Resource()
    assert resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded
    if args.pipeline_dir.resolve() != (ROOT/'assets/resource/base/pipeline').resolve():
        assert resource.post_bundle(args.pipeline_dir.parent).wait().succeeded
    with tempfile.TemporaryDirectory() as folder:
        screen = Path(folder) / "screen.png"
        screen.write_bytes(blank_png())
        controller = CountingController(screen)
        assert controller.post_connection().wait().succeeded
        failures = []

        def case(*args, **kwargs):
            try:
                run_case(resource, controller, nodes, *args, **kwargs)
            except AssertionError as exc:
                failures.append(args[0])
                print(f"FAIL {args[0]}: {exc}", flush=True)
        case('challenge resets per-battle budget before matched input', 'MitamaBattleModeDestination', {'MitamaChallenge'},
            [('MitamaChallenge', set())], stop_after_input='MitamaChallenge', expected_captures=2)
        case('unknown popup hiding the challenge stops the task', 'MitamaBattleModeDestination', {'TouchAreaRisk'},
            succeeds=False, expect_popup_stop=True)
        case('invitation hiding the challenge is refused, then the challenge is clicked', 'MitamaBattleModeDestination', {'CommonRefuseBounty'},
            [('CommonRefuseBounty', {'MitamaChallenge'}), ('MitamaChallenge', set())], stop_after_input='MitamaChallenge')
        for page, entry in [('Home', 'NavigateHome'), ('Explore', 'NavigateExplore'),
                            ('MitamaStage', 'NavigateMitamaStage'), ('AwakeningStage', 'NavigateAwakening'), ('Bondling', 'NavigateBondling'),
                            ('Raid', 'NavigateRaid'), ('Chapter', 'NavigateChapter'), ('Mail', 'NavigateMail'),
                            ('QuestTasks', 'NavigateQuestTasks'), ('Quests', 'NavigateQuests')]:
            # The invite is rare, so it is checked only once the page itself is no longer recognized.
            case('cooperative invite masks ' + page, entry, {'CommonRefuseBounty'},
                [('CommonRefuseBounty', {'Page' + page})])
        case('cooperative invite is refused before the unknown-popup stop', 'NavigateHome', {'CommonRefuseBounty', 'TouchAreaRisk'},
            [('CommonRefuseBounty', {'PageHome'})], expect_popup_stop=False)
        case('unknown popup hiding the page stops navigation', 'NavigateHome', {'TouchAreaRisk'}, succeeds=False, expect_popup_stop=True)
        case('cooperative invite closes after missed refusal', 'NavigateHome', {'CommonRefuseBounty'}, [
            ('CommonRefuseBounty', {'CommonRefuseBounty'}), ('CommonRefuseBounty', {'PageHome'})])
        case('cooperative invite stuck stops after three refusals', 'NavigateHome', {'CommonRefuseBounty'},
            [('CommonRefuseBounty', {'CommonRefuseBounty'})] * 3, succeeds=False)
        case('four cooperative invites in one task get independent retry budgets', 'CommonActivityTask', {'CommonRefuseBounty'}, [
            ('CommonRefuseBounty', {'CommonActivityChallenge'}),
            ('CommonActivityChallenge', {'CommonRefuseBounty'}),
            ('CommonRefuseBounty', {'CommonActivityChallenging'}),
            ('CommonRefuseBounty', {'CommonActivityBattleResult'}),
            ('CommonActivityBattleResult', {'CommonRefuseBounty'}),
            ('CommonRefuseBounty', {'CommonActivityChallenge'})], expected_count=1,
            probe_events=[('PageBattle', {'CommonRefuseBounty'})])
        case("already at target", "NavigateRaid", {"PageRaid"})
        case("prefer home icon", "ReturnMain", {"PageMitamaStage", "ReturnMainHomeButton", "NavBack"},
             [("NativeReturnMainHomeButton", {"PageHome"})])
        case("back-only page", "ReturnMain", {"PageQuests", "NavBack"},
             [("NativeNavBack", {"PageHome"})])
        case("chapter to raid without courtyard", "NavigateRaid", {"PageChapterField", "NavBack"}, [
            ("NativeNavBack", {"PageExitExplore", "NavConfirmExitExplore"}),
            ("NativeNavConfirmExitExplore", {"PageChapter", "NavBack"}),
            ("NativeNavBack", {"PageExplore", "NavOpenRaid"}),
            ("NativeToRaidNavOpenRaid", {"PageRaid"})])
        case("refuse navigation during battle", "NavigateHome", {"PageBattle"}, succeeds=False)
        stop_at_layer = {"MitamaLayerReady": {"next": []}}
        case("mitama entry from another page navigates once and confirms the layer", "MitamaNavigate", {"PageExplore", "NavOpenMitama"}, [
            ("NativeToMitamaStageNavOpenMitama", {"PageMitamaMenu", "NavOpenBaqi"}),
            ("NativeToMitamaStageNavOpenBaqi", {"PageMitamaStage", "MitamaLayerSelected"})], pipeline_override=stop_at_layer)
        case("mitama entry already on the stage clicks nothing", "MitamaNavigate", {"PageMitamaStage", "MitamaLayerSelected"},
             pipeline_override=stop_at_layer)
        case("mitama entry still clears a popup covering the stage", "MitamaNavigate", {"PageMitamaStage", "MitamaLayerSelected", "DailyRewardPopup"}, [
            ("NativeDailyRewardPopup", {"PageMitamaStage", "MitamaLayerSelected"})], pipeline_override=stop_at_layer)
        case("mitama entry stops when the stage is lost after navigating", "MitamaNavigate", {"PageMitamaStage", "MitamaLayerSelected"},
             succeeds=False, probe_events=[("PageMitamaStage", {"PageHome"})])
        case("navigation stops on an unknown popup hiding the page", "NavigateMitamaStage",
             {"TouchAreaRisk"}, succeeds=False, expect_popup_stop=True)
        case("leader battle never counts a waiting room", "TeamLeaderBattle", {"PageTeamRoom"}, succeeds=False)
        case("leader does not start for an unmatched teammate", "TeamLeaderFight", {"PageTeamRoom"}, succeeds=False)
        room_wait = {"TeamMemberRoomWait": {"max_hit": 3}}
        invite = {"TeamMemberLeaderName", "TeamMemberInviteTarget", "TeamMemberAcceptIcon"}
        case("waiting room is not a victory", "TeamMemberTask", {"PageTeamRoom"}, succeeds=False, pipeline_override=room_wait)
        # Anti-idle: about every 15 seconds in the room the member taps the speak button, then keeps waiting.
        speak = {"PageTeamRoom", "TeamRoomSpeakButton"}
        case("member taps the speak button while waiting and still joins the battle", "TeamMemberTask", speak, [
            ("TeamMemberIdleEmote", speak | {"DailyBattleResult"}), ("TeamMemberBattleResult", set())], expected_count=1,
            pipeline_override={"TeamMemberRoomTick": {"max_hit": 2}})
        case("unmatched invitation is not accepted", "TeamMemberTask", {"TeamMemberAcceptIcon"}, succeeds=False)
        case("named invitation and team settlement", "TeamMemberTask", invite, [
            ("TeamMemberAccept", {"DailyBattleResult"}),
            ("TeamMemberBattleResult", set())], expected_count=1)
        # Default accept (gear check) pops an auto-ready confirmation; confirm it before anything else.
        default_invite = (invite - {"TeamMemberAcceptIcon"}) | {"TeamMemberAcceptDefaultIcon"}
        auto_ready = default_invite | {"TeamMemberAutoReadyText", "TeamMemberAutoReadyButton"}
        case("default accept confirms the auto-ready prompt", "TeamMemberTask", default_invite, [
            ("TeamMemberAcceptDefault", auto_ready), ("TeamMemberAutoReadyConfirm", {"DailyBattleResult"}),
            ("TeamMemberBattleResult", set())], expected_count=1)
        # The prompt may not appear at all: the member lands straight in the room and waits for the leader.
        case("default accept without a prompt goes straight to the room", "TeamMemberTask", default_invite, [
            ("TeamMemberAcceptDefault", {"PageTeamRoom"}), ("TeamMemberBattleResult", set())], expected_count=1,
            probe_events=[("PageTeamRoom", {"DailyBattleResult"})])
        # One battle shows two result screens (win, then rewards); only the first counts, the next battle counts again.
        case("member counts one win per battle despite a second result screen", "TeamMemberTask", {"DailyBattleResult"}, [
            ("TeamMemberBattleResult", {"PageTeamRoom"}), ("TeamMemberBattleResult", {"PageTeamRoom"}),
            ("TeamMemberBattleResult", {"PageTeamRoom"})], requested_count=2, expected_count=2,
            probe_events=[("PageTeamRoom", {"DailyBattleResult"}), ("PageTeamRoom", {"PageBattle"}), ("PageBattle", {"DailyBattleResult"})])
        # The next invite shows up right after the result: the short post-result route accepts it.
        case("member accepts the next invite right after the result", "TeamMemberTask", {"DailyBattleResult"}, [
            ("TeamMemberBattleResult", invite), ("TeamMemberAccept", {"PageBattle"}),
            ("TeamMemberBattleResult", set())], requested_count=2, expected_count=2,
            probe_events=[("PageBattle", {"DailyBattleResult"})])
        # A win shows the red drum then the reward bag; both are clicked straight away and counted once.
        case("member taps both win pages without the defeat check", "TeamMemberTask", {"DailyBattleResult", "CommonBattleWinDrum"}, [
            ("TeamMemberWinResult", {"DailyBattleResult", "CommonBattleRewardBag"}), ("TeamMemberWinResult", set())],
            expected_count=1)
        case("member accepts again for every battle", "TeamMemberTask", invite, [
            ("TeamMemberAccept", {"DailyBattleResult"}),
            ("TeamMemberBattleResult", invite),
            ("TeamMemberAccept", {"DailyPrepare"}),
            ("DailyPrepare", {"DailyBattleResult"}),
            ("TeamMemberBattleResult", set())], requested_count=2, expected_count=2)
        case("member prefers default accept over plain accept", "TeamMemberTask", invite | {"TeamMemberAcceptDefaultIcon"}, [
            ("TeamMemberAcceptDefault", {"DailyBattleResult"}),
            ("TeamMemberBattleResult", set())], expected_count=1)
        case("unmatched invitation is not default accepted", "TeamMemberTask", {"TeamMemberAcceptDefaultIcon"}, succeeds=False)
        case("invitation from the leader for another dungeon is not accepted", "TeamMemberTask",
             (invite - {"TeamMemberInviteTarget"}) | {"TeamMemberAcceptDefaultIcon"}, succeeds=False)
        case("member stays joined after default accept across battles", "TeamMemberTask", invite | {"TeamMemberAcceptDefaultIcon"}, [
            ("TeamMemberAcceptDefault", {"DailyBattleResult"}),
            ("TeamMemberBattleResult", {"PageTeamRoom", "DailyBattleResult"}),
            ("TeamMemberBattleResult", set())], requested_count=1, expected_count=1)
        case("member keeps waiting in the room until the battle starts", "TeamMemberTask", {"PageTeamRoom"}, [
            ("TeamMemberBattleResult", set())], expected_count=1,
            probe_events=[("PageTeamRoom", {"PageTeamRoom"}), ("PageTeamRoom", {"DailyBattleResult"})])
        case("member joins a battle the leader already started", "TeamMemberTask", {"DailyBattleResult"}, [
            ("TeamMemberBattleResult", set())], expected_count=1)
        case("member defeat stops without counting or waiting for another invite", "TeamMemberTask", {"DailyBattleFailed", "DailyBattleResult"}, [
            ("TeamMemberFailedResult", invite)], succeeds=False)
        case("member stops when the leader left and the room is now theirs", "TeamMemberTask", {"PageTeamRoom", "TeamLeaderStartButton"}, succeeds=False)
        case("frozen screen stops: member accept never joins", "TeamMemberTask", invite, [("TeamMemberAccept", invite)] * 5, succeeds=False)
        case("frozen screen stops: member settlement never closes", "TeamMemberTask", {"DailyBattleResult"},
             [("TeamMemberBattleResult", {"DailyBattleResult"})] * 8, succeeds=False)
        case("exploration counts after settlement", "ExplorationTask", {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1)
        case("exploration merged loop counts two battles and stops", "ExplorationTask", {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField", "ExplorationEnemyProbe"}),
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], requested_count=2, expected_count=2)
        case("exploration failure is not a victory", "ExplorationTask", {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyBattleFailed", "DailyBattleResult"}),
            ("ExplorationBattleFailedResult", {"PageChapterField"})], succeeds=False)
        ticket_stop = {"ExplorationTicketThreshold": {"enabled": True}}
        case("exploration ticket threshold finishes on map", "ExplorationTask", {"PageExplore", "ExplorationTicketCountMap"},
            pipeline_override=ticket_stop)
        case("exploration ticket threshold finishes at chapter entrance", "ExplorationTask",
            {"PageChapter", "ExplorationEnterChapter", "ExplorationTicketCountEntrance"}, pipeline_override=ticket_stop)
        case("exploration ticket threshold stops in field after counting the battle", "ExplorationTask",
            {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField", "ExplorationEnemyProbe", "ExplorationTicketCountField"})],
            requested_count=3, expected_count=1, pipeline_override=ticket_stop)
        case("exploration ticket count read for another page is ignored", "ExplorationTask",
            {"PageChapterField", "ExplorationEnemyProbe", "ExplorationTicketCountMap", "ExplorationTicketCountEntrance"}, [
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1, pipeline_override=ticket_stop)
        case("exploration ticket threshold disabled enters chapter", "ExplorationTask",
            {"PageChapter", "ExplorationEnterChapter", "ExplorationTicketCountEntrance"}, [
            ("ExplorationEnterChapter", {"PageChapterField", "ExplorationEnemyProbe"}),
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1)
        case("empty chapter reenters only once", "ExplorationTask", {"PageChapterField", "NavBack"}, [
            *[("ExplorationEnemySwipe", {"PageChapterField", "NavBack"}) for _ in range(6)],
            ("NativeToChapterNavBack", {"PageExitExplore", "NavConfirmExitExplore"}),
            ("NativeNavConfirmExitExplore", {"PageChapter", "ExplorationEnterChapter"}),
            ("ExplorationReenterClick", {"PageChapterField", "NavBack"}),
            *[("ExplorationEnemySwipe", {"PageChapterField", "NavBack"}) for _ in range(6)]], succeeds=False)
        case("raid nickname missing never clicks", "RealmRaidTask", {"PageRaid", "RealmRaidCard", "RaidNicknameMissing"}, succeeds=False)
        case("raid selected corner mark never clicks", "RealmRaidTask", {"PageRaid", "RealmRaidCard", "RaidCornerMarked"}, succeeds=False)
        case("raid selection changes before click", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, succeeds=False,
            probe_events=[("RealmRaidNickname", {"PageRaid"})])
        case("raid unknown page never finishes exhausted scan", "RealmRaidScan", set(), succeeds=False)
        # The raid loop polls by rate_limit instead of CommonBattleWait so the exit check stays fast.
        case("raid active battle polls without input or count", "RealmRaidBattle", {"PageBattle", "RealmRaidBattleExitIcon"},
             succeeds=False)
        case("raid loading waits before settlement", "RealmRaidBattle", {"PageLoading"}, [
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1,
            probe_events=[("PageLoading", {"DailyBattleResult"})])
        case("raid verifies selected stamp", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}),
            ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1)
        case("missing selected stamp fails", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}),
            ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid"})], succeeds=False)
        case("raid clearing the board counts the win after auto refresh", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidCard", "RealmRaidBreakRecordZero"}),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], requested_count=2, expected_count=2)
        case("raid zero record with marked slot is not a win", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidCard", "RaidCornerMarked", "RealmRaidBreakRecordZero"})], succeeds=False)
        case("raid shared loop counts two targets", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken", "RealmRaidCard"}),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], requested_count=2, expected_count=2)
        case("unknown popup after a raid win stops before the next card", "RealmRaidSuccess", {"PageRaid", "RealmRaidBroken"},
            requested_count=2, expected_count=1, succeeds=False, expect_popup_stop=True,
            probe_events=[("RealmRaidBroken", {"TouchAreaRisk"})])
        case("no raid tickets stops without count", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyNoResource"})])
        case("raid zero ticket counter finishes before choosing", "RealmRaidTask", {"PageRaid", "RealmRaidCard", "RealmRaidTicketEmpty"})
        case("raid tickets run out after a counted win", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken", "RealmRaidTicketEmpty"})], requested_count=2, expected_count=1)
        retreat = lambda n: {"RealmRaidRetreatBegin": {"max_hit": n}, "RealmRaidRetreatRetry": {"max_hit": n}}
        # The game shows the exit button only after the battle starts, never together with 准备.
        exit_ready = {"PageBattle", "RealmRaidBattleExitIcon"}
        prepare = {"DailyPrepare"}
        case("raid retreats twice on the same card then wins it", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", exit_ready),
            ("RealmRaidRetreatExit", {"RealmRaidRetreatConfirm"}),
            ("RealmRaidRetreatConfirm", {"DailyBattleFailed", "RealmRaidRetreatAgain"}),
            ("RealmRaidRetreatAgain", {"RealmRaidRetreatAgainConfirm"}),
            ("RealmRaidRetreatAgainConfirm", exit_ready),
            ("RealmRaidRetreatExit", {"RealmRaidRetreatConfirm"}),
            ("RealmRaidRetreatConfirm", {"DailyBattleFailed", "RealmRaidRetreatAgain"}),
            ("RealmRaidRetreatAgain", prepare),
            ("DailyPrepare", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1, pipeline_override=retreat(2))
        case("raid retreat disabled prepares normally", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", prepare),
            ("DailyPrepare", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1)
        case("raid retreat exits during auto-started battle", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"PageBattle", "RealmRaidBattleExitIcon"}),
            ("RealmRaidRetreatExit", {"DailyBattleFailed", "RealmRaidRetreatAgain"}),
            ("RealmRaidRetreatAgain", {"DailyPrepare"}),
            ("DailyPrepare", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1, pipeline_override=retreat(1))
        case("raid retreat without exit confirmation stops after three clicks", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", exit_ready),
            *[("RealmRaidRetreatExit", exit_ready) for _ in range(3)]], succeeds=False, pipeline_override=retreat(1))
        case("raid retreat stops when retry has no ticket", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", exit_ready),
            ("RealmRaidRetreatExit", {"RealmRaidRetreatConfirm"}),
            ("RealmRaidRetreatConfirm", {"DailyBattleFailed", "RealmRaidRetreatAgain"}),
            ("RealmRaidRetreatAgain", {"DailyNoResource"})], pipeline_override=retreat(4))
        case("raid real defeat after retreats is not retried", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", exit_ready),
            ("RealmRaidRetreatExit", {"RealmRaidRetreatConfirm"}),
            ("RealmRaidRetreatConfirm", {"DailyBattleFailed", "RealmRaidRetreatAgain"}),
            ("RealmRaidRetreatAgain", prepare),
            ("DailyPrepare", {"DailyBattleFailed", "DailyBattleResult", "RealmRaidRetreatAgain"}),
            ("RealmRaidBattleFailedResult", {"PageRaid"})], pipeline_override=retreat(1))
        case("all raid cards already marked", "RealmRaidTask", {"PageRaid"})
        case("raid failed battle skips attempted target", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleFailed", "DailyBattleResult"}),
            ("RealmRaidBattleFailedResult", {"PageRaid"})])
        battle_exit = {"PageBattle", "RealmRaidBattleExitIcon"}
        case("raid mark waits until the retreat budget is spent", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", battle_exit),
            ("RealmRaidRetreatExit", {"DailyBattleFailed", "RealmRaidRetreatAgain"}),
            ("RealmRaidRetreatAgain", battle_exit),
            ("RealmRaidBattleMark", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1,
            pipeline_override={**retreat(1), "RealmRaidBattleMark": {"enabled": True}})
        case("raid mark clicks once per card", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", battle_exit),
            ("RealmRaidBattleMark", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken", "RealmRaidCard"}),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", battle_exit),
            ("RealmRaidBattleMark", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], requested_count=2, expected_count=2,
            pipeline_override={"RealmRaidBattleMark": {"enabled": True}})
        defeat = [("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleFailed", "DailyBattleResult"})]
        case("raid defeat can end the round", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            *defeat, ("RealmRaidBattleFailedResult", {"PageRaid", "RealmRaidCard", "RealmRaidRefreshButton"})],
            pipeline_override={"RealmRaidFailStop": {"enabled": True}})
        refresh_mode = {"RealmRaidFailRefresh": {"enabled": True}, "RealmRaidFailRefreshUnavailable": {"enabled": True}}
        case("raid defeat refreshes then continues", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            *defeat, ("RealmRaidBattleFailedResult", {"PageRaid", "RealmRaidCard", "RealmRaidRefreshButton"}),
            ("RealmRaidRefresh", {"PageRaid", "RealmRaidCard", "RealmRaidRefreshConfirm"}),
            ("RealmRaidRefreshConfirm", {"PageRaid", "RealmRaidCard"}),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1, pipeline_override=refresh_mode)
        lost = {"DailyBattleFailed", "DailyBattleResult", "RealmRaidRetreatAgain"}
        fail_retry = lambda n: {"RealmRaidFailRetry": {"max_hit": n}}
        case("raid defeat retries the same card then wins", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", lost),
            ("RealmRaidRetreatAgain", {"RealmRaidRetreatAgainConfirm"}),
            ("RealmRaidRetreatAgainConfirm", {"DailyPrepare"}), ("DailyPrepare", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1, pipeline_override=fail_retry(1))
        case("raid defeat retry budget runs out then skips", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", lost),
            ("RealmRaidRetreatAgain", {"DailyPrepare"}), ("DailyPrepare", lost),
            ("RealmRaidBattleFailedResult", {"PageRaid"})], pipeline_override=fail_retry(1))
        case("raid defeat retry resets for the next card", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", lost),
            ("RealmRaidRetreatAgain", {"DailyPrepare"}), ("DailyPrepare", lost),
            ("RealmRaidBattleFailedResult", {"PageRaid", "RealmRaidCard"}),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", lost),
            ("RealmRaidRetreatAgain", {"DailyPrepare"}), ("DailyPrepare", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1, pipeline_override=fail_retry(1))
        case("raid retreat and defeat retry keep separate budgets", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", battle_exit),
            ("RealmRaidRetreatExit", lost),
            ("RealmRaidRetreatAgain", {"DailyPrepare"}), ("DailyPrepare", lost),
            ("RealmRaidRetreatAgain", {"DailyPrepare"}), ("DailyPrepare", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1,
            pipeline_override={**retreat(1), **fail_retry(1)})
        exhausted_refresh = {**refresh_mode, "RealmRaidExhaustedRefresh": {"enabled": True}}
        case("raid refresh mode refreshes a board with only failed cards", "RealmRaidTask", {"PageRaid", "RealmRaidRefreshButton"}, [
            ("RealmRaidRefresh", {"PageRaid", "RealmRaidRefreshConfirm"}),
            ("RealmRaidRefreshConfirm", {"PageRaid", "RealmRaidCard"}),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1, pipeline_override=exhausted_refresh)
        case("raid exhausted board in cooldown ends the round", "RealmRaidTask", {"PageRaid"}, pipeline_override=exhausted_refresh)
        case("raid default mode never refreshes an exhausted board", "RealmRaidTask", {"PageRaid", "RealmRaidRefreshButton"})
        case("raid refresh in cooldown ends the round", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            *defeat, ("RealmRaidBattleFailedResult", {"PageRaid", "RealmRaidCard"})], pipeline_override=refresh_mode)
        case("raid refresh without confirmation stops after three clicks", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            *defeat, ("RealmRaidBattleFailedResult", {"PageRaid", "RealmRaidRefreshButton"}),
            *[("RealmRaidRefresh", {"PageRaid", "RealmRaidRefreshButton"}) for _ in range(3)]],
            succeeds=False, pipeline_override=refresh_mode)
        case("empty mail/tasks/rewards terminates", "DailyRewardsTask", {"PageMail", "NavClose"}, [
            ("NativeNavClose", {"PageHome", "NavOpenQuests"}),
            ("NativeToQuestTasksNavOpenQuests", {"PageQuests", "NavQuestTasks"}),
            ("NativeToQuestTasksNavQuestTasks", {"PageQuests", "PageQuestTasks", "NavQuestRewards"}),
            ("NativeRewardsSwitchTab", {"PageQuests"}),
            *[("NativeRewardsScroll", {"PageQuests"}) for _ in range(5)]])
        case("reward loop claims twice and handles popup before next claim", "NativeRewardsClaimLoop", {"PageQuests", "DailyRewardsClaimReward"}, [
            ("NativeRewardsClaim", {"PageQuests", "DailyRewardPopup"}),
            ("DailyRewardPopup", {"PageQuests", "DailyRewardsClaimReward"}),
            ("NativeRewardsClaim", {"PageQuests"}),
            *[("NativeRewardsScroll", {"PageQuests"}) for _ in range(5)]])
        case("reward loop stops on an unknown popup after a claim", "NativeRewardsClaimLoop", {"PageQuests", "DailyRewardsClaimReward"}, [
            ("NativeRewardsClaim", {"TouchAreaRisk"})], succeeds=False, expect_popup_stop=True)
        # CommonActivity counter state lives in native anchors: a battle frame points the stage anchor at the counting node.
        ca_battle = [("PageBattle", {"CommonActivityBattleResult"})]
        case("CommonActivity merged loop counts two victories and stops", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"}),
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"})], requested_count=2, expected_count=2,
            probe_events=ca_battle * 2)
        case("CommonActivity single victory counts once", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"})], expected_count=1, probe_events=ca_battle)
        case("CommonActivity victory without continue text taps the safe area and counts", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityVictory", {"CommonActivityChallenge"})], expected_count=1,
            probe_events=[("PageBattle", {"CommonActivityVictory"})])
        case("CommonActivity result that returns straight to the stage still counts", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"})], expected_count=1,
            probe_events=[("PageBattle", {"CommonActivityChallenge"})])
        case("CommonActivity stage seen without a battle never counts", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenge"}),
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"})], expected_count=1, probe_events=ca_battle)
        case("CommonActivity challenge that never registers stops after three taps", "CommonActivityTask", {"CommonActivityChallenge"},
            [("CommonActivityChallenge", {"CommonActivityChallenge"})] * 3, succeeds=False)
        case("CommonActivity unknown popup after a battle stops before the next challenge", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"TouchAreaRisk"})], succeeds=False, expect_popup_stop=True, probe_events=ca_battle)
        case("CommonActivity defeat does not count", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityFailedBattleResult", {"CommonActivityChallenge"})], succeeds=False,
            probe_events=[("PageBattle", {"DailyBattleFailed", "CommonActivityBattleResult"})])
        case("CommonActivity resource handler finishes without input or count", "CommonActivityInsufficientAP",
             {"CommonActivityChallenge", "CommonInsufficientAP"})
        case("Mitama single victory counts once", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("Mitama defeat does not count", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"DailyBattleFailed", "MitamaBattleResult"}),
            ("MitamaFailedBattleResult", {"MitamaFinishOnce"})], succeeds=False)
        case("Mitama resource handler finishes without input or count", "MitamaInsufficientAP",
             {"MitamaChallenge", "CommonInsufficientAP"})
        case("CommonActivity unrecognized result screen is tapped through and still counts", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonActivityAfterBattleTap", {"UnknownResultScreen"}),
            ("CommonActivityAfterBattleTap", {"CommonActivityChallenge"})], expected_count=1,
            probe_events=[("PageBattle", {"UnknownResultScreen"})])
        case("CommonActivity fallback tap is bounded and never counts", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            *[("CommonActivityAfterBattleTap", {"UnknownResultScreen"})] * 5], succeeds=False,
            probe_events=[("PageBattle", {"UnknownResultScreen"})])
        case("CommonActivity never taps blindly before a battle", "CommonActivityTask", {"UnknownResultScreen"}, succeeds=False)
        case("CommonActivity closes a reward item detail opened by a stray tap", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonActivityChallenging"}),
            ("CommonRewardItemDetail", {"CommonActivityBattleResult"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"})], expected_count=1,
            probe_events=[("PageBattle", {"CommonRewardItemDetail", "CommonActivityBattleResult"})])
        case("CommonActivity confirms the soul mismatch prompt instead of stopping", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonSoulMismatch", "TouchAreaRisk"}),
            ("CommonSoulMismatch", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"})], expected_count=1, expect_popup_stop=False,
            probe_events=ca_battle)
        case("CommonActivity mutes the soul mismatch prompt once, then confirms", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"CommonSoulMismatchMute", "CommonSoulMismatch"}),
            ("CommonSoulMismatchMute", {"CommonSoulMismatch"}),
            ("CommonSoulMismatch", {"CommonActivityChallenging"}),
            ("CommonActivityBattleResult", {"CommonActivityChallenge"})], expected_count=1, probe_events=ca_battle)
        case("Mitama confirms the soul mismatch prompt before battle", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"CommonSoulMismatch", "TouchAreaRisk"}),
            ("CommonSoulMismatch", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1, expect_popup_stop=False)
        # A confirm/cancel popup no rule handles stops the task once the frequent candidates miss.
        for entry in ("CommonActivityTask", "DailyRewardsTask", "ReturnMain", "TeamLeaderFindTeammate"):
            case(entry + " stops on an unknown popup", entry, {"TouchAreaRisk"}, succeeds=False, expect_popup_stop=True)
        # Accepted trade-off: a visible button under a popup is tapped (max_hit bounds it) before the popup stops the task.
        popup_over_stage = {"TouchAreaRisk", "CommonActivityChallenge", "CommonConfirm"}
        case("purchase popup over the challenge stops after the challenge budget", "CommonActivityTask", {"CommonActivityChallenge"},
             [("CommonActivityChallenge", popup_over_stage)] * 3, succeeds=False, expect_popup_stop=True)
        case("confirmation alone never authorizes an update click", "StartupUpdateConfirm", {"StartupUpdateButton"}, succeeds=False)
        case("unknown startup screen is never blindly clicked", "StartupSkipAnimation", set(), succeeds=False)
        case("explicit update context allows its own button", "StartupUpdateConfirm", {"StartupUpdateText", "StartupUpdateButton"}, [
            ("StartupUpdateConfirm", set())])
        case("desktop launch reaches server and verified home", "StartupTask", set(), [
            ("StartupLaunch", {"startupMain", "StartupCheckServer", "StartupEnterGame"}),
            ("StartupEnterGame", {"StartupHome"})])
        stop_at_battle = {"MitamaPreparationDestination": {"next": []}}
        case("mitama confirms lock before preparation continues", "MitamaPreparation", {"PageMitamaStage", "MitamaUnlockTeam"}, [
            ("MitamaUnlockTeam", {"PageMitamaStage", "MitamaLockedTeam"})],
            expected_steps=["MitamaLockedTeam"], pipeline_override=stop_at_battle)
        case("mitama lock disabled never touches the lock icon", "MitamaPreparation", {"PageMitamaStage", "MitamaUnlockTeam"},
            expected_steps=[], pipeline_override={**stop_at_battle, "MitamaLockTeam": {"enabled": False}})
        case("mitama lock click that never locks stops", "MitamaPreparation", {"PageMitamaStage", "MitamaUnlockTeam"},
            [("MitamaUnlockTeam", {"PageMitamaStage", "MitamaUnlockTeam"})]*3, succeeds=False, pipeline_override=stop_at_battle)
        case("mitama missed challenge retries once", "MitamaBattleEntry", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaChallenge"}), ("MitamaChallenge", {"DailyBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("mitama challenge stops after three misses", "MitamaBattleEntry", {"MitamaChallenge"},
            [("MitamaChallenge", {"MitamaChallenge"})]*3, succeeds=False)
        case("mitama manual battle switches automatic", "MitamaBattle", {"PageBattle","CommonBattleEnableAuto"}, [
            ("CommonBattleEnableAuto", {"DailyBattleResult"}), ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("mitama settlement click retries collect only once", "MitamaBattle", {"DailyBattleResult"}, [
            ("MitamaBattleResult", {"DailyBattleResult"}), ("MitamaBattleResult", {"MitamaFinishOnce"})],
            expected_count=1, enable_drop_statistics=True, expected_statistics=1)
        case("awakening settlement click retries collect only once", "AwakeningBattle", {"DailyBattleResult"}, [
            ("AwakeningBattleResult", {"DailyBattleResult"}), ("AwakeningBattleResult", {"PageAwakeningStage","AwakeningLayerSelected"})],
            expected_count=1, enable_drop_statistics=True, expected_statistics=1)
        case("mitama failure never collects success rewards", "MitamaBattle", {"DailyBattleFailed","DailyBattleResult"}, [
            ("MitamaFailedBattleResult", {"PageMitamaStage"})], succeeds=False,
            enable_drop_statistics=True, expected_statistics=0)
        case("mitama disbanded room exits without counting", "TeamLeaderBattle", {"PageExplore"}, succeeds=False)
        # A frozen screen must exhaust exactly its budget and then stop: timeout never fires while nodes keep hitting.
        result = {"DailyBattleResult"}
        for title, entry, flags, event, budget, extra in [
            ("mitama layer never appears", "MitamaNavigateReady", {"PageMitamaStage"}, "MitamaSwipeBaqin", 8, None),
            ("mitama buff menu never opens", "MitamaEnableBuff", {"PageMitamaStage", "CommonBuffMenuIcon"}, "MitamaOpenBuffMenu", 4,
             {"MitamaEnableBuff": {"enabled": True}}),
            ("mitama settlement never closes", "MitamaBattleResult", {"MitamaBattleResult"}, "MitamaBattleResult", 5, None),
            ("team settlement never closes", "TeamLeaderBattleResult", result, "TeamLeaderBattleResult", 5, None),
            ("mitama prepare never leaves", "MitamaBattle", {"MitamaPrepare"}, "MitamaPrepare", 5, None),
            ("exploration settlement never closes", "ExplorationBattle", result, "ExplorationBattleResult", 5, None),
            ("raid settlement never closes", "RealmRaidBattleSettle", result, "RealmRaidBattleResult", 5, None),
            ("awakening settlement never closes", "AwakeningBattle", result, "AwakeningBattleResult", 5, None),
            ("awakening prepare never leaves", "AwakeningBattle", {"DailyPrepare"}, "DailyPrepare", 5, None),
            ("activity challenge never enters", "CommonActivityTask", {"CommonActivityChallenge"}, "CommonActivityChallenge", 3, None),
            ("activity prepare never leaves", "CommonActivityTask", {"CommonActivityPrepare"}, "CommonActivityPrepare", 5, None),
            ("activity settlement never closes", "CommonActivityBattleResult", {"CommonActivityBattleResult"}, "CommonActivityBattleResult", 5, None),
            ("reward popup never closes", "ExplorationBattle", {"DailyRewardPopup"}, "DailyRewardPopup", 150, None),
        ]:
            case("frozen screen stops: " + title, entry, flags, [(event, flags)] * budget, succeeds=False, pipeline_override=extra)
        one_wait = {"CommonBattleWait": {"max_hit": 3}}
        case("frozen battle page stops once its wait budget runs out", "MitamaBattle", {"PageBattle"}, succeeds=False, pipeline_override=one_wait)
        case("retry budgets return on the next battle", "MitamaBattleEntry", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaPrepare"}), ("MitamaPrepare", {"MitamaPrepare"}),
            ("MitamaPrepare", {"DailyBattleResult"}), ("MitamaBattleResult", {"DailyBattleResult"}),
            ("MitamaBattleResult", {"MitamaChallenge", "MitamaFinishOnce"})] +
            [("MitamaChallenge", {"MitamaPrepare"}), ("MitamaPrepare", {"MitamaPrepare"}),
             ("MitamaPrepare", {"MitamaPrepare"}), ("MitamaPrepare", {"MitamaPrepare"}), ("MitamaPrepare", {"MitamaPrepare"}),
             ("MitamaPrepare", {"DailyBattleResult"}), ("MitamaBattleResult", {"MitamaFinishOnce"})],
            requested_count=2, expected_count=2)
        invite = {"PageTeamInvite", "TeamLeaderInviteTabRecent", "TeamLeaderInviteTabGuild", "TeamLeaderInviteTabCross"}
        friends, recent = invite | {"TeamLeaderInviteFriendsSelected"}, invite | {"TeamLeaderInviteRecentSelected"}
        guild, cross = invite | {"TeamLeaderInviteGuildSelected"}, invite | {"TeamLeaderInviteCrossSelected"}
        # Cases entering at the invite search never pass TeamLeaderRoomReady, which names the first teammate as the round target.
        stop_at_fight = {"TeamLeaderSendInvite": {"next": []},
                         "TeamLeaderFindTeammate": {"anchor": {"TeamLeaderSelectTarget": "TeamLeaderSelectTeammate"}}}
        found = [("TeamLeaderSelectTeammate", {"TeamLeaderSendInvite"}), ("TeamLeaderSendInvite", set())]
        def teammate(flags):
            return flags | {"TeamLeaderTeammateName"}
        case("invite finds teammate on the friends tab without switching", "TeamLeaderFindTeammate", teammate(friends), found,
            pipeline_override=stop_at_fight)
        case("invite searches each tab in order and finds teammate on cross server", "TeamLeaderFindTeammate", friends,
            [("TeamLeaderInviteScroll", friends)] * 7
            + [("TeamLeaderInviteOpenRecent", recent)] + [("TeamLeaderInviteScroll", recent)] * 7
            + [("TeamLeaderInviteOpenGuild", guild)] + [("TeamLeaderInviteScroll", guild)] * 7
            + [("TeamLeaderInviteOpenCross", teammate(cross))] + found, pipeline_override=stop_at_fight)
        case("invite skips scrolling when the online list fits one page", "TeamLeaderFindTeammate", friends | {"TeamLeaderInviteListFits"}, [
            ("TeamLeaderInviteOpenRecent", recent | {"TeamLeaderInviteListFits"}),
            ("TeamLeaderInviteOpenGuild", teammate(guild))] + found, pipeline_override=stop_at_fight)
        case("invite leaves a tab as soon as the list reaches the bottom", "TeamLeaderFindTeammate", friends, [
            ("TeamLeaderInviteScroll", friends),
            ("TeamLeaderInviteScroll", friends | {"TeamLeaderInviteListAtBottom"}),
            ("TeamLeaderInviteOpenRecent", teammate(recent))] + found, pipeline_override=stop_at_fight)
        case("invite still checks the nickname on the bottom frame", "TeamLeaderFindTeammate", friends, [
            ("TeamLeaderInviteScroll", teammate(friends) | {"TeamLeaderInviteListAtBottom"})] + found, pipeline_override=stop_at_fight)
        case("invite falls through a missing guild tab", "TeamLeaderFindTeammate",
            (friends - {"TeamLeaderInviteTabGuild"}) | {"TeamLeaderInviteListFits"}, [
            ("TeamLeaderInviteOpenRecent", (recent - {"TeamLeaderInviteTabGuild"}) | {"TeamLeaderInviteListFits"}),
            ("TeamLeaderInviteOpenCross", teammate(cross - {"TeamLeaderInviteTabGuild"}))] + found, pipeline_override=stop_at_fight)
        case("invite fails without inviting once every tab is searched", "TeamLeaderFindTeammate", cross | {"TeamLeaderInviteListFits"}, succeeds=False,
             pipeline_override=stop_at_fight)
        case("invite stops when a tab never switches", "TeamLeaderFindTeammate", friends | {"TeamLeaderInviteListFits"},
            [("TeamLeaderInviteOpenRecent", friends | {"TeamLeaderInviteListFits"})] * 3, succeeds=False,
            pipeline_override=stop_at_fight)
        private = {"PageTeamCreate","TeamLeaderPrivacyText","TeamLeaderCreateButton","TeamLeaderPrivacySelected"}
        public = private - {"TeamLeaderPrivacySelected"}
        case("private room already selected skips toggling", "TeamLeaderVerifyPrivacy", private,
            [("TeamLeaderCreateConfirm", {"PageTeamRoom"})], stop_after_input="TeamLeaderCreateConfirm")
        case("private selection retries a missed toggle", "TeamLeaderVerifyPrivacy", public,
            [("TeamLeaderSetPrivate", public),("TeamLeaderSetPrivate",private),("TeamLeaderCreateConfirm",{"PageTeamRoom"})],
            stop_after_input="TeamLeaderCreateConfirm")
        case("private setting never confirmed stops after three clicks", "TeamLeaderVerifyPrivacy", public,
            [("TeamLeaderSetPrivate",public)]*3, succeeds=False)
        case("creation refuses an unverified private setting", "TeamLeaderCreateConfirm", public, succeeds=False)
        case("created room must be seen before invite panel opens", "TeamLeaderWaitRoom", {"PageTeamRoom","TeamLeaderOpenInvite"},
            [("TeamLeaderOpenInvite",set())], stop_after_input="TeamLeaderOpenInvite")
        stage = {"PageMitamaStage", "TeamPresetEntryText"}
        window = {"PageMitamaStage", "TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetFooter", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
        apply_steps = [
            ("TeamPresetSelectTeamConfigured", window | {"TeamPresetApplyIcon"}),
            ("TeamPresetApplyIcon", window | {"TeamPresetConfirm"}),
            ("TeamPresetConfirm", window | {"TeamPresetInUse"}),
        ]
        case("preset retries missed open and missed exit before completing", "MitamaPreset", stage, [
            ("TeamPresetOpen", stage), ("TeamPresetOpen", window), *apply_steps,
            ("TeamPresetOut", window | {"TeamPresetInUse"}),
            ("TeamPresetOut", {"PageMitamaStage"})], prepare_only=True, expected_steps=["MitamaPresetDone"])
        case("preset never skips unopened window after three misses", "MitamaPreset", stage,
             [("TeamPresetOpen", stage)] * 3, prepare_only=True, succeeds=False, expected_steps=[])
        case("preset never completes while exit window remains open", "MitamaPreset", stage, [
            ("TeamPresetOpen", window), *apply_steps,
            *[("TeamPresetOut", window | {"TeamPresetInUse"}) for _ in range(3)]],
             prepare_only=True, succeeds=False, expected_steps=[])
        # The configured group or team may sit below the first screen: swipe the list and look again.
        no_group = window - {"TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
        case("preset swipes the group list to find the configured group", "MitamaPreset", stage, [
            ("TeamPresetOpen", no_group), ("TeamPresetGroupScroll", no_group | {"TeamPresetSelectGroup"}),
            ("TeamPresetSelectGroup", window), *apply_steps,
            ("TeamPresetOut", {"PageMitamaStage"})], prepare_only=True, expected_steps=["MitamaPresetDone"])
        case("preset stops when the group list bottoms out without the group", "MitamaPreset", stage, [
            ("TeamPresetOpen", no_group), ("TeamPresetGroupScroll", no_group | {"TeamPresetGroupAtBottom"})],
             prepare_only=True, succeeds=False, expected_steps=[])
        no_team = window - {"TeamPresetSelectTeamConfigured"}
        case("preset swipes the team list to find the configured team", "MitamaPreset", stage, [
            ("TeamPresetOpen", no_team), ("TeamPresetTeamScroll", no_team), ("TeamPresetTeamScroll", window), *apply_steps,
            ("TeamPresetOut", {"PageMitamaStage"})], prepare_only=True, expected_steps=["MitamaPresetDone"])
        # Log 2026-11-13 00:21: the selected team has 4 shikigami, so the footer shows the 5-member hint instead of 出战.
        short_no_team = window - {"TeamPresetSelectTeamConfigured", "TeamPresetOut"}
        case("preset window with the 5-member hint still swipes to the configured team", "MitamaPreset", stage, [
            ("TeamPresetOpen", short_no_team), ("TeamPresetTeamScroll", short_no_team | {"TeamPresetSelectTeamConfigured"}),
            *apply_steps, ("TeamPresetOut", {"PageMitamaStage"})], prepare_only=True, expected_steps=["MitamaPresetDone"])
        # Solo mode needs 出战: a preset team under 5 shikigami has none, so it stops instead of passing.
        short = window - {"TeamPresetOut"}
        case("solo preset stops when the applied team cannot sortie", "MitamaPreset", stage, [
            ("TeamPresetOpen", short), ("TeamPresetSelectTeamConfigured", short | {"TeamPresetApplyIcon"}),
            ("TeamPresetApplyIcon", short | {"TeamPresetConfirm"}), ("TeamPresetConfirm", short | {"TeamPresetInUse"})],
             prepare_only=True, succeeds=False, expected_steps=[])
        case("preset stops when the team list bottoms out without the team", "MitamaPreset", stage, [
            ("TeamPresetOpen", no_team), ("TeamPresetTeamScroll", no_team | {"TeamPresetTeamAtBottom"})],
             prepare_only=True, succeeds=False, expected_steps=[])
        case("preset team swipes are bounded", "MitamaPreset", stage, [
            ("TeamPresetOpen", no_team), *[("TeamPresetTeamScroll", no_team)] * 6],
             prepare_only=True, succeeds=False, expected_steps=[])
        # Team mode: 全队正在使用 means done; close the window and never press 出战 (a short team has none anyway).
        team_preset = {"TeamPresetInUse": {"next": ["[JumpBack]CommonBountyInterrupt", "TeamPresetClose"]},
                       "TeamPresetCloseRetry": {"next": ["[JumpBack]CommonBountyInterrupt", "TeamPresetWindowClosed", "TeamPresetClose"]}}
        short = window - {"TeamPresetOut"}
        case("team mode applies preset mitama and closes without sortie", "MitamaPreset", stage, [
            ("TeamPresetOpen", short), ("TeamPresetSelectTeamConfigured", short | {"TeamPresetApplyIcon"}),
            ("TeamPresetApplyIcon", short | {"TeamPresetConfirm"}), ("TeamPresetConfirm", short | {"TeamPresetInUse"}),
            ("TeamPresetClose", short | {"TeamPresetInUse"}), ("TeamPresetClose", {"PageMitamaStage"})],
            prepare_only=True, expected_steps=["MitamaPresetDone"], pipeline_override=team_preset)
        case("team mode never falls back to sortie when the window stays open", "MitamaPreset", stage, [
            ("TeamPresetOpen", window), *apply_steps, *[("TeamPresetClose", window | {"TeamPresetInUse"})] * 3],
             prepare_only=True, succeeds=False, expected_steps=[], pipeline_override=team_preset)
        case("unknown popup after opening preset prevents dependent steps", "MitamaPreset", stage, [
            ("TeamPresetOpen", {"TouchAreaRisk"})],
             prepare_only=True, succeeds=False, expected_steps=[], expect_popup_stop=True)
        case("locked team: prepare seen during the automatic transition is tapped once", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaPrepare"}),
            ("MitamaPrepare", {"MitamaChallenging"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1,
            probe_events=[("PageBattle", {"MitamaBattleResult"})])
        case("locked team skips prepare screen entirely", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("mitama merged turnaround counts two victories and stops", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce", "MitamaChallenge"}),
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce", "MitamaChallenge"})],
            requested_count=2, expected_count=2)
        case("mitama turnaround stops on an unknown popup before the next challenge", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], requested_count=2, expected_count=1, succeeds=False,
            expect_popup_stop=True, probe_events=[("MitamaFinishOnce", {"TouchAreaRisk"})])
        case("unlocked team still clicks its prepare button", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaPrepare"}),
            ("MitamaPrepare", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("team battle also tolerates automatic preparation", "TeamLeaderBattle", {"DailyPrepare"}, [
            ("DailyPrepare", {"MitamaChallenging"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1,
            probe_events=[("PageBattle", {"DailyBattleResult"})])
        case("first daily treasure closes before continuing settlement", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"MitamaFinishOnce"})], expected_count=1)
        case("missed treasure dismissal retries before settlement", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("treasure dismissal stops after three misses", "MitamaTreasurePopup", {"MitamaTreasurePopup"},
            [("MitamaTreasurePopup", {"MitamaTreasurePopup"})] * 3, succeeds=False)
        case("team treasure closes before returning to the room", "TeamLeaderBattle", {"DailyBattleResult"}, [
            ("TeamLeaderBattleResult", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"PageTeamRoom"})], expected_count=1)
        for prefix in ("CommonActivity",):
            case(prefix + " tolerates automatic preparation", prefix + "Task", {prefix + "Challenge"}, [
                (prefix + "Challenge", {prefix + "Prepare"}),
                (prefix + "Prepare", {prefix + "Challenging"}),
                (prefix + "BattleResult", {prefix + "Challenge"})], expected_count=1,
                probe_events=[("PageBattle", {prefix + "BattleResult"})])
        case("exploration prepare tapped during the automatic transition", "ExplorationTask", {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyPrepare"}),
            ("DailyPrepare", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1)
        case("exploration switches gameplay tab back to mainline", "ExplorationEnsureMainline", {"PageExplore", "ExplorationOpenMainline"}, [
            ("ExplorationOpenMainline", {"PageExplore", "ExplorationMainlineReady", "ExplorationSelectChapter"}),
            ("ExplorationSelectChapter", {"PageChapter", "ExplorationEnterChapter"}),
            ("ExplorationEnterChapter", {"PageChapterField", "ExplorationEnemyProbe"}),
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1)
        case("missing mainline panel never causes blind chapter swipes", "ExplorationEnsureMainline", {"PageExplore"}, succeeds=False)
        case("raid selection miss retries the same card", "RealmRaidTask", {"PageRaid", "RealmRaidCard"}, [
            ("RealmRaidSelect", {"PageRaid", "RealmRaidCard"}),
            ("RealmRaidSelect", {"RealmRaidChallenge"}),
            ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1)
        case("raid selection stops after three misses", "RealmRaidTask", {"PageRaid", "RealmRaidCard"},
            [("RealmRaidSelect", {"PageRaid", "RealmRaidCard"})] * 3, succeeds=False)
        case("raid reward popup is dismissed before stamp verification", "RealmRaidVerify", {"PageRaid", "DailyRewardPopup"}, [
            ("DailyRewardPopup", {"PageRaid", "RealmRaidBroken"})], expected_count=1)
        case("reward tab retries a missed click", "NativeRewardsSwitchTab", {"PageQuests", "PageQuestTasks", "NavQuestRewards"}, [
            ("NativeRewardsSwitchTab", {"PageQuests", "PageQuestTasks", "NavQuestRewards"}),
            ("NativeRewardsSwitchTab", {"PageQuests"}),
            *[("NativeRewardsScroll", {"PageQuests"}) for _ in range(5)]])
        case("reward tab never succeeds while still on tasks", "NativeRewardsSwitchTab", {"PageQuests", "PageQuestTasks", "NavQuestRewards"},
            [("NativeRewardsSwitchTab", {"PageQuests", "PageQuestTasks", "NavQuestRewards"})] * 3, succeeds=False)
        case("navigation also verifies reward tab transition", "NavigateQuests", {"PageQuests", "PageQuestTasks", "NavQuestRewards"}, [
            ("NativeQuestRewardsSwitch", {"PageQuests"})])
        case("mail-specific attachment confirmation", "DailyMailClaimConfirm", {"DailyMailClaimConfirm"}, [
            ("DailyMailClaimConfirm", {"PageMail"})])
        case("mail illustration prompt is cancelled", "DailyMailIllustrationCancel", {"DailyMailIllustrationCancel"}, [
            ("DailyMailIllustrationCancel", {"PageMail"})])
        room = {"PageTeamRoom", "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2", "TeamLeaderStartButton"}
        case("leader retries missed start and handles team start confirmation", "TeamLeaderFight", room, [
            ("TeamLeaderStart", room),
            ("TeamLeaderStart", {"PageTeamRoom", "TeamLeaderStartConfirm"}),
            ("TeamLeaderStartConfirm", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        popup = {"PageTeamRoom", "TeamLeaderReinvitePrompt", "TeamLeaderReinviteConfirm", "CommonConfirm"}
        waiting = [("PageTeamRoom", {"PageTeamRoom"})] * 3 + [("PageTeamRoom", room)]
        case("leader waits in the room until the teammate returns, then challenges", "TeamLeaderFight", {"PageTeamRoom"}, [
            ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1, probe_events=waiting,
            pipeline_override={"TeamLeaderFight": {"timeout": 5000}})
        case("defeat stops the task and never starts another battle", "TeamLeaderFight", room, [
            ("TeamLeaderStart", {"DailyBattleFailed", "DailyBattleResult"}),
            ("TeamLeaderBattleFailedResult", room)], succeeds=False)
        case("leader confirms reinvite, never cancels it, and starts the next battle", "TeamLeaderFight", room, [
            ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", popup),
            ("TeamLeaderReinvite", {"PageTeamRoom"} | {"TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2", "TeamLeaderStartButton"}),
            ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], requested_count=2, expected_count=2)
        stale_options = {"MitamaCountDetermine": {"action": {"param": {"custom_action_param": {"continue_node": "TeamLeaderBattleEntry"}}}}}
        case("win loop survives task options cached from before the fix", "TeamLeaderFight", room, [
            ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", room),
            ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], requested_count=2, expected_count=2, pipeline_override=stale_options)
        case("reinvite popup arriving after the count is still confirmed", "TeamLeaderComplete", popup, [
            ("TeamLeaderReinvite", {"PageTeamRoom"})], expected_count=1)
        # With default invite the room replaces the settlement with no popup.
        case("team settlement returning straight to the room counts the win", "TeamLeaderBattle", {"DailyBattleResult"}, [
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        case("team two page settlement clicks each page once", "TeamLeaderBattle", {"DailyBattleResult"}, [
            ("TeamLeaderBattleResult", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        ticked = {"PageTeamRoom", "TeamLeaderReinvitePrompt", "TeamLeaderReinviteConfirm", "TeamLeaderReinviteLabel"}
        case("leader ticks default invite before confirming", "TeamLeaderComplete", ticked | {"TeamLeaderReinviteUnchecked"}, [
            ("TeamLeaderReinviteTick", ticked), ("TeamLeaderReinvite", {"PageTeamRoom"})], expected_count=1)
        case("already ticked default invite is not clicked again", "TeamLeaderComplete", ticked, [
            ("TeamLeaderReinvite", {"PageTeamRoom"})], expected_count=1)
        case("default invite that never ticks still confirms", "TeamLeaderComplete", ticked | {"TeamLeaderReinviteUnchecked"},
            [("TeamLeaderReinviteTick", ticked | {"TeamLeaderReinviteUnchecked"})] * 3 + [("TeamLeaderReinvite", {"PageTeamRoom"})], expected_count=1)
        buff_on = {"MitamaDisableBuff": {"enabled": True}}
        explore = {"PageExplore"}
        anchored = {"TeamLeaderFinish": {"next": ["[JumpBack]CommonBountyInterrupt", "[Anchor]TeamLeaderFinishTarget"]},
                    "TeamLeaderBattleNoResource": {"next": ["[JumpBack]CommonBountyInterrupt", "[Anchor]TeamLeaderNoResourceTarget"]},
                    "TeamLeaderOpen": {"next": ["TeamLeaderFight"]}}
        for scope in ("Mitama", "Awakening"):
            case(scope + " team run ends through its own finish", scope + "TeamEntry", {"TeamLeaderOpen"}, [
                ("TeamLeaderOpen", room),
                ("TeamLeaderStart", {"DailyBattleResult"}),
                ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1, pipeline_override=anchored)
        case("Awakening team run stops at its no resource handler", "AwakeningTeamEntry", {"TeamLeaderOpen"}, [
            ("TeamLeaderOpen", room),
            ("TeamLeaderStart", {"DailyNoResource"})], succeeds=False, pipeline_override=anchored)
        case("Mitama team run finishes normally when resources run out", "MitamaTeamEntry", {"TeamLeaderOpen"}, [
            ("TeamLeaderOpen", room),
            ("TeamLeaderStart", {"DailyNoResource"})], pipeline_override=anchored)
        awakening_team = {"AwakeningBattleModeDestination": {"next": ["[JumpBack]CommonBountyInterrupt", "AwakeningTeamEntry"]},
                          "TeamLeaderOpen": {"next": []}}
        case("awakening team option opens the shared team page", "AwakeningBattleModeDestination", {"TeamLeaderOpen"},
             [("TeamLeaderOpen", set())], pipeline_override=awakening_team)
        case("awakening without the team option still challenges solo", "AwakeningBattleModeDestination", {"AwakeningLayerSelected", "AwakeningChallengeButton"},
             [("AwakeningChallenge", set())], stop_after_input="AwakeningChallenge")
        case("awakening buff closes from explore after leaving the room", "AwakeningDisableBuff", {"PageTeamRoom", "NavBack"}, [
            ("NativeNavBack", {"PageExitTeam", "NavConfirmExitTeam"}),
            ("NativeNavConfirmExitTeam", {"PageExplore", "CommonBuffMenuIcon"}),
            ("AwakeningOpenBuffMenu", {"PageExplore", "AwakeningBuffPanel", "AwakeningBuffOn"}),
            ("AwakeningDisableBuffClick", {"PageExplore", "AwakeningBuffPanel", "AwakeningBuffOff"}),
            ("AwakeningCloseBuffAfterDisable", {"PageExplore"})], awakening_buff=True)
        case("leader closes the buff from explore without entering mitama", "MitamaDisableBuff", {"PageTeamRoom", "NavBack"}, [
            ("NativeNavBack", {"PageExitTeam", "NavConfirmExitTeam"}),
            ("NativeNavConfirmExitTeam", explore | {"CommonBuffMenuIcon"}),
            ("MitamaOpenBuffMenu", explore | {"MitamaBuffPanel", "MitamaCheckBuffStatusOpen"}),
            ("MitamaDisableBuffClick", explore | {"MitamaBuffPanel", "MitamaCheckBuffStatusClose"}),
            ("MitamaFinishCloseBuffPanel", explore)], pipeline_override=buff_on)
        case("solo finish still closes the buff on the stage page", "MitamaDisableBuff", {"PageMitamaStage", "CommonBuffMenuIcon"}, [
            ("MitamaOpenBuffMenu", {"PageMitamaStage", "MitamaBuffPanel", "MitamaCheckBuffStatusOpen"}),
            ("MitamaDisableBuffClick", {"PageMitamaStage", "MitamaBuffPanel", "MitamaCheckBuffStatusClose"}),
            ("MitamaFinishCloseBuffPanel", {"PageMitamaStage"})], pipeline_override=buff_on)
        pick = {"PageTeamInvite", "TeamLeaderTeammateName", "TeamLeaderInviteFriendsSelected", "TeamLeaderInviteTabFriends"}
        both = {"PageTeamRoom", "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2", "TeamLeaderStartButton"}
        anchored_first = {"TeamLeaderInviteSearch": {"anchor": {"TeamLeaderSelectTarget": "TeamLeaderSelectTeammate"}}}
        # Screenshot 2026-11-13: 寮友 tab selected, 在线：0, empty list -> no swipe, straight to 跨区.
        case("empty guild list switches to cross without swiping", "TeamLeaderInviteSearch", guild | {"TeamLeaderInviteListFits"}, [
            ("TeamLeaderInviteOpenCross", cross | {"TeamLeaderTeammateName"}), ("TeamLeaderSelectTeammate", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1, pipeline_override=anchored_first)
        # Log 2026-11-13 03:32: the teammate joined mid-search and the game closed the invite panel by itself.
        case("teammate joins mid-search and the game closes the panel", "TeamLeaderInviteSearch", guild | {"TeamLeaderInviteListFits"}, [
            ("TeamLeaderInviteOpenCross", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1, pipeline_override=anchored_first)
        # Only the second teammate's name is on screen in round two, so picking the first one would stall.
        pick2 = (pick - {"TeamLeaderTeammateName"}) | {"TeamLeaderTeammate2Name"}
        first_only = {"PageTeamRoom", "TeamLeaderOpenInvite", "TeamLeaderRoomTeammate"}
        case("leader invites two teammates in separate rounds and waits for both", "TeamLeaderRoomReady", {"PageTeamRoom", "TeamLeaderOpenInvite"}, [
            ("TeamLeaderOpenInvite", pick), ("TeamLeaderSelectTeammate", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", first_only),
            ("TeamLeaderOpenInvite", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        # An empty second nickname matches any text, so the room always "has" the second teammate.
        case("one teammate gets a single invite round", "TeamLeaderRoomReady", {"PageTeamRoom", "TeamLeaderOpenInvite"}, [
            ("TeamLeaderOpenInvite", pick), ("TeamLeaderSelectTeammate", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both),
            ("TeamLeaderStart", {"DailyBattleResult"}), ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        case("second invite round starts again from the friends tab", "TeamLeaderInviteAgain", first_only, [
            ("TeamLeaderOpenInvite", {"PageTeamInvite", "TeamLeaderInviteCrossSelected", "TeamLeaderInviteTabFriends"}),
            ("TeamLeaderInviteOpenFriends", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        # About 15 seconds without the second teammate: only the missing one is invited again.
        missing2 = {"PageTeamRoom", "TeamLeaderOpenInvite", "TeamLeaderRoomTeammate", "TeamLeaderStartButton"}
        case("missing second teammate is invited again after the wait", "TeamLeaderFight", missing2, [
            ("TeamLeaderOpenInvite", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        # The first teammate is invited first; the second round then follows as usual.
        nobody = {"PageTeamRoom", "TeamLeaderOpenInvite", "TeamLeaderStartButton"}
        case("missing teammates are both invited again, first one first", "TeamLeaderFight", nobody, [
            ("TeamLeaderOpenInvite", pick), ("TeamLeaderSelectTeammate", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", nobody | {"TeamLeaderRoomTeammate"}),
            ("TeamLeaderOpenInvite", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        # The right slot is taken: the invite opens from the middle slot.
        case("reinvite opens the panel from the middle slot when the right one is taken", "TeamLeaderFight",
             missing2 | {"TeamLeaderInviteSlotRight", "TeamLeaderOpenInviteMiddle"}, [
            ("TeamLeaderOpenInviteMiddle", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        case("leader stops after three invite rounds nobody answers", "TeamLeaderFight", missing2,
             [("TeamLeaderOpenInvite", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
              ("TeamLeaderSendInvite", missing2)] * 3, succeeds=False)
        case("leader never starts before the second teammate arrives", "TeamLeaderFight",
             {"PageTeamRoom", "TeamLeaderRoomTeammate", "TeamLeaderStartButton"}, succeeds=False)
        # A teammate may join after being judged missing: the room fills up, or they drop out of the invite lists.
        case("teammate who joined meanwhile fills the room: no invite panel, start", "TeamLeaderInviteSlot",
             both | {"TeamLeaderInviteSlotRight", "TeamLeaderInviteSlotMiddle", "TeamLeaderOpenInvite", "TeamLeaderOpenInviteMiddle"}, [
            ("TeamLeaderStart", {"DailyBattleResult"}), ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        # A teammate already in the room is still listed: the invite goes out but the panel may stay open.
        case("panel left open after inviting a teammate already in the room is cancelled", "TeamLeaderFight", missing2, [
            ("TeamLeaderOpenInvite", pick2), ("TeamLeaderSelectTeammate2", {"TeamLeaderSendInvite"}),
            ("TeamLeaderSendInvite", {"PageTeamInvite", "NavCancelInvite"}), ("TeamLeaderInviteTeammateMissing", both),
            ("TeamLeaderStart", {"DailyBattleResult"}), ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1)
        searched = {"PageTeamInvite", "TeamLeaderInviteCrossSelected", "TeamLeaderInviteListFits", "NavCancelInvite"}
        case("teammate who joined meanwhile is not in the lists: close the panel and start", "TeamLeaderFindTeammate", searched, [
            ("TeamLeaderInviteTeammateMissing", both), ("TeamLeaderStart", {"DailyBattleResult"}),
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1,
            pipeline_override={"TeamLeaderFindTeammate": {"anchor": {"TeamLeaderSelectTarget": "TeamLeaderSelectTeammate"}}})
        resume = {"PageTeamRoom", "MitamaRoomTargetDungeon", "MitamaRoomTargetLayer", "TeamLeaderRoomTeammate",
                  "TeamLeaderRoomTeammate2", "TeamLeaderStartButton"}
        team_mode = {"MitamaTeamResume": {"enabled": True},
                     "MitamaBattleModeDestination": {"next": ["[JumpBack]CommonBountyInterrupt", "MitamaTeamEntry"]}}
        case("mitama task already in the right room keeps it and starts", "MitamaNavigate", resume, [
            ("TeamLeaderStart", {"DailyBattleResult"}), ("TeamLeaderBattleResult", {"PageTeamRoom"})],
            expected_count=1, pipeline_override=team_mode)
        # Without a match the task falls back to navigation; resuming would have clicked start, which no case allows.
        for missing in ("MitamaRoomTargetLayer", "MitamaRoomTargetDungeon", "TeamLeaderRoomTeammate2"):
            case("mitama room without " + missing + " is not resumed", "MitamaNavigate", resume - {missing},
                 succeeds=False, pipeline_override=team_mode)
        case("solo mitama never checks the room", "MitamaNavigate", resume, succeeds=False)
        buff = {"MitamaEnableBuff": {"enabled": True}}
        case("resumed room still turns the buff on from the room", "MitamaNavigate", resume | {"CommonBuffMenuIcon"}, [
            ("MitamaOpenBuffMenu", resume | {"MitamaBuffPanel", "MitamaCheckBuffStatusClose"}),
            ("MitamaEnableBuffClick", resume | {"MitamaBuffPanel", "MitamaCheckBuffStatusOpen"}),
            ("MitamaCloseBuffPanel", resume),
            ("TeamLeaderStart", {"DailyBattleResult"}), ("TeamLeaderBattleResult", {"PageTeamRoom"})],
            expected_count=1, pipeline_override={**team_mode, **buff})
        awakening_resume = {"PageTeamRoom", "AwakeningRoomTargetDungeon", "AwakeningRoomType", "AwakeningRoomLayer",
                            "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2", "TeamLeaderStartButton"}
        awakening_mode = {"AwakeningTeamResume": {"enabled": True},
                          "AwakeningBattleModeDestination": {"next": ["[JumpBack]CommonBountyInterrupt", "AwakeningTeamEntry"]}}
        case("awakening task already in the right room keeps it and starts", "AwakeningNavigate", awakening_resume, [
            ("TeamLeaderStart", {"DailyBattleResult"}), ("TeamLeaderBattleResult", {"PageTeamRoom"})],
            expected_count=1, pipeline_override=awakening_mode)
        case("awakening room for another layer is not resumed", "AwakeningNavigate", awakening_resume - {"AwakeningRoomLayer"},
             succeeds=False, pipeline_override=awakening_mode)
        case("matching nickname alone does not authorize team start", "TeamLeaderFight", {"PageTeamRoom", "TeamLeaderRoomTeammate", "TeamLeaderRoomTeammate2"}, succeeds=False)
        case("startup announcement closes before server entry", "StartupTask", set(), [
            ("StartupLaunch", {"StartupCloseAnnouncement"}),
            ("StartupCloseAnnouncement", {"startupMain", "StartupCheckServer", "StartupEnterGame"}),
            ("StartupEnterGame", {"StartupHome"})])
        case("optional illustration download is cancelled during startup", "StartupTask", set(), [
            ("StartupLaunch", {"StartupCancelIllustration"}),
            ("StartupCancelIllustration", {"startupMain", "StartupCheckServer", "StartupEnterGame"}),
            ("StartupEnterGame", {"StartupHome"})])
        case("collapsed chapter panel expands before chapter search", "ExplorationEnsureMainline", {"PageExplore", "ExplorationExpandChapterPanel"}, [
            ("ExplorationExpandChapterPanel", {"PageExplore", "ExplorationMainlineReady", "ExplorationSelectChapter"}),
            ("ExplorationSelectChapter", {"PageChapter", "ExplorationEnterChapter"}),
            ("ExplorationEnterChapter", {"PageChapterField", "ExplorationEnemyProbe"}),
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1)
        case("chapter chest is collected before final battle count", "ExplorationTask", {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyBattleResult"}),
            ("ExplorationBattleResult", {"PageChapterField", "ExplorationCollectChest"}),
            ("ExplorationCollectChest", {"PageChapterField", "DailyRewardPopup"}),
            ("DailyRewardPopup", {"PageChapterField"})], expected_count=1)
        case("solo statistics execute once before settlement input", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1,
            enable_drop_statistics=True, expected_statistics=1)
        case("team statistics execute once before settlement input", "TeamLeaderBattle", {"DailyBattleResult"}, [
            ("TeamLeaderBattleResult", {"PageTeamRoom"})], expected_count=1,
            enable_drop_statistics=True, expected_statistics=1)
        case("defeat does not report successful drops", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"DailyBattleFailed", "MitamaBattleResult"}),
            ("MitamaFailedBattleResult", {"MitamaFinishOnce"})], succeeds=False,
            enable_drop_statistics=True)
        case("navigation closes a verified announcement before accepting home", "NavigateHome", {"PageHome", "StartupCloseAnnouncement"}, [
            ("StartupCloseAnnouncement", {"PageHome"})])
        awake_stage = {"PageAwakeningStage", "AwakeningTargetType", "AwakeningLayerSelected", "AwakeningLockedTeam", "AwakeningChallengeButton"}
        case("awakening already at target needs only challenge and settlement", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1, expected_steps=["AwakeningLockedTeam"])
        entering = {'PageBattleEntering', 'DailyPrepare', 'MitamaPrepare'}
        case('awakening deployment waits without clicking the dim preparation drum', 'AwakeningWaitBattleStart', entering,
            [('AwakeningBattleResult', awake_stage)], expected_count=1,
            probe_events=[('PageBattleEntering', entering), ('PageBattleEntering', entering),
                ('PageBattleEntering', {'PageBattle'}), ('PageBattle', {'DailyBattleResult'})])
        case('awakening cutscene then deployment then battle never retries challenge', 'AwakeningWaitBattleStart', {'PageLoading'},
            [('AwakeningBattleResult', awake_stage)], expected_count=1,
            probe_events=[('PageLoading', entering), ('PageBattleEntering', entering),
                ('PageBattleEntering', {'PageBattle'}), ('PageBattle', {'DailyBattleResult'})])
        case('persistent deployment times out without preparation clicks or count', 'CommonBattleEnteringWait', entering,
            succeeds=False)
        case('deployment blocks navigation without clicking back', 'NavigateHome', entering, succeeds=False)
        bond = {"PageBondling", "BondlingSearchButton", "BondlingSearchLockedTeam"}
        case("bondling already on explore page searches and counts once", "BondlingSearchTask", bond, [
            ("BondlingSearchStart", {"DailyBattleResult"}),
            ("BondlingSearchBattleResult", bond)], expected_count=1)
        case("bondling routes from courtyard through the explore bottom bar", "BondlingSearchTask", {"PageHome", "NavOpenExplore"}, [
            ("NativeToBondlingNavOpenExplore", {"PageExplore", "NavBondlingEntry"}),
            ("NativeToBondlingNavOpenBondling", bond),
            ("BondlingSearchStart", {"DailyBattleResult"}),
            ("BondlingSearchBattleResult", bond)], expected_count=1)
        case("bondling locks an unlocked team before searching", "BondlingSearchTask",
             {"PageBondling", "BondlingSearchButton", "BondlingSearchUnlockTeam"}, [
            ("BondlingSearchUnlockTeam", bond),
            ("BondlingSearchStart", {"DailyBattleResult"}),
            ("BondlingSearchBattleResult", bond)], expected_count=1)
        case("bondling search that never starts a battle ends as five bondlings found", "BondlingSearchTask", bond,
             [("BondlingSearchStart", bond)] * 3, expected_count=0)
        case("bondling gather-later prompt closes before the next search", "BondlingSearchTask", bond, [
            ("BondlingSearchStart", {"DailyBattleResult"}),
            ("BondlingSearchBattleResult", bond | {"BondlingSearchGatherLater"}),
            ("BondlingSearchGatherLater", bond),
            ("BondlingSearchStart", {"PageBattle", "CommonBattleEnableAuto"}),
            ("CommonBattleEnableAuto", {"DailyBattleResult"}),
            ("BondlingSearchBattleResult", bond)], requested_count=2, expected_count=2)
        case("bondling defeat stops without counting", "BondlingSearchTask", bond, [
            ("BondlingSearchStart", {"DailyBattleFailed", "DailyBattleResult"}),
            ("BondlingSearchFailedBattleResult", bond)], succeeds=False)
        case("bondling stops on stamina prompt without searching", "BondlingSearchBattleEntry", bond | {"DailyNoResource"}, succeeds=False)
        case("awakening routes from courtyard without forcing a return between battles", "AwakeningTask", {"PageHome", "NavOpenExplore"}, [
            ("NativeToAwakeningNavOpenExplore", {"PageExplore", "NavOpenAwakening"}),
            ("NativeToAwakeningNavOpenAwakening", {"PageAwakeningMenu"}),
            ("AwakeningOpenType", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening switches wrong element before selecting floor", "AwakeningTask", {"PageAwakeningStage"}, [
            ("AwakeningSwitchType", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening searches and verifies selected floor", "AwakeningTask", {"PageAwakeningStage", "AwakeningTargetType"}, [
            ("AwakeningLayerScrollDown", {"PageAwakeningStage", "AwakeningTargetType", "AwakeningLayerText"}),
            ("AwakeningSelectLayer", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening missed challenge retries without extra success count", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening missed challenge stops after three clicks", "AwakeningTask", awake_stage,
            [("AwakeningChallenge", awake_stage)] * 3, succeeds=False)
        case("awakening prepare tapped during the automatic transition", "AwakeningBattle", {"DailyPrepare"}, [
            ("DailyPrepare", {"PageBattle"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1,
            probe_events=[("PageBattle", {"DailyBattleResult"})])
        case("awakening handles manual preparation", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"DailyPrepare"}),
            ("DailyPrepare", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening switches manual combat to automatic", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"PageBattle", "CommonBattleEnableAuto"}),
            ("CommonBattleEnableAuto", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening treasure closes before counting", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", awake_stage)], expected_count=1)
        case("awakening refuses an invite hiding the settlement", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"CommonRefuseBounty"}),
            ("CommonRefuseBounty", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening defeat never counts as a completed victory", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"DailyBattleFailed", "DailyBattleResult"}),
            ("AwakeningFailedBattleResult", awake_stage)], succeeds=False)
        case("awakening insufficient stamina stops before challenge", "AwakeningTask", awake_stage | {"DailyNoResource"}, succeeds=False)
        case("awakening cannot count an idle stage as success", "AwakeningBattle", awake_stage, succeeds=False)
        case("awakening challenge retry budget resets for the second battle", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", awake_stage), ("AwakeningChallenge", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}), ("AwakeningBattleResult", awake_stage),
            ("AwakeningChallenge", awake_stage), ("AwakeningChallenge", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}), ("AwakeningBattleResult", awake_stage)],
            requested_count=2, expected_count=2)
        awake_unlocked = (awake_stage - {"AwakeningLockedTeam"}) | {"AwakeningUnlockTeam"}
        case("awakening confirms lock before entering battle", "AwakeningTask", awake_unlocked, [
            ("AwakeningUnlockTeam", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1, expected_steps=["AwakeningLockedTeam"])
        stage_buff_button = awake_stage | {"AwakeningOpenBuffMenu"}
        awake_buff_off = awake_stage | {"AwakeningBuffPanel", "AwakeningBuffOff"}
        awake_buff_on = awake_stage | {"AwakeningBuffPanel", "AwakeningBuffOn"}
        case("awakening buff opens before battle and closes after target count", "AwakeningTask", stage_buff_button, [
            ("AwakeningOpenBuffMenu", awake_buff_off),
            ("AwakeningEnableBuffClick", awake_buff_on),
            ("AwakeningCloseBuffAfterEnable", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", stage_buff_button),
            ("AwakeningOpenBuffMenu", awake_buff_on),
            ("AwakeningDisableBuffClick", awake_buff_off),
            ("AwakeningCloseBuffAfterDisable", awake_stage)], expected_count=1, awakening_buff=True,
            expected_steps=["AwakeningLockedTeam", "AwakeningBuffEnabled", "AwakeningBuffDisabled"])
        def preset_events(stage):
            window = stage | {"TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetFooter", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
            return [
                ("TeamPresetOpen", window),
                ("TeamPresetSelectTeamConfigured", window | {"TeamPresetApplyIcon"}),
                ("TeamPresetApplyIcon", window | {"TeamPresetConfirm"}),
                ("TeamPresetConfirm", window | {"TeamPresetInUse"}),
                ("TeamPresetOut", stage),
            ]
        awake_preset_stage = awake_stage | {"TeamPresetEntryText"}
        for prefix in ("Awakening",):
            stage = awake_preset_stage
            result = "DailyBattleResult"
            returned = awake_preset_stage
            steps_done = ["AwakeningLockedTeam", prefix + "PresetDone"]
            case(prefix + " completes preset before challenge", prefix + "Task", stage, [
                *preset_events(stage),
                (prefix + "Challenge", {result}),
                (prefix + "BattleResult", returned)], expected_count=1, preset_task=prefix, expected_steps=steps_done)
            steps = preset_events(stage)
            case(prefix + " preset resumes after cooperative invite", prefix + "Task", stage, [
                ("TeamPresetOpen", {"CommonRefuseBounty"}),
                ("CommonRefuseBounty", steps[0][1]),
                *steps[1:], (prefix + "Challenge", {result}),
                (prefix + "BattleResult", returned)], expected_count=1, preset_task=prefix, expected_steps=steps_done)
        raid_stage = {"PageRaid", "RealmRaidCard", "TeamPresetEntryText"}
        case("RealmRaid completes preset before choosing a card", "RealmRaidTask", raid_stage, [
            *preset_events(raid_stage),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1,
            pipeline_override={"RealmRaidPreset": {"enabled": True}})
        for prefix in ("Awakening",):
            stage = awake_preset_stage
            steps_done = ["AwakeningLockedTeam"]
            case(prefix + " preset missing window never starts challenge", prefix + "Task", stage,
                [("TeamPresetOpen", stage)] * 3, succeeds=False, preset_task=prefix, expected_steps=steps_done)
        window = awake_preset_stage | {"TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetFooter", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
        events = preset_events(awake_preset_stage)
        case("preset out must actually close window before completing", "AwakeningTask", awake_preset_stage, [
            *events[:-1], *[("TeamPresetOut", window | {"TeamPresetInUse"}) for _ in range(3)]],
            succeeds=False, preset_task="Awakening", expected_steps=["AwakeningLockedTeam"])
        case("unknown popup after opening preset blocks application", "AwakeningTask", awake_preset_stage, [
            ("TeamPresetOpen", {"TouchAreaRisk"})], succeeds=False, expect_popup_stop=True,
            preset_task="Awakening", expected_steps=["AwakeningLockedTeam"])
        stage = awake_preset_stage | {"AwakeningOpenBuffMenu"}
        case("awakening dependency order is lock preset buff then battle", "AwakeningTask", stage, [
            *preset_events(stage),
            ("AwakeningOpenBuffMenu", awake_buff_off),
            ("AwakeningEnableBuffClick", awake_buff_on),
            ("AwakeningCloseBuffAfterEnable", awake_stage),
            ("AwakeningChallenge", {"DailyBattleResult"}),
            ("AwakeningBattleResult", stage),
            ("AwakeningOpenBuffMenu", awake_buff_on),
            ("AwakeningDisableBuffClick", awake_buff_off),
            ("AwakeningCloseBuffAfterDisable", awake_stage)], expected_count=1,
            awakening_buff=True, preset_task="Awakening",
            expected_steps=["AwakeningLockedTeam", "AwakeningPresetDone", "AwakeningBuffEnabled", "AwakeningBuffDisabled"])
        region_controller = RegionController(screen)
        assert region_controller.post_connection().wait().succeeded
        assert resource.register_custom_action("RegionApiProbe", RegionApiProbe())
        region_tasker = Tasker()
        assert region_tasker.bind(resource, region_controller)
        assert region_tasker.post_task("RegionApiProbeEntry", {
            "RegionApiProbeEntry": {"action": {"type": "Custom", "param": {"custom_action": "RegionApiProbe"}}}
        }).wait().succeeded
        assert len(region_controller.clicks) == len(region_controller.swipes) == 8
        assert len(set(region_controller.clicks)) > 1
        assert len(set(region_controller.swipes)) > 1
        assert all(100 <= x < 140 and 100 <= y < 130 for x, y in region_controller.clicks)
        assert all(100 <= x1 < 130 and 500 <= y1 < 530 and 800 <= x2 < 830 and 100 <= y2 < 130
                   for x1, y1, x2, y2 in region_controller.swipes)
        print("PASS framework region inputs: click and swipe samples vary inside configured rectangles", flush=True)
    if failures:
        print(f"{len(failures)} scenario(s) failed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

