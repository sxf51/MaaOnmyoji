"""Pipeline entry points for page inspection and navigation."""
import logging

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from custom.action.target_count import _parse_params
from custom.navigation import detect_page, navigate, screenshot


@AgentServer.custom_action("NavigateTo")
class NavigateTo(CustomAction):
    def run(self, context, argv):
        try:
            params = _parse_params(argv.custom_action_param)
            targets = params["targets"]
            if not isinstance(targets, list) or not all(isinstance(p, str) for p in targets):
                raise ValueError("targets 必须为页面名称列表")
            timeout = float(params.get("timeout", 60))
            steps = int(params.get("max_steps", 24))
            if not 0 < timeout <= 300 or not 0 < steps <= 100:
                raise ValueError("导航时限或步数无效")
        except (KeyError, TypeError, ValueError) as exc:
            logging.getLogger(__name__).error("NavigateTo: %s", exc)
            return CustomAction.RunResult(success=False)
        return CustomAction.RunResult(success=navigate(context, targets, timeout, steps))


@AgentServer.custom_action("DetectPage")
class DetectPage(CustomAction):
    def run(self, context, argv):
        image = screenshot(context)
        if image is None:
            return CustomAction.RunResult(success=False)
        page = detect_page(context, image)
        logging.getLogger("MaaOnmyoji.navigation").info("当前页面：%s", page)
        return CustomAction.RunResult(success=page != "Unknown")
