"""Recognize saved screenshots or the configured emulator; navigation is opt-in.

Run from the repository root:
    uv run python tools/probe_pages.py debug/explore.png
    uv run python tools/probe_pages.py --live
    uv run python tools/probe_pages.py --live --target Explore
"""
import argparse
import ctypes
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from maa.controller import AdbController, CustomController
from maa.buffer import ImageBuffer
from maa.library import Library
from maa.define import MaaBool, MaaImageBufferHandle, MaaSize
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from custom.navigation import PAGES, detect_page, hit, navigate, screenshot


class ScreenshotController(CustomController):
    """Read-only controller using Maa's own image decoder, with no extra DLLs."""
    def __init__(self, filename):
        self.filename = Path(filename)
        super().__init__()

    def connect(self):
        return self.filename.is_file()

    def request_uuid(self):
        return str(self.filename)

    def get_features(self):
        return 0

    def screencap(self):
        data = self.filename.read_bytes()
        buffer = ImageBuffer()
        decode = Library.framework().MaaImageBufferSetEncoded
        decode.argtypes = [MaaImageBufferHandle, ctypes.c_void_p, MaaSize]
        decode.restype = MaaBool
        if not decode(buffer._handle, data, len(data)):
            raise ValueError(f"Unable to decode {self.filename}")
        return buffer.get()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--target", choices=PAGES)
    parser.add_argument("--nodes", nargs="*", default=[])
    args = parser.parse_args()
    if args.target and not args.live:
        parser.error("--target requires --live")
    if not args.images and not args.live:
        parser.error("provide screenshots or --live")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    Toolkit.init_option(ROOT / "debug" / "page_probe")
    resource = Resource()
    assert resource.post_bundle(ROOT / "assets/resource/base").wait().succeeded
    failed = False
    for filename in ([None] if args.live else args.images):
        if args.live:
            config = json.loads((ROOT / "assets/config/maa_pi_config.json").read_text(encoding="utf-8"))["adb"]
            controller = AdbController(config["adb_path"], config["address"], int(config["screencap"]), int(config["input"]), config.get("config", {}))
        else:
            controller = ScreenshotController(Path(filename).resolve())
        assert controller.post_connection().wait().succeeded
        tasker = Tasker()
        assert tasker.bind(resource, controller)

        class Probe(CustomAction):
            def run(self, context, argv):
                image = screenshot(context)
                if image is None:
                    return CustomAction.RunResult(success=False)
                page = detect_page(context, image)
                hits = []
                for name in args.nodes:
                    detail = context.run_recognition(name, image)
                    hits.append({"node": name, "hit": hit(detail), "box": list(detail.box) if hit(detail) else None})
                print(json.dumps({"image": filename or "live", "page": page, "probes": hits}, ensure_ascii=False), flush=True)
                success = navigate(context, [args.target]) if args.target else page != "Unknown"
                return CustomAction.RunResult(success=success)

        resource.register_custom_action("PageProbe", Probe())
        job = tasker.post_task("PageProbeEntry", {"PageProbeEntry": {"action": {"type": "Custom", "param": {"custom_action": "PageProbe"}}}}).wait()
        failed |= not job.succeeded
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
