"""timeout only covers a next list with no hit, so every repeating path needs a max_hit or a win count."""
import unittest
from collections import defaultdict
from pathlib import Path

from tools.validate_schema import load_jsonc

ROOT = Path(__file__).resolve().parents[1]

# JumpBack callees that may legitimately return to their caller repeatedly.
ALLOWED_RETURNS = {
    "CommonBountyInterrupt": "只在弹窗关闭后返回；拒绝点击由 SafeInput_CommonRefuseBounty 限次",
    "MitamaTreasurePopup": "只在提示关闭后返回；点击由 SafeInput_MitamaTreasurePopup 限次",
    "MitamaPrepare": "已进入战斗才返回；点击已限次",
    "DailyPrepare": "已进入战斗才返回；点击已限次",
    "CommonActivityPrepare": "已进入战斗才返回；点击已限次",
    "CurrentActivityPrepare": "已进入战斗才返回；点击已限次",
    "StartupCheckServer": "等待资源下载，时长不可预估",
    "StartupDownloading": "等待资源下载，时长不可预估",
    **{f"Navigate{page}": "到达目标页才返回；调用方同帧认可目标页，导航动作已限次"
       for page in ("Awakening", "Bondling", "Chapter", "Explore", "Home", "Mail", "QuestTasks", "Quests", "Raid")},
}


def load_nodes():
    nodes = {}
    for path in (ROOT / "assets/resource/base/pipeline").glob("*.json"):
        if path.name != "Example.json":
            nodes.update(load_jsonc(path))
    return nodes


class LoopGraph:
    def __init__(self, nodes):
        self.nodes = nodes
        self.plain, self.calls = defaultdict(set), defaultdict(set)
        for name, node in nodes.items():
            self.add_next(name, node.get("next", []))
            self.add_routes(name, node.get("action"))
        for path in (ROOT / "assets/tasks").rglob("*.json"):
            self.scan_options(load_jsonc(path))
        self.counted = {n for n in nodes if self.custom(n) == "TargetCountProgress"}
        self.capped = {n for n, d in nodes.items() if d.get("max_hit") is not None}
        self.effective = self.effective_budgets()

    def add_next(self, source, items):
        for item in items:
            name = item.replace("[JumpBack]", "").replace("[Anchor]", "")
            if name in self.nodes:
                (self.calls if item.startswith("[JumpBack]") else self.plain)[source].add(name)

    def add_routes(self, name, action):
        param = action.get("param", {}).get("custom_action_param") if isinstance(action, dict) else None
        if isinstance(param, dict):
            for key in ("continue_node", "finish_node"):
                if param.get(key) in self.nodes:
                    self.plain[param.get("decision_node", name)].add(param[key])

    def scan_options(self, value):
        if isinstance(value, dict):
            for name, data in (value.get("pipeline_override") or {}).items():
                if name in self.nodes and isinstance(data, dict):
                    self.add_next(name, data.get("next", []))
                    self.add_routes(name, data.get("action"))
            for item in value.values():
                self.scan_options(item)
        elif isinstance(value, list):
            for item in value:
                self.scan_options(item)

    def custom(self, name):
        action = self.nodes[name].get("action")
        return action.get("param", {}).get("custom_action") if isinstance(action, dict) else None

    def resets(self, name):
        if self.custom(name) != "ResetCount":
            return set()
        return set(self.nodes[name]["action"]["param"]["custom_action_param"].get("nodes", []))

    def reachable(self, start, blocked):
        seen, stack = set(), [start]
        while stack:
            for following in self.plain[stack.pop()]:
                if following not in seen and following not in blocked:
                    seen.add(following)
                    stack.append(following)
        return seen

    def effective_budgets(self):
        """A budget stops counting only if a win-free path through its own reset node leads back to it."""
        effective = set(self.capped)
        changed = True
        while changed:
            changed = False
            for reset_node in self.nodes:
                for budget in self.resets(reset_node) & effective:
                    blocked = (effective | self.counted) - {budget}
                    if reset_node in blocked:
                        continue
                    if reset_node in self.reachable(budget, blocked) and budget in self.reachable(reset_node, blocked):
                        effective.discard(budget)
                        changed = True
        return effective

    def unbounded_cycles(self):
        blocked = self.effective | self.counted
        return sorted(name for name in self.nodes if name not in blocked and name in self.reachable(name, blocked))

    def unbounded_returns(self):
        blocked = self.effective | self.counted
        found = set()
        for callees in self.calls.values():
            for callee in callees:
                if callee in blocked:
                    continue
                seen, stack = set(), [callee]
                while stack:
                    current = stack.pop()
                    if current in seen or current in blocked:
                        continue
                    seen.add(current)
                    if not self.plain[current]:
                        found.add(callee)
                        break
                    stack.extend(self.plain[current])
        return found


class LoopBoundsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = LoopGraph(load_nodes())

    def test_next_cycles_are_bounded(self):
        self.assertEqual(self.graph.unbounded_cycles(), [])

    def test_jumpback_callees_are_bounded_or_justified(self):
        self.assertEqual(sorted(self.graph.unbounded_returns() - set(ALLOWED_RETURNS)), [])

    def test_allowed_returns_are_still_needed(self):
        self.assertEqual(sorted(set(ALLOWED_RETURNS) - self.graph.unbounded_returns()), [])

    def test_long_waits_cannot_spin_through_their_budget(self):
        # max_hit counts hits, so a wait budget only means time when each hit takes time.
        nodes = self.graph.nodes
        for name in ("CommonBattleWait", "CommonActivityChallenging", "CurrentActivityChallenging"):
            self.assertEqual((nodes[name]["max_hit"], nodes[name]["post_delay"]), (1800, 1000), name)


class PerBattleBudgetTest(unittest.TestCase):
    """A budget missing here would run out after enough wins and stop a healthy task."""
    SHARED = {"CommonBattleWait", "CommonBattleEnteringWait", "CommonCutscene", "SafeInput_DailyRewardPopup"}
    EXPECTED = {
        "MitamaBattleEntry": {"SafeInput_MitamaChallenge", "SafeInput_MitamaPrepare", "SafeInput_MitamaBattleResult"} | SHARED,
        "TeamLeaderBattleEntry": {"SafeInput_TeamLeaderStart", "SafeInput_DailyPrepare",
                                    "SafeInput_TeamLeaderBattleResult", "SafeInput_CommonConfirm", "SafeInput_TeamLeaderReinvite"} | SHARED,
        "TeamMemberBattleEntry": {"SafeInput_TeamMemberAcceptDefault", "SafeInput_TeamMemberAccept", "SafeInput_TeamMemberBattleResult", "SafeInput_DailyPrepare",
                                  "TeamMemberRoomWait", "TeamMemberRoomTick", "TeamMemberIdleEmoteReady", "SafeInput_TeamMemberAutoReadyConfirm", "TeamMemberInBattle", "SafeInput_TeamMemberWinResult",
                                  "CommonBattleWait", "CommonBattleEnteringWait", "SafeInput_DailyRewardPopup"},
        "AwakeningBattleEntry": {"SafeInput_AwakeningChallenge", "SafeInput_DailyPrepare", "SafeInput_AwakeningBattleResult"} | SHARED,
        "BondlingSearchBattleEntry": {"SafeInput_BondlingSearchStart", "SafeInput_DailyPrepare", "SafeInput_BondlingSearchBattleResult",
                                      "SafeInput_BondlingSearchGatherLater"} | SHARED,
        "ExplorationFieldReady": {"SafeInput_DailyPrepare", "SafeInput_ExplorationBattleResult", "SafeInput_ExplorationBattleFailedResult",
                                  "CommonBattleEnteringWait", "SafeInput_DailyRewardPopup"},
        "ExplorationScanReset": {"SafeInput_DailyPrepare", "SafeInput_ExplorationBattleResult", "SafeInput_ExplorationBattleFailedResult",
                                 "CommonBattleEnteringWait", "SafeInput_DailyRewardPopup"},
        "RealmRaidValidateChoice": {"SafeInput_DailyPrepare", "SafeInput_RealmRaidBattleResult", "SafeInput_RealmRaidBattleFailedResult",
                                    "CommonBattleEnteringWait", "SafeInput_DailyRewardPopup", "RealmRaidBattleResume"},
        "CommonActivityPreparation": {"SafeInput_CommonActivityChallenge", "SafeInput_CommonActivityPrepare", "SafeInput_CommonActivityBattleResult",
                                      "CommonActivityChallenging", "SafeInput_CommonClickAuto", "CommonBattleEnteringWait", "CommonCutscene"},
        "CurrentActivityPreparation": {"SafeInput_CurrentActivityChallenge", "SafeInput_CurrentActivityPrepare", "SafeInput_CurrentActivityBattleResult",
                                       "CurrentActivityChallenging", "SafeInput_CommonClickAuto", "CommonBattleEnteringWait", "CommonCutscene"},
    }

    def test_each_battle_entry_refreshes_its_budgets(self):
        graph = LoopGraph(load_nodes())
        for entry, budgets in self.EXPECTED.items():
            with self.subTest(entry=entry):
                self.assertLessEqual(budgets, graph.resets(entry))
                self.assertLessEqual(budgets, graph.capped)


class GraphRulesTest(unittest.TestCase):
    def graph(self, nodes):
        return LoopGraph({name: {"recognition": {"type": "DirectHit"}, **node} for name, node in nodes.items()})

    def test_detects_a_retry_without_budget(self):
        graph = self.graph({"Ping": {"next": ["Pong"]}, "Pong": {"next": ["Ping"]}})
        self.assertEqual(graph.unbounded_cycles(), ["Ping", "Pong"])

    def test_budget_on_the_cycle_bounds_it(self):
        graph = self.graph({"Ping": {"next": ["Pong"], "max_hit": 3}, "Pong": {"next": ["Ping"]}})
        self.assertEqual(graph.unbounded_cycles(), [])

    def test_budget_reset_inside_its_own_cycle_does_not_count(self):
        reset = {"type": "Custom", "param": {"custom_action": "ResetCount", "custom_action_param": {"nodes": ["Ping"]}}}
        graph = self.graph({"Ping": {"next": ["Pong"], "max_hit": 3}, "Pong": {"next": ["Ping"], "action": reset}})
        self.assertEqual(graph.unbounded_cycles(), ["Ping", "Pong"])

    def test_reset_behind_another_budget_still_counts(self):
        reset = {"type": "Custom", "param": {"custom_action": "ResetCount", "custom_action_param": {"nodes": ["Ping"]}}}
        graph = self.graph({"Ping": {"next": ["Pong"], "max_hit": 3}, "Pong": {"next": ["Gate"]},
                            "Gate": {"next": ["Ping"], "max_hit": 3, "action": reset}})
        self.assertEqual(graph.unbounded_cycles(), [])

    def test_win_count_bounds_a_cycle(self):
        count = {"type": "Custom", "param": {"custom_action": "TargetCountProgress"}}
        graph = self.graph({"Fight": {"next": ["Won"]}, "Won": {"next": ["Fight"], "action": count}})
        self.assertEqual(graph.unbounded_cycles(), [])

    def test_detects_a_jumpback_callee_that_keeps_returning(self):
        graph = self.graph({"Loop": {"next": ["[JumpBack]Wait"]}, "Wait": {"next": []}})
        self.assertEqual(graph.unbounded_returns(), {"Wait"})


if __name__ == "__main__":
    unittest.main()
