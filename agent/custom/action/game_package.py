"""按设备上实际安装的包选择阴阳师包名（参考 OAS detect_package）。

启动/关闭本身仍由原生 StartApp / StopApp 完成；这里只把选出的包名写进对应节点。
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.game_package")

PREFIX = "com.netease.onmyoji"
OFFICIAL = "com.netease.onmyoji.wyzymnqsd_cps"
# 多个候选时的优先顺序：官服模拟器包、网易官方包，其余按名称排序。
PREFERRED_ORDER = (OFFICIAL, PREFIX)
_ACTIONS = ("StartApp", "StopApp")


def parse_packages(output: str) -> list[str]:
    """解析 `pm list packages` 的输出。"""
    return [line.split(":", 1)[1].strip() for line in output.splitlines() if line.strip().startswith("package:")]


def matching_packages(installed: Iterable[str], prefix: str = PREFIX) -> list[str]:
    """包名等于前缀或以“前缀.”开头的都算阴阳师包。"""
    pattern = re.compile(re.escape(prefix) + r"(\..+)?")
    return sorted({name for name in installed if pattern.fullmatch(name)})


def choose_package(installed: Iterable[str], preferred: str = "", prefix: str = PREFIX) -> tuple[str, str]:
    """返回 (包名, 原因)。

    选了渠道且已安装就用它；未选择或没装就在已安装包里匹配阴阳师前缀；
    一个都没找到（或读不到包列表）时退回所选渠道，未选择则用官服包，让原生启动去报错。
    """
    installed = set(installed)
    if preferred and preferred in installed:
        return preferred, "selected"
    matches = matching_packages(installed, prefix)
    if matches:
        for name in PREFERRED_ORDER:
            if name in matches:
                return name, "matched"
        return matches[0], "matched"
    return preferred or OFFICIAL, "fallback"


@AgentServer.custom_action("ResolveGamePackage")
class ResolveGamePackage(CustomAction):
    """读取设备包列表，选出包名后覆盖目标节点的 StartApp / StopApp 包名。

    参数：{"target": 节点名, "action": "StartApp" 或 "StopApp"}；所选渠道来自本节点 attach.package。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            params = _parse_params(argv.custom_action_param)
            target, action = params["target"], params["action"]
            if not isinstance(target, str) or not target or action not in _ACTIONS:
                raise ValueError("target 必须是节点名，action 必须是 StartApp 或 StopApp")
        except (KeyError, ValueError) as exc:
            logger.error("ResolveGamePackage 参数无效：%s", exc)
            return CustomAction.RunResult(success=False)

        attach = (context.get_node_data(argv.node_name) or {}).get("attach") or {}
        preferred = str(attach.get("package") or "").strip()
        try:
            job = context.tasker.controller.post_shell("pm list packages").wait()
            output = (job.get() or "") if job.succeeded else ""
        except Exception as exc:  # 非 ADB 控制器或命令失败：按所选渠道/官服包继续
            logger.warning("读取设备包列表失败，按所选渠道启动：%s", exc)
            output = ""
        installed = parse_packages(output)
        package, reason = choose_package(installed, preferred)
        candidates = matching_packages(installed)
        if reason == "matched" and preferred:
            logger.warning("所选渠道包 %s 未安装，改用已安装的 %s", preferred, package)
        if reason == "matched" and len(candidates) > 1:
            logger.warning("设备上有多个阴阳师包 %s，使用 %s；需要别的请在任务选项里选择渠道", candidates, package)
        if reason == "fallback":
            logger.warning("设备上没有找到 %s 开头的包，仍尝试 %s", PREFIX, package)
        logger.info("阴阳师包名：%s", package)
        ok = context.override_pipeline({target: {"action": {"type": action, "param": {"package": package}}}})
        return CustomAction.RunResult(success=bool(ok))
