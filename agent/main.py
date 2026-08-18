import sys

# 导入模块即完成自定义动作注册。
from custom.action import (  # noqa: F401
    bezier_swipe,
    drop_statistics,
    general,
    target_count,
)
from custom.action.drop_statistics import recover_pending_reports, shutdown_reporter
from custom.reco import general as general_reco  # noqa: F401
from custom.reco import startup as startup_reco  # noqa: F401
from custom.sink import (
    aspect_ratio,  # noqa: F401
    subtask_state,  # noqa: F401
)
from maa.agent.agent_server import AgentServer
from maa.define import LoggingLevelEnum
from maa.tasker import Tasker
from utils import logger


def main():
    Tasker.set_log_dir("./debug")
    Tasker.set_stdout_level(LoggingLevelEnum.Info)

    if len(sys.argv) < 2:
        logger.error("Usage: python main.py <socket_id>")
        logger.error("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]

    logger.info("AgentServer 正在启动")
    AgentServer.start_up(socket_id)
    recover_pending_reports()
    try:
        logger.info("AgentServer 已启动")
        AgentServer.join()
    finally:
        shutdown_reporter(timeout=10.0)
        AgentServer.shut_down()
        logger.info("AgentServer 已停止")


if __name__ == "__main__":
    main()
