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
    return detail is not None and bool(getattr(detail, "hit", False)) and getattr(detail, "box", None) is not None


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

        hits, boxes = [], []
        for node in nodes:
            detail = context.run_recognition(str(node), argv.image)
            matched = _hit(detail)
            hits.append(matched)
            if logic == "AND" and not matched:
                return None
            if matched:
                boxes.append(_box(detail))
                if logic == "OR" and return_mode != "union":
                    break
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


def _clip_roi(value, width, height, offset=None):
    """Resolve a fixed rectangle against the supplied image, never a new screenshot."""
    if value is None or value is True:
        value = [0, 0, width, height]
    if isinstance(value, (str, bool)):
        raise ValueError("颜色 OCR 的裁剪区域必须是矩形，不能引用动态 ROI")
    values = list(value)
    if len(values) != 4 or not all(isinstance(v, (int, np.integer)) and not isinstance(v, bool) for v in values):
        raise ValueError("roi 必须为四个整数 [x, y, width, height]")
    if values == [0, 0, 0, 0]:
        values = [0, 0, width, height]
    if offset is not None:
        if len(offset) != 4 or not all(isinstance(v, (int, np.integer)) and not isinstance(v, bool) for v in offset):
            raise ValueError("roi_offset 必须为四个整数")
        values = [v + delta for v, delta in zip(values, offset)]
    x, y, w, h = map(int, values)
    if w <= 0 or h <= 0:
        raise ValueError("roi 宽高必须为正数")
    left, top, right, bottom = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
    if right <= left or bottom <= top:
        raise ValueError("roi 与当前截图没有交集")
    return [left, top, right - left, bottom - top]


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
            if not all(isinstance(v, (int, np.integer)) and not isinstance(v, bool) and 0 <= v <= 255 for v in color):
                raise ValueError("target_color 通道必须为 0..255 的整数")
            if not 0 <= tolerance <= 255:
                raise ValueError("tolerance 必须在 0..255")
            if order not in {"RGB", "BGR"}:
                raise ValueError("color_order 仅支持 RGB/BGR")
            image = np.asarray(argv.image)
            if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
                raise ValueError("需要非空 BGR 三通道截图")
            height, width = image.shape[:2]
            x, y, w, h = _clip_roi(getattr(argv, "roi", None), width, height)
            node_data = context.get_node_data(node)
            if not isinstance(node_data, dict):
                raise ValueError("被引用的 OCR 节点不存在")
            reco = node_data.get("recognition", {})
            if isinstance(reco, dict) and reco.get("type") == "OCR":
                param = reco.get("param", {})
                nx, ny, nw, nh = _clip_roi(param.get("roi"), width, height, param.get("roi_offset"))
                right, bottom = min(x + w, nx + nw), min(y + h, ny + nh)
                x, y = max(x, nx), max(y, ny)
                w, h = right - x, bottom - y
                if w <= 0 or h <= 0:
                    raise ValueError("custom roi 与 OCR 节点区域没有交集")
            else:
                raise ValueError("颜色 OCR 必须引用原生 OCR 节点")
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("ColorOCR: %s", exc)
            return None

        target = np.asarray(color if order == "BGR" else list(reversed(color)), dtype=np.int16)
        cropped = np.ascontiguousarray(image[y:y+h, x:x+w])
        delta = np.abs(cropped.astype(np.int16) - target)
        mask = np.all(delta <= tolerance, axis=-1)
        processed = np.full_like(cropped, 255, dtype=np.uint8)
        processed[mask] = 0
        # run_recognition's override belongs to a cloned context. Local crop
        # coordinates must not replace the reusable node's global ROI.
        override = {node: {"recognition": {"param": {"roi": [0, 0, w, h], "roi_offset": [0, 0, 0, 0]}}}}
        detail = context.run_recognition(node, processed, override)
        filtered = True
        if not _hit(detail) and self.fallback:
            detail = context.run_recognition(node, cropped, override)
            filtered = False
        if not _hit(detail):
            return None
        bx, by, bw, bh = _box(detail)
        if bx < 0 or by < 0 or bw <= 0 or bh <= 0 or bx + bw > w or by + bh > h:
            return None
        return CustomRecognition.AnalyzeResult(box=[bx+x, by+y, bw, bh], detail={"filtered": filtered, "roi": [x, y, w, h]})


@AgentServer.custom_recognition("ColorOCR")
class ColorOCR(_ColorOCRBase):
    """按目标颜色二值化后运行已有 OCR 节点。"""


@AgentServer.custom_recognition("ColorOCRWithFallback")
class ColorOCRWithFallback(_ColorOCRBase):
    """颜色 OCR 失败后使用原图再次 OCR。"""

    fallback = True
