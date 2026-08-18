"""参数化的通用自定义动作"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.general")

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _to_beijing_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING_TIMEZONE)
    return parsed.astimezone(BEIJING_TIMEZONE)


@AgentServer.custom_action("DisableNode")
class DisableNode(CustomAction):
    """禁用一个或多个 pipeline 节点。参数：nodes 或 node_name。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            params = _parse_params(argv.custom_action_param)
            nodes = params.get("nodes", params.get("node_name"))
            if isinstance(nodes, str):
                nodes = [nodes]
            if not isinstance(nodes, list) or not nodes or not all(isinstance(n, str) and n for n in nodes):
                raise ValueError("nodes 必须是非空节点名称列表")
        except ValueError as exc:
            logger.error("DisableNode: %s", exc)
            return CustomAction.RunResult(success=False)

        success = context.override_pipeline(
            {name: {"enabled": False} for name in nodes}
        )
        return CustomAction.RunResult(success=success)


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
        context.override_pipeline(override)
        return CustomAction.RunResult(success=True)


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


@AgentServer.custom_action("SubTask")
class SubTask(CustomAction):
    """把 enabled 的 sub 节点作为有序 next；全部完成后走 completion_next。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            params = _parse_params(argv.custom_action_param)
            tasks = params.get("sub")
            completion_next = params.get("completion_next")
            timeout = int(params["timeout"])
            if not isinstance(tasks, list) or not tasks:
                raise ValueError("sub 必须是非空任务名称列表")
            if not all(isinstance(name, str) and name for name in tasks):
                raise ValueError("sub 中的节点名必须是非空字符串")
            if not isinstance(completion_next, list) or not completion_next:
                raise ValueError("completion_next 必须是非空任务名称列表")
            if not all(isinstance(name, str) and name for name in completion_next):
                raise ValueError("completion_next 中的节点名必须是非空字符串")
            if timeout <= 0:
                raise ValueError("timeout 必须大于 0")
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("SubTask: %s", exc)
            return CustomAction.RunResult(success=False)

        key = (argv.task_detail.task_id, argv.node_name)
        enabled_tasks: list[str] = []
        for task_name in tasks:
            node_data = context.get_node_data(task_name)
            if node_data is None:
                _clear_subtask_state(key)
                logger.error("SubTask 分支节点不存在：%s", task_name)
                return CustomAction.RunResult(success=False)
            if node_data.get("enabled", True):
                enabled_tasks.append(task_name)

        if not enabled_tasks:
            _clear_subtask_state(key)
            success = context.override_next(argv.node_name, completion_next)
            logger.debug("SubTask %s 完成 -> %s", argv.node_name, completion_next)
            return CustomAction.RunResult(success=success)

        now = time.monotonic()
        with _subtask_next_lock:
            state = _subtask_states.get(key)
            if state is None:
                state = _SubTaskState(deadline=now + timeout / 1000)
                _subtask_states[key] = state
            elif now >= state.deadline:
                del _subtask_states[key]
                logger.error("SubTask 超时：%s", argv.node_name)
                return CustomAction.RunResult(success=False)

        target_next = enabled_tasks
        success = context.override_next(argv.node_name, target_next)
        if not success:
            _clear_subtask_state(key)
        logger.debug("SubTask %s -> %s", argv.node_name, target_next)
        return CustomAction.RunResult(success=success)


@dataclass
class _SubTaskState:
    deadline: float


_subtask_states: dict[tuple[int, str], _SubTaskState] = {}
_subtask_next_lock = threading.Lock()


def _clear_subtask_state(key: tuple[int, str]) -> None:
    with _subtask_next_lock:
        _subtask_states.pop(key, None)


def reset_subtask_states_for_test() -> None:
    with _subtask_next_lock:
        _subtask_states.clear()


def clear_subtask_states(task_id: int) -> None:
    """清理一个已结束或被停止任务遗留的 SubTask 状态。"""
    with _subtask_next_lock:
        keys = [key for key in _subtask_states if key[0] == task_id]
        for key in keys:
            del _subtask_states[key]


@AgentServer.custom_action("TimeWindowRoute")
class TimeWindowRoute(CustomAction):
    """按北京时间动态选择节点，适合限时活动和固定开放时段。

    参数：decision_node、start、end、active_node、inactive_node。
    start/end 使用 ISO 8601；未带时区时按北京时间解释，带时区时转换为北京时间。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            p = _parse_params(argv.custom_action_param)
            decision = str(p["decision_node"])
            start = _to_beijing_time(p["start"])
            end = _to_beijing_time(p["end"])
            active = str(p["active_node"])
            inactive = str(p["inactive_node"])
            if end <= start:
                raise ValueError("end 必须晚于 start")
            now = datetime.now(BEIJING_TIMEZONE)
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("TimeWindowRoute: %s", exc)
            return CustomAction.RunResult(success=False)

        target = active if start <= now < end else inactive
        context.override_next(decision, [target])
        logger.debug("时间窗口路由 %s -> %s", decision, target)
        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("OCRLog")
class OCRLog(CustomAction):
    """运行指定 OCR 节点并将识别文本写入日志。参数：recognition。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            p = _parse_params(argv.custom_action_param)
            node = str(p["recognition"])
        except (KeyError, ValueError) as exc:
            logger.error("OCRLog: %s", exc)
            return CustomAction.RunResult(success=False)
        image = context.tasker.controller.post_screencap().wait().get()
        detail: Any = context.run_recognition(node, image)
        results = getattr(detail, "filtered_results", None) or []
        texts = [str(getattr(item, "text", "")).strip() for item in results]
        texts = [text for text in texts if text]
        logger.info("OCRLog[%s]: %s", node, " | ".join(texts) if texts else "<未识别>")
        return CustomAction.RunResult(success=True)
