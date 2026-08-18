"""从 M9A 迁移的通用组合识别与图像预处理识别。"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_recognition import CustomRecognition
from maa.define import RectType

from custom.action.target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.recognition")


def _hit(detail: Any) -> bool:
    return detail is not None and getattr(detail, "box", None) is not None


def _box(detail: Any) -> list[int] | None:
    value = getattr(detail, "box", None)
    return list(value) if value is not None else None


def _union(boxes: list[list[int]]) -> list[int]:
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return [left, top, right - left, bottom - top]


@AgentServer.custom_recognition("CheckStopping")
class CheckStopping(CustomRecognition):
    """任务收到停止请求时命中。"""

    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> CustomRecognition.AnalyzeResult | RectType | None:
        del argv
        if context.tasker.stopping:
            return CustomRecognition.AnalyzeResult(box=[0, 0, 0, 0], detail={"stopping": True})
        return None


@AgentServer.custom_recognition("MultiRecognition")
class MultiRecognition(CustomRecognition):
    """组合运行多个已有识别节点。

    参数：nodes、logic(`AND`/`OR`)、return(`first`/`union`/固定 ROI)、offset。
    """

    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> CustomRecognition.AnalyzeResult | RectType | None:
        try:
            p = _parse_params(argv.custom_recognition_param)
            nodes = p.get("nodes")
            logic = str(p.get("logic", "AND")).upper()
            return_mode = p.get("return", "first")
            offset = p.get("offset", [0, 0, 0, 0])
            if not isinstance(nodes, list) or not nodes:
                raise ValueError("nodes 必须是非空列表")
            if logic not in {"AND", "OR"}:
                raise ValueError("logic 仅支持 AND 或 OR")
            if not isinstance(offset, list) or len(offset) != 4:
                raise ValueError("offset 必须为 [dx, dy, dw, dh]")
        except ValueError as exc:
            logger.error("MultiRecognition: %s", exc)
            return None

        details = [context.run_recognition(str(node), argv.image) for node in nodes]
        hits = [_hit(detail) for detail in details]
        if (logic == "AND" and not all(hits)) or (logic == "OR" and not any(hits)):
            return None
        boxes = [box for detail in details if (box := _box(detail)) is not None]
        if not boxes:
            return None
        if isinstance(return_mode, list) and len(return_mode) == 4:
            result = [int(v) for v in return_mode]
        elif return_mode == "union":
            result = _union(boxes)
        else:
            result = boxes[0]
        result = [result[i] + int(offset[i]) for i in range(4)]
        return CustomRecognition.AnalyzeResult(box=result, detail={"hits": hits})


class _ColorOCRBase(CustomRecognition):
    fallback = False

    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> CustomRecognition.AnalyzeResult | RectType | None:
        try:
            p = _parse_params(argv.custom_recognition_param)
            color = p.get("target_color", [255, 255, 255])
            tolerance = int(p.get("tolerance", 55))
            node = str(p["recognition"])
            order = str(p.get("color_order", "RGB")).upper()
            if not isinstance(color, list) or len(color) != 3:
                raise ValueError("target_color 必须包含三个通道")
            if not 0 <= tolerance <= 255:
                raise ValueError("tolerance 必须在 0..255")
            if order not in {"RGB", "BGR"}:
                raise ValueError("color_order 仅支持 RGB/BGR")
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("ColorOCR: %s", exc)
            return None

        target = np.asarray(color if order == "BGR" else list(reversed(color)), dtype=np.int16)
        image = np.asarray(argv.image)
        delta = np.abs(image.astype(np.int16) - target)
        mask = np.all(delta <= tolerance, axis=-1)
        processed = np.full_like(image, 255, dtype=np.uint8)
        processed[mask] = 0
        detail = context.run_recognition(node, processed)
        if not _hit(detail) and self.fallback:
            detail = context.run_recognition(node, image)
        if not _hit(detail):
            return None
        return CustomRecognition.AnalyzeResult(box=_box(detail), detail={"filtered": True})


@AgentServer.custom_recognition("ColorOCR")
class ColorOCR(_ColorOCRBase):
    """按目标颜色二值化后运行已有 OCR 节点。"""


@AgentServer.custom_recognition("ColorOCRWithFallback")
class ColorOCRWithFallback(_ColorOCRBase):
    """颜色 OCR 失败后使用原图再次 OCR。"""

    fallback = True
