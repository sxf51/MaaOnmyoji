"""启动流程的主界面时间校验与玩家昵称缓存。"""

from __future__ import annotations

import ctypes
import logging
import re
import threading
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_recognition import CustomRecognition
from maa.define import (
    MaaBool,
    MaaControllerHandle,
    MaaCtrlId,
    MaaStringBufferHandle,
    RectType,
)
from maa.library import Library

logger = logging.getLogger("MaaOnmyoji.startup")

_TIME_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s*[:：.]\s*(\d{2})(?!\d)")
_nickname_by_controller: dict[str, str] = {}
_nickname_lock = threading.Lock()


def _ensure_shell_api_signatures() -> None:
    """补齐 maafw Python binding 5.11.1 遗漏的两个 Shell API 签名。"""
    framework = Library.framework()
    framework.MaaControllerPostShell.restype = MaaCtrlId
    framework.MaaControllerPostShell.argtypes = [
        MaaControllerHandle,
        ctypes.c_char_p,
        ctypes.c_int64,
    ]
    framework.MaaControllerGetShellOutput.restype = MaaBool
    framework.MaaControllerGetShellOutput.argtypes = [
        MaaControllerHandle,
        MaaStringBufferHandle,
    ]


def _recognition_text(detail: Any) -> str | None:
    if detail is None or not getattr(detail, "hit", False):
        return None
    best = getattr(detail, "best_result", None)
    text = getattr(best, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    for result in getattr(detail, "filtered_results", None) or []:
        text = getattr(result, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _parse_minutes(text: str) -> int | None:
    match = _TIME_PATTERN.search(text)
    if match is None:
        return None
    hour, minute = (int(value) for value in match.groups())
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _minute_distance(left: int, right: int) -> int:
    difference = abs(left - right)
    return min(difference, 24 * 60 - difference)


def _match_device_time(ocr_text: str, device_minutes: int) -> tuple[int, int] | None:
    """Match device time, plus or minus one minute, within raw OCR text."""
    normalized_text = ocr_text.replace("：", ":")
    for offset in (0, -1, 1):
        candidate_minutes = (device_minutes + offset) % (24 * 60)
        hour, minute = divmod(candidate_minutes, 60)
        candidates = (f"{hour:02d}:{minute:02d}", f"{hour}:{minute:02d}")
        if any(candidate in normalized_text for candidate in candidates):
            return candidate_minutes, abs(offset)
    return None


def get_cached_nickname(controller_uuid: str) -> str | None:
    """读取当前控制器在本次 Agent 运行期间缓存的玩家昵称。"""
    with _nickname_lock:
        return _nickname_by_controller.get(controller_uuid)


def reset_startup_identity_cache_for_test() -> None:
    with _nickname_lock:
        _nickname_by_controller.clear()


@AgentServer.custom_recognition("StartupHomeIdentity")
class StartupHomeIdentity(CustomRecognition):
    """校验游戏时间，并为每个控制器缓存一次玩家昵称。"""

    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> CustomRecognition.AnalyzeResult | RectType | None:
        time_detail = context.run_recognition("StartupHomeTimeOCR", argv.image)
        ocr_time_text = _recognition_text(time_detail)
        if ocr_time_text is None:
            logger.debug("StartupHome 时间 OCR 无效：%r", ocr_time_text)
            return None

        controller = context.tasker.controller
        try:
            _ensure_shell_api_signatures()
            date_job = controller.post_shell("date +%H:%M", timeout=3000)
            date_job.wait()
            if not date_job.succeeded:
                logger.warning("StartupHome 获取设备时间失败")
                return None
            device_time_text = date_job.get().strip()
        except (
            AttributeError,
            ctypes.ArgumentError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning("StartupHome 获取设备时间异常：%s", exc)
            return None
        device_minutes = _parse_minutes(device_time_text)
        if device_minutes is None:
            logger.warning("StartupHome 设备时间无效：%r", device_time_text)
            return None

        matched_time = _match_device_time(ocr_time_text, device_minutes)
        if matched_time is None:
            logger.debug(
                "StartupHome 时间不匹配：OCR=%s，设备=%s（允许前后 1 分钟）",
                ocr_time_text,
                device_time_text,
            )
            return None

        _, difference = matched_time

        controller_uuid = str(controller.uuid)
        nickname = get_cached_nickname(controller_uuid)
        if nickname is None:
            nickname_detail = context.run_recognition(
                "StartupHomeNicknameOCR", argv.image
            )
            nickname = _recognition_text(nickname_detail)
            if nickname is None:
                logger.debug("StartupHome 昵称 OCR 失败")
                return None
            with _nickname_lock:
                nickname = _nickname_by_controller.setdefault(
                    controller_uuid, nickname
                )
            logger.info("已缓存玩家昵称：%s", nickname)

        box = getattr(time_detail, "box", None) or [198, 5, 67, 22]
        return CustomRecognition.AnalyzeResult(
            box=list(box),
            detail={
                "time": ocr_time_text,
                "device_time": device_time_text,
                "minute_difference": difference,
                "nickname_cached": True,
            },
        )
