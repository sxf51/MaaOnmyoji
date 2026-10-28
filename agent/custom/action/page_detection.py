"""Read-only page logging entry point."""
import logging

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from custom.page_state import detect_page, screenshot


@AgentServer.custom_action("DetectPage")
class DetectPage(CustomAction):
    def run(self, context, argv):
        image = screenshot(context)
        if image is None:
            return CustomAction.RunResult(success=False)
        page = detect_page(context, image)
        logging.getLogger("MaaOnmyoji.page_detection").info("当前页面：%s", page)
        return CustomAction.RunResult(success=page != "Unknown")
