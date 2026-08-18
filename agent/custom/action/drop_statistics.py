"""御魂掉落 OCR 统计与可选上报。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JRecognitionType, JTemplateMatch

from .target_count import get_runtime_params

logger = logging.getLogger("MaaOnmyoji.drop_statistics")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE_ROOT = _PROJECT_ROOT / "assets" / "resource" / "base" / "image" / "Mitama" / "Drop"
_RESOURCE_PREFIX = "Mitama/Drop"
_DROP_ROI = (100, 40, 1080, 590)
_DEFAULT_THRESHOLD = 0.8
_REPORT_QUEUE_MAXSIZE = 100
_REPORT_SPOOL_PATH = _PROJECT_ROOT / "debug" / "drop_reports.jsonl"


def template_label(path: Path) -> str:
    """模板名可用 ``物品名@变体.png`` 表示同一物品的多个变体。"""
    return path.stem.split("@", 1)[0].strip()


def discover_drop_templates(root: Path = _TEMPLATE_ROOT) -> dict[str, list[str]]:
    """发现模板并转换为 MaaResource image 根目录下的资源名。"""
    templates: dict[str, list[str]] = {}
    if not root.is_dir():
        return templates
    for path in sorted(root.glob("*.png")):
        label = template_label(path)
        if label:
            templates.setdefault(label, []).append(f"{_RESOURCE_PREFIX}/{path.name}")
    return templates


def template_match_count(detail: Any) -> int:
    """返回通过阈值和 NMS 后的模板命中数量。"""
    if detail is None or not getattr(detail, "hit", False):
        return 0
    results = getattr(detail, "filtered_results", None) or []
    return len(results) if results else 1


def recognize_drops(
    context: Context,
    image: Any,
    templates: dict[str, list[str]],
    threshold: float = _DEFAULT_THRESHOLD,
) -> Counter[str]:
    """逐模板执行 TemplateMatch；同物品多个变体取最大命中数。"""
    drops: Counter[str] = Counter()
    for label, variants in templates.items():
        counts: list[int] = []
        for template in variants:
            detail = context.run_recognition_direct(
                JRecognitionType.TemplateMatch,
                JTemplateMatch(
                    template=[template],
                    roi=_DROP_ROI,
                    threshold=[threshold],
                    order_by="Horizontal",
                ),
                image,
            )
            counts.append(template_match_count(detail))
        count = max(counts, default=0)
        if count:
            drops[label] = count
    return drops


class DropStatisticsState:
    total: Counter[str] = Counter()
    battles: int = 0

    @classmethod
    def reset(cls) -> None:
        cls.total = Counter()
        cls.battles = 0

    @classmethod
    def add(cls, drops: Counter[str]) -> None:
        cls.total.update(drops)
        cls.battles += 1

    @classmethod
    def summary(cls) -> dict[str, int]:
        return dict(sorted(cls.total.items()))


def _report(url: str, payload: dict[str, Any], timeout: float = 5.0) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except (OSError, URLError, ValueError) as exc:
        logger.warning("御魂掉落上报失败：%s", exc)
        return False


def merge_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """把同一上报地址的逐场记录压缩为一个累计 payload。"""
    if not payloads:
        raise ValueError("payloads 不能为空")
    drops: Counter[str] = Counter()
    battle_count = 0
    indexes: list[int] = []
    for payload in payloads:
        drops.update({str(k): int(v) for k, v in payload.get("drops", {}).items()})
        battle_count += int(payload.get("battle_count", 1))
        if "battle_index" in payload:
            indexes.append(int(payload["battle_index"]))
        indexes.extend(int(v) for v in payload.get("battle_indexes", []))
    first, last = payloads[0], payloads[-1]
    merged = {
        "schema_version": 1,
        "task": str(last.get("task", "MitamaTask")),
        "stage": str(last.get("stage", "")),
        "aggregated": True,
        "battle_count": battle_count,
        "battle_indexes": sorted(set(indexes)),
        "drops": dict(drops),
        "recorded_at": str(last.get("recorded_at", "")),
        "recorded_from": str(first.get("recorded_from", first.get("recorded_at", ""))),
    }
    return merged


class AsyncReportQueue:
    """单 worker 上报队列；任务线程只入队，不等待网络。"""

    _STOP = object()

    _RECOVER = object()

    def __init__(
        self,
        sender: Any = _report,
        maxsize: int = _REPORT_QUEUE_MAXSIZE,
        spool_path: Path = _REPORT_SPOOL_PATH,
    ) -> None:
        self._sender = sender
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._spool_path = spool_path
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._collapse_lock = threading.Lock()
        self._spool_lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._work,
                name="MaaOnmyoji-drop-reporter",
                daemon=True,
            )
            self._thread.start()

    def enqueue(self, url: str, payload: dict[str, Any]) -> None:
        self._ensure_started()
        try:
            self._queue.put_nowait((url, payload))
        except queue.Full:
            self._collapse_pending(url, payload)

    def recover(self) -> None:
        """唤醒 worker，尝试补报磁盘中的历史记录。"""
        self._ensure_started()
        try:
            self._queue.put_nowait(self._RECOVER)
        except queue.Full:
            # 队列已有工作时，worker 会在发送下一项前自动补报。
            pass

    def _collapse_pending(self, url: str, payload: dict[str, Any]) -> None:
        """队列满时压缩相同 URL 的待发送记录，再放回队列。"""
        with self._collapse_lock:
            pending: list[tuple[str, dict[str, Any]]] = []
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item not in (self._STOP, self._RECOVER):
                    pending.append(item)
                self._queue.task_done()

            same_url = [item_payload for item_url, item_payload in pending if item_url == url]
            other = [(item_url, item_payload) for item_url, item_payload in pending if item_url != url]
            same_url.append(payload)
            collapsed = merge_payloads(same_url)
            for item in [*other, (url, collapsed)]:
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    item_url, item_payload = item
                    self._append_spool(item_url, item_payload)
            logger.warning("上报队列已满，已将 %d 条记录合并", len(same_url))

    def _read_spool(self) -> list[tuple[str, dict[str, Any]]]:
        if not self._spool_path.is_file():
            return []
        records: list[tuple[str, dict[str, Any]]] = []
        for line in self._spool_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                records.append((str(item["url"]), dict(item["payload"])))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("忽略损坏的掉落补报记录：%r", line[:120])
        return records

    def _write_spool(self, records: list[tuple[str, dict[str, Any]]]) -> None:
        self._spool_path.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            self._spool_path.unlink(missing_ok=True)
            return
        temp = self._spool_path.with_suffix(self._spool_path.suffix + ".tmp")
        content = "".join(
            json.dumps({"url": url, "payload": payload}, ensure_ascii=False) + "\n"
            for url, payload in records
        )
        temp.write_text(content, encoding="utf-8")
        temp.replace(self._spool_path)

    def _append_spool(self, url: str, payload: dict[str, Any]) -> None:
        with self._spool_lock:
            records = self._read_spool()
            records.append((url, payload))
            self._write_spool(records)

    def _replay_spool(self) -> bool:
        with self._spool_lock:
            records = self._read_spool()
            if not records:
                return True
            remaining: list[tuple[str, dict[str, Any]]] = []
            for index, (url, payload) in enumerate(records):
                if not self._sender(url, payload):
                    remaining = records[index:]
                    break
            self._write_spool(remaining)
        if not remaining:
            logger.info("历史御魂掉落记录补报完成")
        return not remaining

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._STOP:
                    return
                if item is self._RECOVER:
                    self._replay_spool()
                    continue
                url, payload = item
                self._replay_spool()
                if self._sender(url, payload):
                    logger.info("第 %s 场御魂掉落上报成功", payload.get("battle_index", "?"))
                else:
                    self._append_spool(url, payload)
            except Exception:
                # 后台线程不能因单个上报器的意外异常退出。
                logger.exception("御魂掉落后台上报异常")
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 10.0) -> bool:
        """限时等待已入队任务完成，避免关闭过程无限阻塞。"""
        deadline = time.monotonic() + max(timeout, 0.0)
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                logger.warning("御魂掉落上报收尾超时，仍有 %d 项", self._queue.unfinished_tasks)
                return False
            time.sleep(0.05)
        return True

    def shutdown(self, timeout: float = 10.0) -> bool:
        flushed = self.flush(timeout)
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return flushed
            self._queue.put(self._STOP)
        thread.join(timeout=max(timeout, 0.0))
        return flushed and not thread.is_alive()


_report_queue = AsyncReportQueue()


def shutdown_reporter(timeout: float = 10.0) -> bool:
    """Agent 生命周期结束前调用，完成最后一批异步上报。"""
    return _report_queue.shutdown(timeout)


def recover_pending_reports() -> None:
    """Agent 启动后异步补报上次退出时留下的 JSONL。"""
    _report_queue.recover()


def reset_drop_statistics() -> None:
    DropStatisticsState.reset()


def finish_drop_statistics() -> None:
    if DropStatisticsState.battles == 0:
        return
    summary = DropStatisticsState.summary()
    logger.info(
        "御魂掉落汇总（%d 场）：%s",
        DropStatisticsState.battles,
        "、".join(f"{name}x{count}" for name, count in summary.items()) or "未识别",
    )
    DropStatisticsState.reset()


@AgentServer.custom_action("MitamaDropStatistics")
class MitamaDropStatistics(CustomAction):
    """在当前胜利画面逐个执行模板匹配，累计并按配置上报。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        del argv
        templates = discover_drop_templates()
        if not templates:
            logger.warning("未找到御魂掉落模板：%s", _TEMPLATE_ROOT)
            return CustomAction.RunResult(success=True)
        image = context.tasker.controller.cached_image
        drops = recognize_drops(context, image, templates)
        DropStatisticsState.add(drops)
        logger.info(
            "本场御魂掉落：%s",
            "、".join(f"{name}x{count}" for name, count in drops.items()) or "未识别",
        )

        params = get_runtime_params()
        if params.get("report_drops") is not True:
            return CustomAction.RunResult(success=True)
        url = str(params.get("drop_report_url", "")).strip()
        if not url:
            logger.warning("已开启御魂掉落上报，但未配置上报地址")
            return CustomAction.RunResult(success=True)

        payload = {
            "schema_version": 1,
            "task": "MitamaTask",
            "stage": str(params.get("stage_name", "")),
            "battle_index": DropStatisticsState.battles,
            "drops": dict(drops),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        _report_queue.enqueue(url, payload)
        return CustomAction.RunResult(success=True)
