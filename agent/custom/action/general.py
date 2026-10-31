"""参数化的通用自定义动作"""

from __future__ import annotations

import logging

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.general")


@AgentServer.custom_action("NodeOverride")
class NodeOverride(CustomAction):
    """将 custom_action_param 直接作为 pipeline override 应用。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            override = _parse_params(argv.custom_action_param)
        except ValueError as exc:
            logger.error("NodeOverride: %s", exc)
            return CustomAction.RunResult(success=False)
        if not override:
            logger.warning("NodeOverride 收到空覆盖")
            return CustomAction.RunResult(success=True)
        return CustomAction.RunResult(success=context.override_pipeline(override))


@AgentServer.custom_action("ResetCount")
class ResetCount(CustomAction):
    """清除指定 pipeline 节点的命中计数。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            params = _parse_params(argv.custom_action_param)
            nodes = params.get("nodes")
            strict = params.get("strict", False)
            if not isinstance(nodes, list) or not nodes:
                raise ValueError("nodes 必须是非空列表")
            if not isinstance(strict, bool):
                raise ValueError("strict 必须是布尔值")
        except ValueError as exc:
            logger.error("ResetCount: %s", exc)
            return CustomAction.RunResult(success=False)

        failures = [name for name in nodes if not isinstance(name, str) or not context.clear_hit_count(name)]
        if failures:
            logger.warning("以下节点计数清除失败：%s", failures)
        return CustomAction.RunResult(success=not (strict and failures))
