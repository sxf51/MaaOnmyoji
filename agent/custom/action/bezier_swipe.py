"""使用连续触摸事件执行贝塞尔曲线滑动。"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from cBezier import bezier_points, swipe_control_points
from .target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.bezier_swipe")


def _fixed_point(value: Any, field: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field} 必须是 [x, y]")
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是整数坐标") from exc


def _point(value: Any, field: str, rng: random.Random) -> tuple[int, int]:
    """解析固定坐标，或在线段/矩形区域内均匀随机选择一个坐标。"""
    if isinstance(value, dict):
        line = value.get("line")
        if not isinstance(line, (list, tuple)) or len(line) != 2:
            raise ValueError(f"{field}.line 必须是 [[x1, y1], [x2, y2]]")
        start = _fixed_point(line[0], f"{field}.line[0]")
        end = _fixed_point(line[1], f"{field}.line[1]")
        ratio = rng.random()
        return (
            int(round(start[0] + (end[0] - start[0]) * ratio)),
            int(round(start[1] + (end[1] - start[1]) * ratio)),
        )
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            x, y, width, height = map(int, value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 区域必须是 [x, y, width, height]") from exc
        if width < 0 or height < 0:
            raise ValueError(f"{field} 区域的 width 和 height 不能为负数")
        return rng.randint(x, x + width), rng.randint(y, y + height)
    return _fixed_point(value, field)


def build_track(params: dict[str, Any]) -> tuple[list[tuple[int, int]], int, int, int]:
    """解析动作参数并返回轨迹、总时长、触点编号和压力。"""
    rng = random.Random(params.get("random_seed"))
    begin = _point(params.get("begin"), "begin", rng)
    end = _point(params.get("end"), "end", rng)
    segments = int(params.get("segments", 20))
    duration = int(params.get("duration", 500))
    duration_random = int(params.get("duration_random", 0))
    contact = int(params.get("contact", 0))
    pressure = int(params.get("pressure", 1))
    if not 2 <= segments <= 200:
        raise ValueError("segments 必须在 2 到 200 之间")
    if duration <= 0:
        raise ValueError("duration 必须是正整数")
    if duration_random < 0:
        raise ValueError("duration_random 不能为负数")
    duration += rng.randint(0, duration_random)

    supplied = params.get("control_points")
    if supplied is not None:
        if not isinstance(supplied, list):
            raise ValueError("control_points 必须是坐标数组")
        controls = [begin, *[_fixed_point(p, "control_points") for p in supplied], end]
    else:
        deviation = float(params.get("deviation", 30))
        bend = float(params.get("bend", 1))
        controls = swipe_control_points(begin, end, deviation, bend)

    track = bezier_points(controls, segments, bool(params.get("easing", True)))
    return track, duration, contact, pressure


@AgentServer.custom_action("BezierSwipe")
class BezierSwipe(CustomAction):
    """把贝塞尔曲线采样为多次 TouchMove，并在全程保持同一触点按下。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            track, duration, contact, pressure = build_track(_parse_params(argv.custom_action_param))
        except (TypeError, ValueError) as exc:
            logger.error("BezierSwipe: %s", exc)
            return CustomAction.RunResult(success=False)

        controller = context.tasker.controller
        interval = duration / (len(track) - 1) / 1000
        success = True
        pressed = False
        try:
            job = controller.post_touch_down(*track[0], contact, pressure).wait()
            pressed = True
            success = not job.failed
            for x, y in track[1:]:
                if not success:
                    break
                time.sleep(interval)
                success = not controller.post_touch_move(x, y, contact, pressure).wait().failed
        except Exception:
            logger.exception("BezierSwipe 执行失败")
            success = False
        finally:
            if pressed:
                try:
                    success = not controller.post_touch_up(contact).wait().failed and success
                except Exception:
                    logger.exception("BezierSwipe 释放触点失败")
                    success = False
        return CustomAction.RunResult(success=success)
