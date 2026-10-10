"""Named teammate selection and settlement-gated team battle accounting."""
import logging
import re
import time

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from custom.action.daily import battle, click, swipe
from custom.action.target_count import _parse_params, get_runtime_params
from custom.navigation import act, hit, pause, screenshot

logger = logging.getLogger("MaaOnmyoji.team")


def teammate():
    name = get_runtime_params().get("teammate", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("组队模式必须填写完整队友昵称")
    return name.strip()


def named(context, node, image, name):
    return context.run_recognition(node, image, {
        node: {"recognition": {"param": {"expected": "^" + re.escape(name) + "$"}}}
    })


@AgentServer.custom_action("SelectTeammate")
class SelectTeammate(CustomAction):
    def run(self, context, argv):
        try:
            name = teammate()
        except ValueError as exc:
            logger.error("%s", exc)
            return CustomAction.RunResult(success=False)
        for index in range(8):
            image = screenshot(context)
            if image is None or context.tasker.stopping:
                break
            if not hit(context.run_recognition("PageTeamInvite", image)):
                break
            detail = named(context, "MitamaTeammateName", image, name)
            if hit(detail):
                x, y, w, h = detail.box
                return CustomAction.RunResult(success=click(context, x + w // 2, y + h // 2))
            if index < 7 and not swipe(context, (858, 469), (858, 224)):
                break
        logger.error("邀请列表未找到指定队友：%s", name)
        return CustomAction.RunResult(success=False)


@AgentServer.custom_action("WaitLeaderInvitation")
class WaitLeaderInvitation(CustomAction):
    def run(self, context, argv):
        try:
            name = teammate()
        except ValueError as exc:
            logger.error("%s", exc)
            return CustomAction.RunResult(success=False)
        deadline = time.monotonic() + 180
        while not context.tasker.stopping and time.monotonic() < deadline:
            image = screenshot(context)
            if image is None:
                break
            # Both invitation text and the exact configured name must match.
            if hit(context.run_recognition("MitamaInvitationText", image)) and hit(named(context, "MitamaInvitationName", image, name)):
                if act(context, "MitamaAcceptInvite", image):
                    return CustomAction.RunResult(success=True)
            if not pause(context, 1):
                break
        logger.error("等待指定队长邀请超时：%s", name)
        return CustomAction.RunResult(success=False)


@AgentServer.custom_action("TeamBattle")
class TeamBattle(CustomAction):
    def run(self, context, argv):
        try:
            name = teammate()
            role = _parse_params(argv.custom_action_param).get("role", "leader")
            if role not in {"leader", "member"}:
                raise ValueError("未知组队身份")
        except ValueError as exc:
            logger.error("%s", exc)
            return CustomAction.RunResult(success=False)
        if role == "leader":
            deadline = time.monotonic() + 180
            while not context.tasker.stopping and time.monotonic() < deadline:
                image = screenshot(context)
                if image is None:
                    return CustomAction.RunResult(success=False)
                if hit(context.run_recognition("PageTeamRoom", image)) and hit(named(context, "MitamaRoomTeammate", image, name)):
                    if not act(context, "MitamaTeamStart", image):
                        return CustomAction.RunResult(success=False)
                    break
                if not pause(context, 1):
                    return CustomAction.RunResult(success=False)
            else:
                logger.error("等待队友进入房间超时：%s", name)
                return CustomAction.RunResult(success=False)
        result = battle(context, "TeamRoom")
        if result == "exhausted":
            return CustomAction.RunResult(success=context.override_next(argv.node_name, ["MitamaFinish"]))
        # A waiting room never counts as a completed battle without settlement.
        return CustomAction.RunResult(success=result == "success")
