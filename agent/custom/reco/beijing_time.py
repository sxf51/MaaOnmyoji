"""Hit only inside a Beijing-time (UTC+8) window, for game rules tied to the server clock."""
import logging
from datetime import datetime, timedelta, timezone

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from custom.action.target_count import _parse_params

logger = logging.getLogger("MaaOnmyoji.beijing_time")

BEIJING = timezone(timedelta(hours=8))


def in_window(now, start_hour, end_hour):
    """Half-open [start, end) in hours; a window with start > end wraps past midnight."""
    hour = now.astimezone(BEIJING).hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


@AgentServer.custom_recognition("BeijingTimeWindow")
class BeijingTimeWindow(CustomRecognition):
    """Params: start_hour, end_hour (0-23, end exclusive). Uses the host clock converted to UTC+8."""

    def analyze(self, context, argv):
        try:
            params = _parse_params(argv.custom_recognition_param)
            start, end = int(params["start_hour"]), int(params["end_hour"])
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError("小时须在0-23之间")
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("北京时间窗口参数无效：%s", exc)
            return None
        if not in_window(datetime.now(timezone.utc), start, end):
            return None
        return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"start_hour": start, "end_hour": end})
