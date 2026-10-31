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
from custom.route_policy import with_purchase_stop
from custom.battle_session import run_battle_session
import custom.route_policy as route_policy
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
                'CurrentActivityChallenging', 'AwakeningChallenging'})
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
        return [100, 100, 10, 10] if matched else None


class Input(CustomAction):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def run(self, context, argv):
        state = self.state
        name = argv.node_name.removeprefix("SafeInput_")
        # StartApp is an application launch API, not a screenshot-based input.
        if json.loads(argv.custom_action_param or '{}').get('check_frame', True):
            for guard in ("TouchAreaChecked", "CommonRefuseBounty"):
                if state.probe_frames.get(guard) != state.frame:
                    state.error = f"Input {name} did not check {guard} on frame {state.frame}"
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
    "CommonActivityPresetDone", "CurrentActivityPresetDone",
}


class StepRecorder(ContextEventSink):
    """Record finished preparation steps, including those inside run_task sessions."""
    def __init__(self, state):
        super().__init__()
        self.state = state

    def on_node_pipeline_node(self, context, noti_type, detail):
        if noti_type == NotificationType.Succeeded and detail.name in STEP_DONE:
            self.state.completed_steps.append(detail.name)


class LocalTarget(CustomRecognition):
    """Simulate the local image predicate after the real business recognition."""
    def __init__(self, state):
        super().__init__()
        self.state=state

    def analyze(self, context, argv):
        public=json.loads(argv.custom_recognition_param)['target_node']
        # InputTarget reads the click action from this node; sub_name must carry it.
        if argv.node_name!='SafeInput_'+public:
            self.state.error=f"InputTarget for {public} received node {argv.node_name!r}"
            context.tasker.post_stop()
            return None
        detail=context.run_recognition(public,argv.image)
        if not detail or not detail.hit or detail.box is None:
            return None
        # InputTarget reuses roi as the click target; it must be this frame's target box.
        if list(argv.roi)!=list(detail.box):
            self.state.error=f"InputTarget for {public} received roi {list(argv.roi)}, target {list(detail.box)}"
            context.tasker.post_stop()
            return None
        self.state.probe_frames['TouchAreaChecked']=self.state.frame
        if 'TouchAreaRisk' in self.state.flags or not self.state.readable:
            self.state.touch_blocked = True
            context.tasker.post_stop()
            return None
        return list(detail.box)


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
                context.override_next(argv.node_name, with_purchase_stop([params["finish_node"] if done else params["continue_node"]], context))
        elif self.kind == "TargetCountDetermine":
            done = self.state.target != -1 and self.state.count >= self.state.target
            context.override_next(params["decision_node"], with_purchase_stop([params["finish_node"] if done else params["continue_node"]], context))
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
             preset_task=None, stop_after_input=None, expected_captures=None, pipeline_override=None):
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
    assert resource.register_custom_recognition("SimInputTarget", LocalTarget(state))
    assert resource.register_custom_action("SimInput", Input(state))
    for kind in ("TargetCountInit", "TargetCountProgress", "TargetCountDetermine", "TargetCountFinish",
                 "ResetCount", "ConfigureTeamRecognition", "RewardDropStatistics", "NodeOverride", "RunBattleSession"):
        assert resource.register_custom_action(kind, Counter(state, kind))
    override = {}
    for key, original in nodes.items():
        node = {"pre_delay": 0, "post_delay": 0, "rate_limit": 1, "timeout": 100,
                "pre_wait_freezes": 0, "post_wait_freezes": 0}
        recognition = original.get("recognition", {})
        if isinstance(recognition, dict) and "type" in recognition:
            param = recognition.get("param", {})
            children = param.get("all_of", []) + param.get("any_of", [])
            guard = next((c for c in children if isinstance(c, dict)
                          and c.get('recognition', {}).get('param', {}).get('custom_recognition') == 'InputTarget'), None)
            if guard:
                # Keep the native And gate; only the inline touch-area check is simulated.
                node['recognition'] = {'type': 'And', 'param': {**param, 'all_of': [
                    c if c is not guard else {**guard, 'recognition': {'type': 'Custom', 'param': {
                        **guard['recognition']['param'], 'custom_recognition': 'SimInputTarget'}}}
                    for c in children]}}
            elif key in ('MitamaChallenge', 'MitamaFinishOnce') or key.startswith("Page") or recognition["type"] not in ("And", "Or") or any(isinstance(c, dict) for c in children):
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
    for entry_node in ("MitamaTask", "ExplorationTask", "RealmRaidTask", "CommonActivityTask", "CurrentActivityTask", "AwakeningTask"):
        # Same shape as task options: the report option replaces custom_action_param whole, the count lives in attach.
        override[entry_node]["attach"] = {"target_count": requested_count}
        override[entry_node]["action"] = {"param": {"custom_action_param": {"report_drops": False}}}
    if prepare_only:
        override["MitamaLockTeam"]["enabled"] = False
        override["MitamaPreset"]["enabled"] = True
        override["MitamaEnableBuff"]["enabled"] = False
        override["MitamaPreparationDestination"]["next"] = []
    for stats in ("MitamaSoloDropStatistics", "MitamaTeamDropStatistics", "AwakeningDropStatistics"):
        override[stats]["enabled"] = enable_drop_statistics
    for step in ("AwakeningEnableBuff", "AwakeningDisableBuff"):
        override[step]["enabled"] = awakening_buff
    for prefix in ("Awakening", "CommonActivity", "CurrentActivity"):
        override[prefix + "Preset"]["enabled"] = prefix == preset_task
    if preset_task in ("CommonActivity", "CurrentActivity"):
        override[preset_task + "Task"]["next"] = with_purchase_stop([preset_task + "BeforeChallenge"])
    if stop_after_input:
        override['SafeInput_' + stop_after_input]['next'] = []
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
    if nodes['SafeInput_MitamaChallenge']['recognition']['type']=='Custom':
        route_policy.GUARD_PREFIX[:]=['[JumpBack]CommonBountyInterrupt']
    with tempfile.TemporaryDirectory() as folder:
        screen = Path(folder) / "screen.png"
        screen.write_bytes(blank_png())
        controller = CountingController(screen)
        assert controller.post_connection().wait().succeeded
        def case(*args, **kwargs):
            run_case(resource, controller, nodes, *args, **kwargs)
        case('challenge resets per-battle budget before matched input', 'MitamaBattleModeDestination', {'MitamaChallenge'},
            [('MitamaChallenge', set())], stop_after_input='MitamaChallenge', expected_captures=2)
        case('same-frame challenge risk blocks its visible target', 'MitamaBattleModeDestination', {'MitamaChallenge', 'TouchAreaRisk'},
            succeeds=False, stop_after_input='MitamaChallenge')
        case('same-frame challenge invitation is refused before target click', 'MitamaBattleModeDestination', {'MitamaChallenge', 'CommonRefuseBounty'},
            [('CommonRefuseBounty', {'MitamaChallenge'}), ('MitamaChallenge', set())], stop_after_input='MitamaChallenge')
        for page, entry in [('Home', 'NavigateHome'), ('Explore', 'NavigateExplore'),
                            ('MitamaStage', 'NavigateMitamaStage'), ('AwakeningStage', 'NavigateAwakening'),
                            ('Raid', 'NavigateRaid'), ('Chapter', 'NavigateChapter'), ('Mail', 'NavigateMail'),
                            ('QuestTasks', 'NavigateQuestTasks'), ('Quests', 'NavigateQuests')]:
            case('cooperative invite masks ' + page, entry, {'Page' + page, 'CommonRefuseBounty'},
                [('CommonRefuseBounty', {'Page' + page})])
        case('cooperative invite never overrides purchase stop', 'NavigateHome', {'PageHome', 'CommonRefuseBounty', 'TouchAreaRisk'}, succeeds=False)
        case('cooperative invite closes after missed refusal', 'NavigateHome', {'PageHome', 'CommonRefuseBounty'}, [
            ('CommonRefuseBounty', {'PageHome', 'CommonRefuseBounty'}), ('CommonRefuseBounty', {'PageHome'})])
        case('cooperative invite stuck stops after three refusals', 'NavigateHome', {'PageHome', 'CommonRefuseBounty'},
            [('CommonRefuseBounty', {'PageHome', 'CommonRefuseBounty'})] * 3, succeeds=False)
        case('cooperative invite disappears before input', 'NavigateHome', {'PageHome', 'CommonRefuseBounty'},
            probe_events=[('CommonRefuseBounty', {'PageHome'})])
        for prefix in ('Mitama', 'CommonActivity', 'CurrentActivity'):
            case(prefix + ' cooperative invite arrives between detection and click', prefix + 'Challenge', {prefix + 'Challenge'}, [
                ('CommonRefuseBounty', {prefix + 'Challenge'}),
                (prefix + 'Challenge', {prefix + 'BattleResult'}),
                (prefix + 'BattleResult', {'MitamaFinishOnce'} if prefix == 'Mitama' else {prefix + 'Challenge'})], expected_count=1,
                probe_events=[(prefix + 'Challenge', {prefix + 'Challenge', 'CommonRefuseBounty'})])
        case('four cooperative invites in one task get independent retry budgets', 'CommonActivityTask', {'CommonActivityChallenge', 'CommonRefuseBounty'}, [
            ('CommonRefuseBounty', {'CommonActivityChallenge'}),
            ('CommonActivityChallenge', {'CommonActivityBattleResult', 'CommonRefuseBounty'}),
            ('CommonRefuseBounty', {'CommonActivityBattleResult'}),
            ('CommonActivityBattleResult', {'CommonActivityChallenge', 'CommonRefuseBounty'}),
            ('CommonRefuseBounty', {'CommonActivityChallenge'}),
            ('CommonActivityChallenge', {'CommonActivityBattleResult', 'CommonRefuseBounty'}),
            ('CommonRefuseBounty', {'CommonActivityBattleResult'}),
            ('CommonActivityBattleResult', {'CommonActivityChallenge'})], requested_count=2, expected_count=2)
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
        case("waiting room is not a victory", "MitamaMemberFight", {"PageTeamRoom"}, succeeds=False)
        case("leader does not start for an unmatched teammate", "MitamaLeaderFight", {"PageTeamRoom"}, succeeds=False)
        case("unmatched invitation is not accepted", "MitamaTeamMember", {"MitamaInvitationText", "MitamaAcceptInvite"}, succeeds=False)
        case("named invitation and team settlement", "MitamaTeamMember", {"MitamaInvitationText", "MitamaInvitationName", "MitamaAcceptInvite"}, [
            ("MitamaAcceptNamedInvitation", {"DailyBattleResult"}),
            ("MitamaNativeBattleResult", {"PageTeamRoom"})], expected_count=1)
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
        case("raid risk blocks next card", "RealmRaidSuccess", {"PageRaid", "RealmRaidBroken"},
            requested_count=2, expected_count=1, succeeds=False,
            probe_events=[("RealmRaidBroken", {"PageRaid", "RealmRaidCard", "TouchAreaRisk"})])
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
        case("reward loop purchase popup after claim prevents next input", "NativeRewardsClaimLoop", {"PageQuests", "DailyRewardsClaimReward"}, [
            ("NativeRewardsClaim", {"PageQuests", "DailyRewardsClaimReward", "TouchAreaRisk"})], succeeds=False)
        for prefix in ("CommonActivity", "CurrentActivity"):
            case(prefix + " merged loop counts two victories and stops", prefix + "Task", {prefix + "Challenge"}, [
                (prefix + "Challenge", {prefix + "BattleResult"}),
                (prefix + "BattleResult", {prefix + "Challenge"}),
                (prefix + "Challenge", {prefix + "BattleResult"}),
                (prefix + "BattleResult", {prefix + "Challenge"})], requested_count=2, expected_count=2)
            case(prefix + " risk after result return prevents next input", prefix + "FinishOnce", {prefix + "Challenge"},
                requested_count=2, expected_count=1, succeeds=False,
                probe_events=[(prefix + "Challenge", {prefix + "Challenge", "TouchAreaRisk"})])
        for prefix in ("CommonActivity", "CurrentActivity", "Mitama"):
            start = prefix + "Task" if prefix != "Mitama" else "MitamaChallenge"
            returned = prefix + "Challenge" if prefix != "Mitama" else "MitamaFinishOnce"
            case(prefix + " single victory counts once", start, {prefix + "Challenge"}, [
                (prefix + "Challenge", {prefix + "BattleResult"}),
                (prefix + "BattleResult", {returned})], expected_count=1)
            case(prefix + " defeat does not count", start, {prefix + "Challenge"}, [
                (prefix + "Challenge", {"DailyBattleFailed", prefix + "BattleResult"}),
                (prefix + "FailedBattleResult", {returned})], succeeds=False)
            case(prefix + " resource handler finishes without input or count", prefix + "InsufficientAP",
                 {prefix + "Challenge", "CommonInsufficientAP"})
        # A dangerous button overlapping the touch range blocks even a positive background target.
        for entry, background in [
            ("MitamaChallenge", {"MitamaChallenge"}),
            ("CommonActivityTask", {"CommonActivityChallenge"}),
            ("CurrentActivityTask", {"CurrentActivityChallenge"}),
            ("DailyRewardsTask", {"PageMail", "DailyRewardsClaimAllMail"}),
            ("DailyRewardsClaimAllMail", {"DailyRewardsClaimAllMail"}),
            ("ReturnMain", {"NavBack", "ReturnMainHomeButton"}),
            ("NavCancelNoResource", {"NavCancelNoResource"}),
            ("CommonConfirm", {"CommonConfirm"}),
            ("StartupUpdateConfirm", {"StartupUpdateText", "StartupUpdateButton"}),
            ("ExplorationFindEnemy", {"PageChapterField", "ExplorationEnemyProbe"}),
            ("MitamaFindTeammate", {"PageTeamInvite", "MitamaTeammateName"}),
        ]:
            case(entry + " blocks a hazardous button overlapping its touch range", entry,
                 background | {"TouchAreaRisk"}, succeeds=False)
        case("purchase popup after challenge is never confirmed", "CommonActivityTask", {"CommonActivityChallenge"}, [
            ("CommonActivityChallenge", {"TouchAreaRisk", "CommonActivityChallenge", "CommonConfirm", "DailyRewardPopup"})], succeeds=False)
        case("purchase popup appears after button was matched", "CommonActivityTask", {"CommonActivityChallenge"},
             risk_after_probe="CommonActivityChallenge", succeeds=False)
        case("unreadable OCR screen prevents template click", "ReturnMain", {"ReturnMainHomeButton"},
             readable=False, succeeds=False)
        case("confirmation alone never authorizes an update click", "StartupUpdateConfirm", {"StartupUpdateButton"}, succeeds=False)
        case("unknown startup screen is never blindly clicked", "StartupSkipAnimation", set(), succeeds=False)
        case("explicit update context allows its own button", "StartupUpdateConfirm", {"StartupUpdateText", "StartupUpdateButton"}, [
            ("StartupUpdateConfirm", set())])
        case("desktop launch reaches server and verified home", "StartupTask", set(), [
            ("StartupTask", {"startupMain", "StartupCheckServer", "StartupEnterGame"}),
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
        case("mitama disbanded room exits without counting", "MitamaMemberFight", {"PageExplore"}, succeeds=False)
        private = {"PageTeamCreate","MitamaTeamPrivacyText","MitamaTeamCreateButton","MitamaTeamPrivacySelected"}
        public = private - {"MitamaTeamPrivacySelected"}
        case("private room already selected skips toggling", "MitamaVerifyTeamPrivacy", private,
            [("MitamaTeamCreate", {"PageTeamRoom"})], stop_after_input="MitamaTeamCreate")
        case("private selection retries a missed toggle", "MitamaVerifyTeamPrivacy", public,
            [("MitamaTeamSet", public),("MitamaTeamSet",private),("MitamaTeamCreate",{"PageTeamRoom"})],
            stop_after_input="MitamaTeamCreate")
        case("private setting never confirmed stops after three clicks", "MitamaVerifyTeamPrivacy", public,
            [("MitamaTeamSet",public)]*3, succeeds=False)
        case("creation refuses an unverified private setting", "MitamaTeamCreate", public, succeeds=False)
        case("created room must be seen before invite panel opens", "MitamaWaitCreatedRoom", {"PageTeamRoom","MitamaOpenInvite"},
            [("MitamaOpenInvite",set())], stop_after_input="MitamaOpenInvite")
        stage = {"PageMitamaStage", "TeamPresetEntryText"}
        window = {"PageMitamaStage", "TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
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
        case("purchase risk while opening preset prevents dependent steps", "MitamaPreset", stage, [
            ("TeamPresetOpen", window | {"TouchAreaRisk"})],
             prepare_only=True, succeeds=False, expected_steps=[])
        case("locked team enters battle while prepare is being rechecked", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaPrepare"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1,
            probe_events=[("MitamaPrepare", {"MitamaChallenging"}),
                          ("PageBattle", {"MitamaBattleResult"})])
        case("locked team skips prepare screen entirely", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("mitama merged turnaround counts two victories and stops", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce", "MitamaChallenge"}),
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce", "MitamaChallenge"})],
            requested_count=2, expected_count=2)
        case("mitama merged turnaround rechecks purchase risk before next challenge", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], requested_count=2, expected_count=1, succeeds=False,
            probe_events=[("MitamaFinishOnce", {"MitamaChallenge", "TouchAreaRisk"})])
        case("unlocked team still clicks its prepare button", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaPrepare"}),
            ("MitamaPrepare", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("team battle also tolerates automatic preparation", "MitamaMemberFight", {"MitamaPrepare"}, [
            ("MitamaNativeBattleResult", {"PageTeamRoom"})], expected_count=1,
            probe_events=[("MitamaPrepare", {"MitamaChallenging"}),
                          ("PageBattle", {"DailyBattleResult"})])
        case("first daily treasure closes before continuing settlement", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"MitamaFinishOnce"})], expected_count=1)
        case("treasure appears between settlement detection and input", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaBattleResult"}),
            ("MitamaTreasurePopup", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1,
            probe_events=[("MitamaBattleResult", {"MitamaTreasurePopup"})])
        case("missed treasure dismissal retries before settlement", "MitamaChallenge", {"MitamaChallenge"}, [
            ("MitamaChallenge", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"MitamaBattleResult"}),
            ("MitamaBattleResult", {"MitamaFinishOnce"})], expected_count=1)
        case("treasure dismissal stops after three misses", "MitamaTreasurePopup", {"MitamaTreasurePopup"},
            [("MitamaTreasurePopup", {"MitamaTreasurePopup"})] * 3, succeeds=False)
        case("treasure popup never bypasses purchase protection", "MitamaTreasurePopup",
            {"MitamaTreasurePopup", "TouchAreaRisk"}, succeeds=False)
        case("team treasure closes before returning to the room", "MitamaMemberFight", {"DailyBattleResult"}, [
            ("MitamaNativeBattleResult", {"MitamaTreasurePopup"}),
            ("MitamaTreasurePopup", {"PageTeamRoom"})], expected_count=1)
        for prefix in ("CommonActivity", "CurrentActivity"):
            case(prefix + " tolerates automatic preparation", prefix + "Task", {prefix + "Challenge"}, [
                (prefix + "Challenge", {prefix + "Prepare"}),
                (prefix + "BattleResult", {prefix + "Challenge"})], expected_count=1,
                probe_events=[(prefix + "Prepare", {prefix + "Challenging"}),
                              ("PageBattle", {prefix + "BattleResult"})])
        case("exploration preparation can advance before fresh target check", "ExplorationTask", {"PageChapterField", "ExplorationEnemyProbe"}, [
            ("ExplorationFindEnemy", {"DailyPrepare"}),
            ("ExplorationBattleResult", {"PageChapterField"})], expected_count=1,
            probe_events=[("DailyPrepare", {"PageBattle"}), ("PageBattle", {"DailyBattleResult"})])
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
        room = {"PageTeamRoom", "MitamaRoomTeammate", "MitamaTeamStartButton"}
        case("leader retries missed start and handles team start confirmation", "MitamaLeaderFight", room, [
            ("MitamaNamedTeamStart", room),
            ("MitamaNamedTeamStart", {"PageTeamRoom", "MitamaTeamStartConfirm"}),
            ("MitamaTeamStartConfirm", {"DailyBattleResult"}),
            ("MitamaNativeBattleResult", {"PageTeamRoom"})], expected_count=1)
        case("matching nickname alone does not authorize team start", "MitamaLeaderFight", {"PageTeamRoom", "MitamaRoomTeammate"}, succeeds=False)
        case("team member readies in the named room", "MitamaMemberFight", {"PageTeamRoom", "MitamaRoomTeammate", "MitamaRoomMemberReady"}, [
            ("MitamaRoomMemberReady", {"DailyBattleResult"}),
            ("MitamaNativeBattleResult", {"PageTeamRoom"})], expected_count=1)
        case("startup announcement closes before server entry", "StartupTask", set(), [
            ("StartupTask", {"StartupCloseAnnouncement"}),
            ("StartupCloseAnnouncement", {"startupMain", "StartupCheckServer", "StartupEnterGame"}),
            ("StartupEnterGame", {"StartupHome"})])
        case("optional illustration download is cancelled during startup", "StartupTask", set(), [
            ("StartupTask", {"StartupCancelIllustration"}),
            ("StartupCancelIllustration", {"startupMain", "StartupCheckServer", "StartupEnterGame"}),
            ("StartupEnterGame", {"StartupHome"})])
        for name in ("ExplorationOpenMainline", "DailyMailClaimConfirm", "DailyMailIllustrationCancel", "MitamaTeamStartConfirm",
                     "MitamaRoomMemberReady", "StartupCloseAnnouncement", "StartupCancelIllustration"):
            case(name + " preserves purchase stop priority", name, {name, "TouchAreaRisk"}, succeeds=False)
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
        case("team statistics execute once before settlement input", "MitamaMemberFight", {"DailyBattleResult"}, [
            ("MitamaNativeBattleResult", {"PageTeamRoom"})], expected_count=1,
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
        case("awakening auto preparation advances during target recheck", "AwakeningBattle", {"DailyPrepare"}, [
            ("AwakeningBattleResult", awake_stage)], expected_count=1,
            probe_events=[("DailyPrepare", {"PageBattle"}), ("PageBattle", {"DailyBattleResult"})])
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
        case("awakening refuses bounty before touching settlement", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"CommonRefuseBounty", "DailyBattleResult"}),
            ("CommonRefuseBounty", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1)
        case("awakening treasure may appear before fresh settlement input", "AwakeningBattle", {"DailyBattleResult"}, [
            ("MitamaTreasurePopup", {"DailyBattleResult"}),
            ("AwakeningBattleResult", awake_stage)], expected_count=1,
            probe_events=[("DailyBattleResult", {"MitamaTreasurePopup"})])
        case("awakening defeat never counts as a completed victory", "AwakeningTask", awake_stage, [
            ("AwakeningChallenge", {"DailyBattleFailed", "DailyBattleResult"}),
            ("AwakeningFailedBattleResult", awake_stage)], succeeds=False)
        case("awakening insufficient stamina stops before challenge", "AwakeningTask", awake_stage | {"DailyNoResource"}, succeeds=False)
        case("awakening cannot count an idle stage as success", "AwakeningBattle", awake_stage, succeeds=False)
        for name in ("AwakeningOpenType", "AwakeningSwitchType", "AwakeningSelectLayer", "AwakeningChallenge", "AwakeningBattleResult", "AwakeningOpenBuffMenu"):
            case(name + " keeps purchase protection", name, awake_stage | {name, "TouchAreaRisk"}, succeeds=False)
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
        for name in ("AwakeningEnableBuffClick", "AwakeningDisableBuffClick", "AwakeningCloseBuffAfterEnable", "AwakeningCloseBuffAfterDisable"):
            case(name + " cannot bypass purchase stop", name, awake_buff_on | {name, "TouchAreaRisk"}, succeeds=False)
        def preset_events(stage):
            window = stage | {"TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
            return [
                ("TeamPresetOpen", window),
                ("TeamPresetSelectTeamConfigured", window | {"TeamPresetApplyIcon"}),
                ("TeamPresetApplyIcon", window | {"TeamPresetConfirm"}),
                ("TeamPresetConfirm", window | {"TeamPresetInUse"}),
                ("TeamPresetOut", stage),
            ]
        awake_preset_stage = awake_stage | {"TeamPresetEntryText"}
        for prefix in ("Awakening", "CommonActivity", "CurrentActivity"):
            stage = awake_preset_stage if prefix == "Awakening" else {prefix + "Challenge", "TeamPresetEntryText"}
            result = "DailyBattleResult" if prefix == "Awakening" else prefix + "BattleResult"
            returned = stage if prefix != "Awakening" else awake_preset_stage
            steps_done = (["AwakeningLockedTeam"] if prefix == "Awakening" else []) + [prefix + "PresetDone"]
            case(prefix + " completes preset before challenge", prefix + "Task", stage, [
                *preset_events(stage),
                (prefix + "Challenge", {result}),
                (prefix + "BattleResult", returned)], expected_count=1, preset_task=prefix, expected_steps=steps_done)
            steps = preset_events(stage)
            window_with_bounty = steps[0][1] | {"CommonRefuseBounty"}
            case(prefix + " preset resumes after cooperative invite", prefix + "Task", stage, [
                ("TeamPresetOpen", window_with_bounty),
                ("CommonRefuseBounty", steps[0][1]),
                *steps[1:], (prefix + "Challenge", {result}),
                (prefix + "BattleResult", returned)], expected_count=1, preset_task=prefix, expected_steps=steps_done)
        raid_stage = {"PageRaid", "RealmRaidCard", "TeamPresetEntryText"}
        case("RealmRaid completes preset before choosing a card", "RealmRaidTask", raid_stage, [
            *preset_events(raid_stage),
            ("RealmRaidSelect", {"PageRaidTarget"}), ("RealmRaidAttack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaidBroken"})], expected_count=1,
            pipeline_override={"RealmRaidPreset": {"enabled": True}})
        activity_stage = {"CommonActivityChallenge", "TeamPresetEntryText"}
        case("activity preset runs once across multiple battles", "CommonActivityTask", activity_stage, [
            *preset_events(activity_stage),
            ("CommonActivityChallenge", {"CommonActivityBattleResult"}),
            ("CommonActivityBattleResult", activity_stage),
            ("CommonActivityChallenge", {"CommonActivityBattleResult"}),
            ("CommonActivityBattleResult", activity_stage)], requested_count=2, expected_count=2,
            preset_task="CommonActivity", expected_steps=["CommonActivityPresetDone"])
        for prefix in ("Awakening", "CommonActivity", "CurrentActivity"):
            stage = awake_preset_stage if prefix == "Awakening" else {prefix + "Challenge", "TeamPresetEntryText"}
            steps_done = ["AwakeningLockedTeam"] if prefix == "Awakening" else []
            case(prefix + " preset missing window never starts challenge", prefix + "Task", stage,
                [("TeamPresetOpen", stage)] * 3, succeeds=False, preset_task=prefix, expected_steps=steps_done)
        window = awake_preset_stage | {"TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
        events = preset_events(awake_preset_stage)
        case("preset out must actually close window before completing", "AwakeningTask", awake_preset_stage, [
            *events[:-1], *[("TeamPresetOut", window | {"TeamPresetInUse"}) for _ in range(3)]],
            succeeds=False, preset_task="Awakening", expected_steps=["AwakeningLockedTeam"])
        case("purchase popup after opening preset blocks application", "AwakeningTask", awake_preset_stage, [
            ("TeamPresetOpen", window | {"TouchAreaRisk"})], succeeds=False,
            preset_task="Awakening", expected_steps=["AwakeningLockedTeam"])
        for prefix in ("CommonActivity", "CurrentActivity"):
            case(prefix + " preset cannot begin from an existing battle", prefix + "Task", {prefix + "BattleResult"},
                succeeds=False, preset_task=prefix, expected_steps=[])
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

