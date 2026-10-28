"""Configure exact nickname recognitions; Pipeline owns the team workflow."""
import logging
import re

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from custom.action.target_count import get_runtime_params

logger = logging.getLogger("MaaOnmyoji.team_config")


def teammate():
    name = get_runtime_params().get("teammate", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("组队模式必须填写完整队友昵称")
    return name.strip()


@AgentServer.custom_action("ConfigureTeamRecognition")
class ConfigureTeamRecognition(CustomAction):
    """Escape the configured nickname without inspecting or operating the screen."""

    def run(self, context, argv):
        try:
            expected = "^" + re.escape(teammate()) + "$"
        except ValueError as exc:
            logger.error("%s", exc)
            return CustomAction.RunResult(success=False)
        nodes = ("MitamaTeammateName", "MitamaRoomTeammate", "MitamaInvitationName")
        success = context.override_pipeline({
            node: {"recognition": {"param": {"expected": expected}}}
            for node in nodes
        })
        return CustomAction.RunResult(success=success)
