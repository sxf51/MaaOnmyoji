"""任务结束时清理 SubTask 的进程内状态。"""

from maa.agent.agent_server import AgentServer
from maa.event_sink import NotificationType
from maa.tasker import Tasker, TaskerEventSink

from custom.action.general import clear_subtask_states


@AgentServer.tasker_sink()
class SubTaskStateCleaner(TaskerEventSink):
    def on_tasker_task(
        self,
        tasker: Tasker,
        noti_type: NotificationType,
        detail: TaskerEventSink.TaskerTaskDetail,
    ) -> None:
        del tasker
        if noti_type in (NotificationType.Succeeded, NotificationType.Failed):
            clear_subtask_states(detail.task_id)
