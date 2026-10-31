"""结算页模板掉落统计；暂存同场结果，返回玩法页后提交一次。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import struct
import zlib
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np

from urllib.error import URLError
from urllib.request import Request, urlopen

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JRecognitionType, JTemplateMatch, JOCR, JWaitFreezes

from .target_count import get_runtime_params

logger = logging.getLogger("MaaOnmyoji.drop_statistics")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_IMAGE_ROOT = _PROJECT_ROOT / "assets" / "resource" / "base" / "image"
_TEMPLATE_ROOT = _IMAGE_ROOT / "Rewards"
_DROP_ROI = (210, 140, 900, 390)
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
    for path in sorted(root.rglob("*.png")):
        label = template_label(path)
        if label:
            templates.setdefault(label, []).append(path.relative_to(_IMAGE_ROOT).as_posix())
    return templates


def template_match_count(detail: Any) -> int:
    """返回通过阈值和 NMS 后的模板命中数量。"""
    if detail is None or not getattr(detail, "hit", False):
        return 0
    results = getattr(detail, "filtered_results", None) or []
    return len(results) if results else 1


def load_drop_catalog(scope: str) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """按玩法加载清单；资源路径只允许位于打包的 image/Rewards 内。"""
    if scope not in {"Mitama", "Awakening"}:
        raise ValueError("未知的奖励模板分类")
    templates, specs = {}, {}
    for category in ("Common", scope):
        folder = _TEMPLATE_ROOT / category
        discovered = discover_drop_templates(folder)
        templates.update(discovered)
        for label in discovered:
            specs[label] = {"quantity_mode": "optional"}
        manifest = folder / "catalog.json"
        if not manifest.is_file():
            continue
        for label, spec in json.loads(manifest.read_text("utf-8")).items():
            if not isinstance(spec, dict):
                raise ValueError("每个掉落物的配置必须是对象")
            variants = spec.get("templates", [])
            mode = spec.get("quantity_mode", "required" if "quantity_roi" in spec else "optional")
            if mode not in {"single", "optional", "required"}:
                raise ValueError("quantity_mode 只能是 single、optional 或 required")
            spec = {**spec, "quantity_mode": mode}
            paths = []
            for name in variants:
                path = (folder / name).resolve()
                if not path.is_relative_to(_TEMPLATE_ROOT.resolve()) or path.suffix.lower() != ".png" or not path.is_file():
                    raise ValueError("掉落模板路径无效")
                paths.append(path.relative_to(_IMAGE_ROOT).as_posix())
            if paths or label in templates:
                # 清单只补充配置，不屏蔽用户以后按同物品名添加的 @变体 文件。
                templates[label] = list(dict.fromkeys([*templates.get(label, []), *paths]))
                specs[label] = spec
                for alias in list(templates):
                    if alias == label:
                        continue
                    templates[alias] = [name for name in templates[alias] if name not in paths]
                    if not templates[alias]:
                        del templates[alias]
                        specs.pop(alias, None)
    return templates, specs


def _overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0, min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
    return intersection / max(1, min(aw*ah,bw*bh)) > 0.5


def parse_quantity(text: str) -> int | None:
    """数量只接受整数；容忍字形边缘被 OCR 读作首尾标点。"""
    value = unicodedata.normalize("NFKC", text).strip().strip(".,，。·:：'’‘\"").strip()
    if value.startswith(("x", "X", "×")):
        value = value[1:].strip()
    return int(value) if value.isascii() and value.isdigit() and int(value)>0 else None


def recognize_drops(context: Context, image: Any, templates: dict[str, list[str]],
                    threshold: float = _DEFAULT_THRESHOLD, specs=None) -> Counter[str]:
    """合并各模板的实际命中框，按分数消除变体重复和同位置分类冲突。"""
    specs = specs or {}
    detections = []
    for label, variants in templates.items():
        spec = specs.get(label, {})
        for template in variants:
            detail = context.run_recognition_direct(JRecognitionType.TemplateMatch,
                JTemplateMatch(template=[template], roi=spec.get("roi",_DROP_ROI),
                    threshold=[spec.get("threshold",threshold)], order_by="Horizontal",
                    green_mask=spec.get("green_mask", True)), image)
            if detail is None or not detail.hit:
                continue
            for result in getattr(detail,"filtered_results",[]) or []:
                box = list(result.box)
                if len(box)==4 and box[2]>0 and box[3]>0:
                    detections.append((float(result.score), label, box))
    accepted, drops = [], Counter()
    for score, label, box in sorted(detections, key=lambda d:d[0], reverse=True):
        if any(_overlaps(box, previous) for previous in accepted):
            continue
        accepted.append(box)
        quantity = 1
        spec = specs.get(label, {})
        mode = spec.get("quantity_mode", "required" if "quantity_roi" in spec else "single")
        if mode != "single":
            # 新增的完整方格模板默认检查右下角；裁剪图标可显式配置相对数量框。
            x, y, w, h = box
            offset = spec.get("quantity_roi", [round(w*.55), round(h*.70), max(1,round(w*.50)), max(1,round(h*.35))])
            if not isinstance(offset, list) or len(offset)!=4 or any(type(v) is not int for v in offset):
                raise ValueError("quantity_roi 必须是相对模板框的四个整数")
            dx, dy, qw, qh = offset
            roi = [x+dx, y+dy, qw, qh]
            if min(roi[:2])<0 or qw<=0 or qh<=0 or roi[0]+qw>image.shape[1] or roi[1]+qh>image.shape[0]:
                raise ValueError("奖励数量区域超出截图")
            # 不先用数字正则过滤，防止把读到的模糊/非数字内容误当作没有数量。
            detail = context.run_recognition_direct(JRecognitionType.OCR,
                JOCR(expected=[], roi=roi, color_filter="RewardQuantityWhite"), image)
            results = getattr(detail, "all_results", None) if detail is not None else None
            if results is None and detail is not None and detail.hit:
                results = getattr(detail, "filtered_results", None)
            crop = image[roi[1]:roi[1]+qh, roi[0]:roi[0]+qw]
            min_ink = spec.get("quantity_min_white_pixels", 20)
            if type(min_ink) is not int or min_ink < 0:
                raise ValueError("quantity_min_white_pixels 必须是非负整数")
            white_pixels = int(np.count_nonzero(np.all(crop[:, :, :3] >= 220, axis=2)))
            if detail is not None and mode == "optional" and white_pixels < min_ink:
                # 小区域 OCR 在空白高光上会输出低置信度标点；无数量字形才按单个。
                quantity = 1
            else:
                text = results[0].text.strip() if results is not None and len(results)==1 else ""
                score = getattr(results[0], "score", 1.0) if results is not None and len(results)==1 else 0
                parsed = parse_quantity(text)
                if parsed is None or score<0.5:
                    drops[label+"（数量未识别）"] += 1
                    continue
                quantity = parsed
        drops[label] += quantity
    return drops


class DropStatisticsState:
    total: Counter[str] = Counter()
    battles: int = 0
    pending: Counter[str] | None = None
    scope: str = ""

    @classmethod
    def reset(cls) -> None:
        cls.total = Counter()
        cls.battles = 0
        cls.pending = None
        cls.scope = ""

    @classmethod
    def add(cls, drops: Counter[str]) -> None:
        cls.total.update(drops)
        cls.battles += 1

    @classmethod
    def summary(cls) -> dict[str, int]:
        return dict(sorted(cls.total.items()))


def save_settlement(scope: str, image: Any) -> None:
    """保留最近一张原始结算图，方便后续手动补充模板。"""
    height, width = image.shape[:2]
    rgb = image[:, :, :3][:, :, ::-1]
    def chunk(kind, data):
        return struct.pack(">I", len(data))+kind+data+struct.pack(">I", zlib.crc32(kind+data))
    rows = b"".join(b"\0"+rgb[y].tobytes() for y in range(height))
    encoded = (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))
               +chunk(b"IDAT",zlib.compress(rows,1))+chunk(b"IEND",b""))
    try:
        folder = _PROJECT_ROOT/"debug"/"rewards"
        folder.mkdir(parents=True,exist_ok=True)
        target = folder/(scope+"-last-settlement.png")
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(target)
    except OSError:
        logger.exception("无法保存最近奖励截图")


def commit_drop_statistics() -> None:
    """仅由成功返回玩法页后的 TargetCountProgress 调用。"""
    if DropStatisticsState.pending is None:
        return
    drops = DropStatisticsState.pending
    DropStatisticsState.pending = None
    DropStatisticsState.add(drops)
    params = get_runtime_params()
    payload = {"schema_version":1,"task":DropStatisticsState.scope+"Task",
        "stage":str(params.get("stage_name","")),"battle_index":DropStatisticsState.battles,
        "drops":dict(drops),"recorded_at":datetime.now(timezone.utc).isoformat()}
    folder = _PROJECT_ROOT / "debug" / "rewards"
    try:
        folder.mkdir(parents=True,exist_ok=True)
        path = folder / (DropStatisticsState.scope+"-latest.json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({**payload,"totals":DropStatisticsState.summary()},ensure_ascii=False,indent=2),"utf-8")
        temporary.replace(path)
    except OSError:
        logger.exception("无法保存奖励统计")
    logger.info("本场奖励：%s；累计：%s",dict(drops),DropStatisticsState.summary())
    if params.get("report_drops") is True:
        url = str(params.get("drop_report_url","")).strip()
        if url.startswith(("https://","http://")):
            _report_queue.enqueue(url,payload)


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
        logger.warning("奖励掉落上报失败：%s", exc)
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
    if len({(p.get("task", "MitamaTask"), p.get("stage", "")) for p in payloads}) != 1:
        raise ValueError("不同玩法或关卡的掉落记录不能合并")
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

            def same_group(item_url, item_payload):
                return (item_url == url and item_payload.get("task", "MitamaTask") == payload.get("task", "MitamaTask")
                        and item_payload.get("stage", "") == payload.get("stage", ""))
            same_url = [p for u, p in pending if same_group(u, p)]
            other = [(u, p) for u, p in pending if not same_group(u, p)]
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
            logger.info("历史奖励掉落记录补报完成")
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
                    logger.info("第 %s 场奖励掉落上报成功", payload.get("battle_index", "?"))
                else:
                    self._append_spool(url, payload)
            except Exception:
                # 后台线程不能因单个上报器的意外异常退出。
                logger.exception("奖励掉落后台上报异常")
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 10.0) -> bool:
        """限时等待已入队任务完成，避免关闭过程无限阻塞。"""
        deadline = time.monotonic() + max(timeout, 0.0)
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                logger.warning("奖励掉落上报收尾超时，仍有 %d 项", self._queue.unfinished_tasks)
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
        "奖励掉落汇总（%d 场）：%s",
        DropStatisticsState.battles,
        "、".join(f"{name}x{count}" for name, count in summary.items()) or "未识别",
    )
    DropStatisticsState.reset()


@AgentServer.custom_action("RewardDropStatistics")
class RewardDropStatistics(CustomAction):
    """读取当前结算帧，只暂存一次；重试关闭结算不会重复统计。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        from .target_count import _parse_params
        if DropStatisticsState.pending is not None:
            return CustomAction.RunResult(success=True)
        try:
            params = _parse_params(argv.custom_action_param)
            scope = params["scope"]
            # Pipeline 先等待奖励入场；这里调用框架原生稳定等待并检查返回值。
            # 节点 post_wait_freezes 的超时不阻断动作，不能拿它作为截图成功条件。
            if not context.wait_freezes(wait_freezes_param=JWaitFreezes(
                    time=1000, target=[240,150,800,150], threshold=0.985,
                    method=10001, rate_limit=150, timeout=8000)):
                logger.warning("奖励图标区域未稳定，取消本次截图和统计")
                return CustomAction.RunResult(success=False)
            image = context.tasker.controller.cached_image
            if image is None or getattr(image,"size",0)==0:
                raise ValueError("结算截图为空")
            # 使用稳定等待更新后的最新画面；重新排除失败和覆盖层。
            for node in ("DailyBattleFailed","MitamaTreasurePopup","CommonRefuseBounty"):
                detail = context.run_recognition(node,image)
                if detail is not None and detail.hit:
                    return CustomAction.RunResult(success=False)
            result = context.run_recognition(params["result_node"], image)
            if result is None or not result.hit:
                return CustomAction.RunResult(success=False)
            templates,specs = load_drop_catalog(scope)
            save_settlement(scope,image)
            if not templates:
                logger.warning("尚无%s奖励模板，不将未识别奖励记成零掉落",scope)
                return CustomAction.RunResult(success=True)
            drops = recognize_drops(context,image,templates,specs=specs)
            DropStatisticsState.scope = scope
            DropStatisticsState.pending = drops
            return CustomAction.RunResult(success=True)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            logger.exception("结算奖励统计失败")
            return CustomAction.RunResult(success=False)
