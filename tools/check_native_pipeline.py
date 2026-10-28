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
import custom.route_policy as route_policy
from probe_pages import ScreenshotController
from validate_schema import load_jsonc
from maa.custom_action import CustomAction
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
        self.disabled = []
        self.probe_events = []
        self.statistics = 0
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
        name = json.loads(argv.custom_recognition_param)["probe"]
        self.state.probe_frames[name] = self.state.frame
        matched = self.state.readable if name == "CommonSafetyScreenReadable" else name in self.state.flags
        if name == 'CommonBuffMenuIcon':
            matched = bool(self.state.flags & {'CommonBuffMenuIcon', 'MitamaOpenBuffMenu', 'AwakeningOpenBuffMenu'})
        if name == 'PageBattle':
            matched = bool(self.state.flags & {'PageBattle', 'MitamaChallenging', 'CommonActivityChallenging',
                'CurrentActivityChallenging', 'AwakeningChallenging'})
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


class LocalTarget(CustomRecognition):
    """Simulate the local image predicate after the real business recognition."""
    def __init__(self, state):
        super().__init__()
        self.state=state

    def analyze(self, context, argv):
        public=json.loads(argv.custom_recognition_param)['target_node']
        detail=context.run_recognition(public,argv.image)
        if not detail or not detail.hit or detail.box is None:
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
        if self.kind == "TargetCountInit":
            self.state.target = int(params.get("target_count", 1))
            self.state.count = 0
        elif self.kind == "TargetCountProgress":
            self.state.count += params.get("count", 1)
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
        elif self.kind == "SubTask":
            enabled = [name for name in params["sub"] if context.get_node_data(name).get("enabled", True)]
            context.override_next(argv.node_name, with_purchase_stop(enabled[:1] or params["completion_next"], context))
        elif self.kind == "DisableNode":
            names = params.get("nodes", params.get("node_name"))
            if isinstance(names, str):
                names = [names]
            assert context.override_pipeline({name: {"enabled": False} for name in names})
            self.state.disabled.extend(names)
        elif self.kind == "MitamaDropStatistics":
            self.state.statistics += 1
        elif self.kind == "NodeOverride":
            return self.RunResult(success=context.override_pipeline(params))
        return self.RunResult(success=True)


def run_case(resource, controller, nodes, name, entry, flags, events=(), expected_count=0, succeeds=True,
             readable=True, risk_after_probe=None, prepare_only=False, expected_disabled=None, probe_events=(),
             enable_drop_statistics=False, expected_statistics=0, requested_count=1, awakening_buff=False,
             preset_task=None, stop_after_input=None, expected_captures=None):
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
                 "ResetCount", "ConfigureTeamRecognition", "SubTask", "DisableNode", "MitamaDropStatistics", "NodeOverride"):
        assert resource.register_custom_action(kind, Counter(state, kind))
    override = {}
    for key, original in nodes.items():
        node = {"pre_delay": 0, "post_delay": 0, "rate_limit": 1, "timeout": 50,
                "pre_wait_freezes": 0, "post_wait_freezes": 0}
        recognition = original.get("recognition", {})
        if isinstance(recognition, dict) and "type" in recognition:
            param = recognition.get("param", {})
            children = param.get("all_of", []) + param.get("any_of", [])
            if recognition['type']=='Custom' and param.get('custom_recognition')=='InputTarget':
                node['recognition']={'type':'Custom','param':{'custom_recognition':'SimInputTarget',
                    'custom_recognition_param':param['custom_recognition_param']}}
            elif key.startswith("Page") or recognition["type"] not in ("And", "Or") or any(isinstance(c, dict) for c in children):
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
    for entry_node in ("ExplorationTask", "RealmRaidTask", "CommonActivityTask", "CurrentActivityTask", "AwakeningTask"):
        override[entry_node]["action"] = {"param": {"custom_action_param": {"target_count": requested_count}}}
    if prepare_only:
        override["MitamaLockTeam"]["enabled"] = False
        override["MitamaPreset"]["enabled"] = True
        override["MitamaEnableBuff"]["enabled"] = False
        override["MitamaPreparationDestination"]["next"] = []
    for stats in ("MitamaSoloDropStatistics", "MitamaTeamDropStatistics"):
        override[stats]["enabled"] = enable_drop_statistics
    for step in ("AwakeningEnableBuff", "AwakeningDisableBuff"):
        override[step]["enabled"] = awakening_buff
    for prefix in ("Awakening", "CommonActivity", "CurrentActivity"):
        override[prefix + "Preset"]["enabled"] = prefix == preset_task
    if preset_task in ("CommonActivity", "CurrentActivity"):
        override[preset_task + "Task"]["next"] = with_purchase_stop([preset_task + "BeforeChallenge"])
    if stop_after_input:
        override['SafeInput_' + stop_after_input]['next'] = []
    tasker = Tasker()
    assert tasker.bind(resource, controller)
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
    if expected_disabled is not None:
        assert state.disabled == expected_disabled, (name, state.disabled)
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
        case('same-frame challenge dispatch takes one screenshot before click', 'MitamaBattleModeDestination', {'MitamaChallenge'},
            [('MitamaChallenge', set())], stop_after_input='MitamaChallenge', expected_captures=1)
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
        case("empty chapter reenters only once", "ExplorationTask", {"PageChapterField", "NavBack"}, [
            *[("ExplorationEnemySwipe", {"PageChapterField", "NavBack"}) for _ in range(6)],
            ("NativeToChapterNavBack", {"PageExitExplore", "NavConfirmExitExplore"}),
            ("NativeNavConfirmExitExplore", {"PageChapter", "ExplorationEnterChapter"}),
            ("ExplorationReenterClick", {"PageChapterField", "NavBack"}),
            *[("ExplorationEnemySwipe", {"PageChapterField", "NavBack"}) for _ in range(6)]], succeeds=False)
        case("skip broken card and verify correct stamp", "RealmRaidTask", {"PageRaid", "RealmRaid0Broken", "RealmRaid0Card", "RealmRaid1Card"}, [
            ("RealmRaidSelect1", {"PageRaidTarget", "RealmRaidChallenge"}),
            ("RealmRaid1Attack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaid1Broken"})], expected_count=1)
        case("missing victory stamp fails", "RealmRaidTask", {"PageRaid", "RealmRaid0Card"}, [
            ("RealmRaidSelect0", {"RealmRaidChallenge"}),
            ("RealmRaid0Attack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid"})], succeeds=False)
        case("raid merged loop skips first stamp and counts second card", "RealmRaidTask", {"PageRaid", "RealmRaid0Card"}, [
            ("RealmRaidSelect0", {"PageRaidTarget", "RealmRaidChallenge"}),
            ("RealmRaid0Attack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaid0Broken", "RealmRaid1Card"}),
            ("RealmRaidSelect1", {"PageRaidTarget", "RealmRaidChallenge"}),
            ("RealmRaid1Attack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaid0Broken", "RealmRaid1Broken"})], requested_count=2, expected_count=2)
        case("raid risk after stamp detection blocks next card", "RealmRaid0Success", {"PageRaid", "RealmRaid0Broken"},
            requested_count=2, expected_count=1, succeeds=False,
            probe_events=[("RealmRaid0Broken", {"PageRaid", "RealmRaid0Broken", "RealmRaid1Card", "TouchAreaRisk"})])
        case("no raid tickets stops without count", "RealmRaidTask", {"PageRaid", "RealmRaid0Card"}, [
            ("RealmRaidSelect0", {"RealmRaidChallenge"}),
            ("RealmRaid0Attack", {"DailyNoResource"})])
        case("all raid cards already broken", "RealmRaidTask", {"PageRaid", *[f"RealmRaid{i}Broken" for i in range(9)]})
        case("raid folded dispatch reaches ninth card after eight stamps", "RealmRaidTask", {"PageRaid", "RealmRaid8Card", *[f"RealmRaid{i}Broken" for i in range(8)]}, [
            ("RealmRaidSelect8", {"PageRaidTarget", "RealmRaidChallenge"}),
            ("RealmRaid8Attack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", *[f"RealmRaid{i}Broken" for i in range(9)]})], expected_count=1)
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
        stage = {"PageMitamaStage", "MitamaPresetEntryText"}
        window = {"PageMitamaStage", "MitamaPresetFrame", "MitamaPresetTeamOut", "MitamaCheckTeamGroupSelected", "MitamaSelectPresetTeamConfigured"}
        apply_steps = [
            ("MitamaSelectPresetTeamConfigured", window | {"MitamaClickPresetTeamIcon"}),
            ("MitamaClickPresetTeamIcon", window | {"MitamaConfirmPreset"}),
            ("MitamaConfirmPreset", window | {"MitamaPresetTeamInUse"}),
        ]
        case("preset retries missed open and missed exit before completing", "MitamaPreset", stage, [
            ("MitamaOpenPreset", stage), ("MitamaOpenPreset", window), *apply_steps,
            ("MitamaPresetTeamOut", window | {"MitamaPresetTeamInUse"}),
            ("MitamaPresetTeamOut", {"PageMitamaStage"})], prepare_only=True, expected_disabled=["MitamaPreset"])
        case("preset never skips unopened window after three misses", "MitamaPreset", stage,
             [("MitamaOpenPreset", stage)] * 3, prepare_only=True, succeeds=False, expected_disabled=[])
        case("preset never completes while exit window remains open", "MitamaPreset", stage, [
            ("MitamaOpenPreset", window), *apply_steps,
            *[("MitamaPresetTeamOut", window | {"MitamaPresetTeamInUse"}) for _ in range(3)]],
             prepare_only=True, succeeds=False, expected_disabled=[])
        case("purchase risk while opening preset prevents dependent steps", "MitamaPreset", stage, [
            ("MitamaOpenPreset", window | {"TouchAreaRisk"})],
             prepare_only=True, succeeds=False, expected_disabled=[])
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
        case("raid selection miss retries the same card", "RealmRaidTask", {"PageRaid", "RealmRaid0Card"}, [
            ("RealmRaidSelect0", {"PageRaid", "RealmRaid0Card"}),
            ("RealmRaidSelect0", {"RealmRaidChallenge"}),
            ("RealmRaid0Attack", {"DailyBattleResult"}),
            ("RealmRaidBattleResult", {"PageRaid", "RealmRaid0Broken"})], expected_count=1)
        case("raid selection stops after three misses", "RealmRaidTask", {"PageRaid", "RealmRaid0Card"},
            [("RealmRaidSelect0", {"PageRaid", "RealmRaid0Card"})] * 3, succeeds=False)
        case("raid reward popup is dismissed before stamp verification", "RealmRaid0Verify", {"PageRaid", "DailyRewardPopup"}, [
            ("DailyRewardPopup", {"PageRaid", "RealmRaid0Broken"})], expected_count=1)
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
            ("AwakeningBattleResult", awake_stage)], expected_count=1, expected_disabled=["AwakeningLockTeam"])
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
            ("AwakeningChallenge", {"PageBattle", "AwakeningEnableAuto"}),
            ("AwakeningEnableAuto", {"DailyBattleResult"}),
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
            ("AwakeningBattleResult", awake_stage)], expected_count=1, expected_disabled=["AwakeningLockTeam"])
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
            expected_disabled=["AwakeningLockTeam", "AwakeningEnableBuff", "AwakeningDisableBuff"])
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
            disabled = (["AwakeningLockTeam"] if prefix == "Awakening" else []) + [prefix + "Preset"]
            case(prefix + " completes preset before challenge", prefix + "Task", stage, [
                *preset_events(stage),
                (prefix + "Challenge", {result}),
                (prefix + "BattleResult", returned)], expected_count=1, preset_task=prefix, expected_disabled=disabled)
            steps = preset_events(stage)
            window_with_bounty = steps[0][1] | {"CommonRefuseBounty"}
            case(prefix + " preset resumes after cooperative invite", prefix + "Task", stage, [
                ("TeamPresetOpen", window_with_bounty),
                ("CommonRefuseBounty", steps[0][1]),
                *steps[1:], (prefix + "Challenge", {result}),
                (prefix + "BattleResult", returned)], expected_count=1, preset_task=prefix, expected_disabled=disabled)
        activity_stage = {"CommonActivityChallenge", "TeamPresetEntryText"}
        case("activity preset runs once across multiple battles", "CommonActivityTask", activity_stage, [
            *preset_events(activity_stage),
            ("CommonActivityChallenge", {"CommonActivityBattleResult"}),
            ("CommonActivityBattleResult", activity_stage),
            ("CommonActivityChallenge", {"CommonActivityBattleResult"}),
            ("CommonActivityBattleResult", activity_stage)], requested_count=2, expected_count=2,
            preset_task="CommonActivity", expected_disabled=["CommonActivityPreset"])
        for prefix in ("Awakening", "CommonActivity", "CurrentActivity"):
            stage = awake_preset_stage if prefix == "Awakening" else {prefix + "Challenge", "TeamPresetEntryText"}
            disabled = ["AwakeningLockTeam"] if prefix == "Awakening" else []
            case(prefix + " preset missing window never starts challenge", prefix + "Task", stage,
                [("TeamPresetOpen", stage)] * 3, succeeds=False, preset_task=prefix, expected_disabled=disabled)
        window = awake_preset_stage | {"TeamPresetFrameTemplate", "TeamPresetOut", "TeamPresetGroupSelected", "TeamPresetSelectTeamConfigured"}
        events = preset_events(awake_preset_stage)
        case("preset out must actually close window before completing", "AwakeningTask", awake_preset_stage, [
            *events[:-1], *[("TeamPresetOut", window | {"TeamPresetInUse"}) for _ in range(3)]],
            succeeds=False, preset_task="Awakening", expected_disabled=["AwakeningLockTeam"])
        case("purchase popup after opening preset blocks application", "AwakeningTask", awake_preset_stage, [
            ("TeamPresetOpen", window | {"TouchAreaRisk"})], succeeds=False,
            preset_task="Awakening", expected_disabled=["AwakeningLockTeam"])
        for prefix in ("CommonActivity", "CurrentActivity"):
            case(prefix + " preset cannot begin from an existing battle", prefix + "Task", {prefix + "BattleResult"},
                succeeds=False, preset_task=prefix, expected_disabled=[])
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
            expected_disabled=["AwakeningLockTeam", "AwakeningPreset", "AwakeningEnableBuff", "AwakeningDisableBuff"])
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

