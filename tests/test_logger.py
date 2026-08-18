from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from utils import logger as app_logger
from utils.logger import change_console_level, setup_logger


def test_child_logger_writes_to_console_and_file(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PI_CLIENT_NAME", raising=False)
    setup_logger(tmp_path, console_level="DEBUG")

    child = logging.getLogger("MaaOnmyoji.example")
    child.debug("example value=%s", 42)
    for handler in app_logger.handlers:
        handler.flush()

    assert "example value=42" in capsys.readouterr().err
    assert "example value=42" in (tmp_path / "runtime.log").read_text(encoding="utf-8")


def test_mfaa_console_format(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PI_CLIENT_NAME", "MFAAvalonia")
    setup_logger(tmp_path, console_level="INFO")

    logging.getLogger("MaaOnmyoji.example").warning("device unavailable")

    assert "warn:device unavailable" in capsys.readouterr().err


def test_change_console_level_keeps_debug_file_logging(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PI_CLIENT_NAME", raising=False)
    setup_logger(tmp_path, console_level="INFO")

    change_console_level("DEBUG")
    logging.getLogger("MaaOnmyoji.example").debug("debug enabled")
    for handler in app_logger.handlers:
        handler.flush()

    assert "debug enabled" in capsys.readouterr().err
    assert "debug enabled" in (tmp_path / "runtime.log").read_text(encoding="utf-8")
