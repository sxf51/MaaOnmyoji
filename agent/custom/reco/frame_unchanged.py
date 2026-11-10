"""Tell whether an input changed the screen, for lists that give no end-of-list signal."""
import logging

import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from custom.action.target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.frame_unchanged")

# Pixels count as changed above this per-channel difference; lower values pick up compression noise.
COLOR_DELTA = 30

_remembered = {"task_id": None, "image": None}


def changed_ratio(before, after, region):
    x, y, w, h = region
    first = before[y:y + h, x:x + w].astype(np.int16)
    second = after[y:y + h, x:x + w].astype(np.int16)
    return float((np.abs(first - second).max(axis=2) > COLOR_DELTA).mean())


@AgentServer.custom_action("RememberFrame")
class RememberFrame(CustomAction):
    """Keep the frame just recognized as the baseline for the input that follows."""

    def run(self, context, argv):
        image = context.tasker.controller.cached_image
        _remembered["task_id"] = argv.task_detail.task_id
        _remembered["image"] = None if image is None else image.copy()
        return CustomAction.RunResult(success=True)


@AgentServer.custom_recognition("FrameUnchanged")
class FrameUnchanged(CustomRecognition):
    """Hit only if every region stayed below max_changed_ratio since RememberFrame."""

    def analyze(self, context, argv):
        before = _remembered["image"]
        if before is None or _remembered["task_id"] != argv.task_detail.task_id:
            return None
        if before.shape != argv.image.shape:
            return None
        try:
            params = _parse_params(argv.custom_recognition_param)
            regions, limit = params["regions"], params["max_changed_ratio"]
            if any(changed_ratio(before, argv.image, region) > limit for region in regions):
                return None
            return CustomRecognition.AnalyzeResult(box=list(regions[0]), detail={})
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("画面比较参数无效：%s", exc)
            return None
