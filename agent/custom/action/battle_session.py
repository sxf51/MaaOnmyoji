"""Run a native battle flow with bounded native cleanup on failure.

No screenshots, clicks or business polling live here. User stop/input-safety stop
always wins; cleanup never converts a failed battle into a successful task.
"""
import logging

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from .target_count import _parse_params
from custom.battle_session import run_battle_session

logger = logging.getLogger("MaaOnmyoji.battle_session")


@AgentServer.custom_action("RunBattleSession")
class RunBattleSession(CustomAction):
    def run(self, context, argv):
        try:
            return self.RunResult(success=run_battle_session(context, _parse_params(argv.custom_action_param)))
        except (AttributeError, TypeError, ValueError) as exc:
            logger.error("战斗会话参数无效：%s", exc)
            return self.RunResult(success=False)
