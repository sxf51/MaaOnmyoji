"""Per next-list candidate statistics from MaaFramework logs.

For every node that runs a next list, prints each candidate's tries, hits, hit rate and the
time spent on misses, sorted by miss time. Candidates that are tried often but rarely hit are
the ones worth moving later in the list (unless they must precede another candidate).

Usage:
    python tools/next_stats.py                       # debug/maafw.log
    python tools/next_stats.py debug/maafw.bak.*.log --since "2026-11-15 02:08" --top 20
    python tools/next_stats.py --node MitamaBattle

SafeInput_ prefixes in older logs are stripped so they map onto the current node names.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\].*?\[msg=([A-Za-z.]+)\] \[details=(\{.*\})\]\s*$")


def norm(name):
    return name.replace("SafeInput_", "") if isinstance(name, str) else name


def parse_since(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"无法解析时间：{text!r}（格式如 2026-11-15 02:08）")


def collect(paths, since=None):
    rounds = defaultdict(int)
    tries = defaultdict(lambda: defaultdict(int))
    hits = defaultdict(lambda: defaultdict(int))
    miss_ms = defaultdict(lambda: defaultdict(float))
    started = {}
    parent_of_task = {}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "OnEventNotify" not in line or "Node." not in line:
                    continue
                m = LINE.match(line)
                if not m:
                    continue
                stamp, msg, raw = m.groups()
                t = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f")
                if since and t < since:
                    continue
                try:
                    detail = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                task = detail.get("task_id")
                if msg == "Node.NextList.Starting":
                    parent = norm(detail.get("name"))
                    parent_of_task[task] = parent
                    rounds[parent] += 1
                elif msg == "Node.Recognition.Starting":
                    parent = parent_of_task.get(task)
                    if parent is not None:
                        started[(task, norm(detail.get("name")))] = (t, parent)
                elif msg in ("Node.Recognition.Succeeded", "Node.Recognition.Failed"):
                    key = (task, norm(detail.get("name")))
                    if key not in started:
                        continue
                    t0, parent = started.pop(key)
                    tries[parent][key[1]] += 1
                    if msg.endswith("Succeeded"):
                        hits[parent][key[1]] += 1
                    else:
                        miss_ms[parent][key[1]] += (t - t0).total_seconds() * 1000
    return rounds, tries, hits, miss_ms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="*", type=Path, help="MaaFramework 日志，默认 debug/maafw.log")
    parser.add_argument("--since", type=parse_since, help="只统计此时间之后的记录，如 \"2026-11-15 02:08\"")
    parser.add_argument("--top", type=int, default=20, help="按失败耗时显示前 N 个列表（默认 20）")
    parser.add_argument("--node", action="append", default=[], help="只显示指定父节点，可重复")
    args = parser.parse_args(argv)
    logs = args.logs or [ROOT / "debug/maafw.log"]
    missing = [str(p) for p in logs if not p.is_file()]
    if missing:
        parser.error("找不到日志：" + ", ".join(missing))

    rounds, tries, hits, miss_ms = collect(logs, args.since)
    parents = sorted(tries, key=lambda p: -sum(miss_ms[p].values()))
    if args.node:
        parents = [p for p in parents if p in args.node]
    else:
        parents = parents[:args.top]
    if not parents:
        print("没有匹配的记录")
        return 1
    for parent in parents:
        print(f"\n== {parent}  rounds={rounds[parent]}  miss_time={sum(miss_ms[parent].values()) / 1000:.1f}s")
        for name, n in sorted(tries[parent].items(), key=lambda kv: -miss_ms[parent][kv[0]]):
            h = hits[parent][name]
            miss = miss_ms[parent][name]
            print(f"   {name:42s} tries={n:6d} hits={h:5d} hit%={100 * h / n:5.1f} "
                  f"miss_time={miss / 1000:7.1f}s avg_miss={miss / max(1, n - h):6.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
