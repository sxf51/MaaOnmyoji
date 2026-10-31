"""Battle-session orchestration; page handling stays in native Pipeline."""
import logging

logger=logging.getLogger("MaaOnmyoji.battle_session")

def succeeded(result):
    return bool(result and result.status.succeeded)


def run_battle_session(context, params):
    prefix = params.get("scope")
    if prefix not in ("Mitama", "Awakening"):
        raise ValueError("scope 必须是 Mitama 或 Awakening")
    disable = prefix + "DisableBuff"
    cleanup_override = params.get("cleanup_override", {})
    if not isinstance(cleanup_override, dict):
        raise ValueError("cleanup_override 必须是原生节点覆盖对象")
    cleanup_enabled = bool(context.get_node_data(disable).get("enabled", True))
    result = False
    try:
        result = succeeded(context.run_task(prefix + "Navigate"))
    except (RuntimeError, ValueError, OSError) as exc:
        logger.error("%s 战斗流程异常：%s", prefix, exc)
    finally:
        if not result and cleanup_enabled and not context.tasker.stopping:
            logger.warning("%s 未正常结束，尝试关闭对应加成；不增加成功次数", prefix)
            try:
                # Isolate cleanup routes/budgets from the failed battle context.
                cleanup = context.clone()
                # Limits are configured in Pipeline and enforced by the SDK in
                # this clone. This adapter performs no timed/visual polling.
                override = {**cleanup_override, disable: {"enabled": True}}
                if not succeeded(cleanup.run_task(prefix + "AbortCleanup", override)):
                    logger.error("%s 加成清理未完成，请检查当前页面与加成状态", prefix)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.error("%s 加成清理异常：%s", prefix, exc)
    return result


