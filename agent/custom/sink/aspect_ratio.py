"""任务开始时检查控制器画面比例"""

from __future__ import annotations

import logging
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.event_sink import NotificationType
from maa.tasker import Tasker, TaskerEventSink

logger = logging.getLogger("MaaOnmyoji.aspect_ratio")


def get_resolution(controller: Any) -> tuple[int, int]:
    if controller is None:
        return (0, 0)
    try:
        if controller.cached_image is None:
            controller.post_screencap().wait().get()
        width, height = controller.resolution
        return int(width), int(height)
    except Exception as exc:
        logger.warning("读取控制器分辨率失败：%s", exc)
        return (0, 0)


def is_16_by_9(width: int, height: int, tolerance: float = 0.01) -> bool:
    if width <= 0 or height <= 0:
        return False
    return abs(width / height - 16 / 9) <= tolerance


@AgentServer.tasker_sink()
class AspectRatioChecker(TaskerEventSink):
    def on_tasker_task(
        self,
        tasker: Tasker,
        noti_type: NotificationType,
        detail: TaskerEventSink.TaskerTaskDetail,
    ) -> None:
        if noti_type != NotificationType.Starting or detail.entry == "MaaTaskerPostStop":
            return
        width, height = get_resolution(tasker.controller)
        if not width or not height:
            logger.warning("无法检查任务 %s 的分辨率", detail.entry)
            raise RuntimeError(f"无法检查任务 {detail.entry} 的分辨率")
        elif not is_16_by_9(width, height):
            logger.warning("当前画面 %dx%d 不是 16:9", width, height)
            raise RuntimeError(f"当前画面 {width}x{height} 不是 16:9")
