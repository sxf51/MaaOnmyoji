from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from custom.action.target_count import _parse_params
from custom.page_state import detect_page


@AgentServer.custom_recognition("CurrentPage")
class CurrentPage(CustomRecognition):
    def analyze(self, context, argv):
        try:
            params = _parse_params(argv.custom_recognition_param)
        except ValueError:
            return None
        page = detect_page(context, argv.image)
        if page == "Unknown" or page not in params.get("pages", [page]):
            return None
        return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"page": page})
