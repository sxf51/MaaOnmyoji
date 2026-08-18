"""通用目标次数控制"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

logger = logging.getLogger("MaaOnmyoji.target_count")


def _parse_params(raw: Any) -> dict[str, Any]:
    """兼容 MaaFramework 传入 JSON 字符串或字典两种形式。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"custom_action_param 不是有效 JSON: {raw!r}") from exc
        if isinstance(value, dict):
            return value
    raise ValueError("custom_action_param 必须是 JSON 对象")


def _positive_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if result <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return result


def _target_count(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_count 必须是正整数或 -1") from exc
    if result != -1 and result <= 0:
        raise ValueError("target_count 必须是正整数或 -1")
    return result


@dataclass
class TargetCountState:
    target_count: int = 0
    completed_count: int = 0
    initialized: bool = False

    def reset(self, target_count: int) -> None:
        self.target_count = _target_count(target_count)
        self.completed_count = 0
        self.initialized = True

    def progress(self, count: int = 1) -> None:
        if not self.initialized:
            raise RuntimeError("目标次数尚未初始化")
        self.completed_count += _positive_int(count, "count")

    @property
    def finished(self) -> bool:
        return (
            self.initialized
            and self.target_count != -1
            and self.completed_count >= self.target_count
        )


_state = TargetCountState()
_runtime_params: dict[str, Any] = {}


def reset_state_for_test() -> None:
    """仅供单元测试隔离全局 Agent 状态。"""
    global _state, _runtime_params
    _state = TargetCountState()
    _runtime_params = {}


def get_runtime_params() -> dict[str, Any]:
    """返回本次玩法初始化时传入的配置副本。"""
    return dict(_runtime_params)


@AgentServer.custom_action("TargetCountInit")
class TargetCountInit(CustomAction):
    """初始化一次玩法运行的目标次数。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        del context
        global _runtime_params
        try:
            params = _parse_params(argv.custom_action_param)
            _state.reset(params.get("target_count", 1))
        except (ValueError, RuntimeError) as exc:
            logger.error("初始化目标次数失败：%s", exc)
            return CustomAction.RunResult(success=False)

        _runtime_params = dict(params)
        # 每次玩法初始化时清除上一次可能因异常退出而遗留的掉落状态。
        from .drop_statistics import reset_drop_statistics

        reset_drop_statistics()

        if _state.target_count == -1:
            logger.info("目标挑战次数：无限")
        else:
            logger.info("目标挑战次数：%d", _state.target_count)
        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("TargetCountProgress")
class TargetCountProgress(CustomAction):
    """战斗结算成功后累计进度。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        del context
        try:
            params = _parse_params(argv.custom_action_param)
            _state.progress(params.get("count", 1))
        except (ValueError, RuntimeError) as exc:
            logger.error("记录挑战进度失败：%s", exc)
            return CustomAction.RunResult(success=False)

        if _state.target_count == -1:
            logger.info("已完成副本次数：%d", _state.completed_count)
        else:
            logger.info("挑战进度：%d/%d", _state.completed_count, _state.target_count)
        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("TargetCountDetermine")
class TargetCountDetermine(CustomAction):
    """根据进度把当前判断节点路由到继续节点或完成节点。

    参数：decision_node、continue_node、finish_node。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            params = _parse_params(argv.custom_action_param)
            decision_node = str(params["decision_node"])
            continue_node = str(params["continue_node"])
            finish_node = str(params["finish_node"])
            if not _state.initialized:
                raise RuntimeError("目标次数尚未初始化")
        except (KeyError, ValueError, RuntimeError) as exc:
            logger.error("判断目标次数失败：%s", exc)
            return CustomAction.RunResult(success=False)

        next_node = finish_node if _state.finished else continue_node
        context.override_next(decision_node, [next_node])
        logger.debug("次数判断节点 %s -> %s", decision_node, next_node)
        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("TargetCountFinish")
class TargetCountFinish(CustomAction):
    """输出最终进度；界面返回动作仍由各玩法 pipeline 决定。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        del context, argv
        if not _state.initialized:
            logger.error("目标次数尚未初始化")
            return CustomAction.RunResult(success=False)
        # 延迟导入避免通用次数模块与玩法模块形成导入环。
        from .drop_statistics import finish_drop_statistics

        finish_drop_statistics()
        logger.info("挑战完成：%d/%d", _state.completed_count, _state.target_count)
        return CustomAction.RunResult(success=True)
