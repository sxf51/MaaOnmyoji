"""Check actual touch areas on the image used to recognize the target."""
from __future__ import annotations

import logging
import re

from maa.pipeline import JOCR
from custom.page_state import hit, screenshot

logger = logging.getLogger("MaaOnmyoji.input_safety")

# Each predicate also requires its specific non-purchase prompt, not just 确定.
CONFIRM_TARGETS = frozenset({
    "StartupUpdateConfirm", "MitamaConfirmPreset", "TeamPresetConfirm",
    "NavConfirmExitExplore", "NativeNavConfirmExitExplore",
    "NavConfirmExitTeam", "NativeNavConfirmExitTeam",
    "DailyMailClaimConfirm", "MitamaTeamStartConfirm",
    "RealmRaidRetreatConfirm", "RealmRaidRetreatAgainConfirm",
})
PURCHASE_WORDS = re.compile(r"购|充值|支付|付费|兑换|勾玉|魂玉|售价|价格")
CONFIRM_WORDS = re.compile(r"确认|确定")


def stop_input(context, reason):
    logger.error("%s，停止输入", reason)
    context.tasker.post_stop()  # Do not wait on the tasker thread.
    return False


def rectangle(value):
    values = list(value)
    if len(values) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
        raise ValueError("输入区域必须为矩形")
    x, y, w, h = map(int, values)
    if w <= 0 or h <= 0:
        raise ValueError("输入区域不能为空")
    return [x, y, w, h]


def action_region(node, matched_box):
    """Resolve the whole random click range, including target_offset."""
    action = node.get("action", "Click")
    params = action.get("param", {}) if isinstance(action, dict) else {}
    target = params.get("target", True)
    if target is True:
        target = matched_box
    if isinstance(target, (str, bool)):
        raise ValueError("无法解析输入目标区域")
    region = rectangle(target)
    offset = params.get("target_offset", [0, 0, 0, 0])
    if not isinstance(offset, (list, tuple)) or len(offset) != 4:
        raise ValueError("target_offset 必须为四元组")
    return rectangle([value + delta for value, delta in zip(region, offset)])


def regions_safe(context, image, regions, target_node):
    """Read only touch rectangles and nearby button labels; blank areas are valid."""
    if context.tasker.stopping:
        return False
    try:
        height, width = image.shape[:2]
        for value in regions:
            x, y, w, h = rectangle(value)
            if x < 0 or y < 0 or x + w > width or y + h > height:
                raise ValueError("输入范围超出截图")
            # Include a label when the click range touches its button's edge.
            left, top = max(0, x - 80), max(0, y - 40)
            right, bottom = min(width, x + w + 80), min(height, y + h + 40)
            crop = image[top:bottom, left:right].copy()
            detail = context.run_recognition_direct("OCR", JOCR(expected=[]), crop)
            if detail is None:
                return stop_input(context, "点击区域 OCR 无法执行")
            for result in detail.all_results:
                text = result.text
                if not (PURCHASE_WORDS.search(text) or CONFIRM_WORDS.search(text)):
                    continue
                bx, by, bw, bh = result.box
                # Keep the whole random range outside the label-to-button envelope.
                bx, by = bx + left - 80, by + top - 24
                bw, bh = bw + 160, bh + 48
                intersects = x <= bx + bw and bx <= x + w and y <= by + bh and by <= y + h
                if not intersects:
                    continue
                if PURCHASE_WORDS.search(text) or target_node not in CONFIRM_TARGETS:
                    return stop_input(context, f"输入范围可能覆盖危险按钮：{text}")
        return True
    except (AttributeError, RuntimeError, ValueError, OSError, TypeError) as exc:
        return stop_input(context, f"无法核验输入范围：{exc}")


def ensure_input_safe(context, image=None, target_node=None, regions=None):
    """Revalidate a gesture's page and actual track after popup handling."""
    try:
        if not target_node or not regions:
            return stop_input(context, "缺少滑动目标或触摸范围")
        for attempt in range(2):
            if context.tasker.stopping:
                return False
            if image is None:
                image = screenshot(context)
            if image is None:
                return stop_input(context, "截图失败")
            bounty = context.run_recognition("CommonRefuseBounty", image)
            if bounty is None:
                return stop_input(context, "协作邀请检查失败")
            if bounty.hit:
                if attempt:
                    return stop_input(context, "协作邀请仍遮挡触摸目标")
                result = context.run_task("CommonBountyInterrupt")
                if not getattr(getattr(result, "status", None), "succeeded", False):
                    return stop_input(context, "协作邀请未能关闭")
                image = None
                continue
            public = target_node.removeprefix("SafeInput_")
            if not hit(context.run_recognition(public, image)):
                return stop_input(context, "滑动前页面目标已改变")
            return regions_safe(context, image, regions, public)
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        return stop_input(context, f"输入目标复核失败：{exc}")
    return False
