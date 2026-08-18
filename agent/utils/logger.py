from __future__ import annotations

import html
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import TextIO

LOGGER_NAME = "MaaOnmyoji"

LEVEL_SHORT_NAMES = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "err",
    "CRITICAL": "critical",
}

ANSI_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41m\033[37m",
}

HTML_LEVEL_COLORS = {
    "DEBUG": "deepskyblue",
    "INFO": "forestgreen",
    "WARNING": "darkorange",
    "ERROR": "crimson",
    "CRITICAL": "firebrick",
}


def _client_name() -> str:
    return os.getenv("PI_CLIENT_NAME", "").strip().upper()


def _is_mfaa_client() -> bool:
    return _client_name() == "MFAAVALONIA"


def _is_mxu_client() -> bool:
    return _client_name() == "MXU"


def _resolve_console_stream() -> TextIO:
    return sys.stdout if _is_mxu_client() else sys.stderr


def _short_level_name(level_name: str) -> str:
    return LEVEL_SHORT_NAMES.get(level_name, level_name.lower())


def _format_mxu_html_message(level_name: str, message: str) -> str:
    color = HTML_LEVEL_COLORS.get(level_name, "inherit")
    return "\n".join(
        f'<span style="color:{color};">{line}</span>'
        for line in html.escape(message).split("\n")
    )


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()

        if _is_mfaa_client():
            return f"{_short_level_name(record.levelname)}:{message}"
        if _is_mxu_client():
            return _format_mxu_html_message(record.levelname, message)

        color = ANSI_LEVEL_COLORS.get(record.levelname, "")
        reset = "\033[0m" if color else ""
        return f"{color}{message}{reset}"


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), logging.INFO)


def _close_handlers(target: logging.Logger) -> None:
    for handler in target.handlers[:]:
        target.removeHandler(handler)
        handler.close()


def setup_logger(
    log_dir: str | Path = "debug/custom",
    console_level: str | int = "INFO",
) -> logging.Logger:
    """Configure MaaOnmyoji child loggers for the PI console and rotating files."""

    target = logging.getLogger(LOGGER_NAME)
    _close_handlers(target)
    target.setLevel(logging.DEBUG)
    target.propagate = False

    console_handler = logging.StreamHandler(_resolve_console_stream())
    console_handler.setLevel(_resolve_level(console_level))
    console_handler.setFormatter(_ConsoleFormatter())
    console_handler.name = "MaaOnmyoji-console"
    target.addHandler(console_handler)

    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        path / "runtime.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | "
            "%(name)s:%(funcName)s:%(lineno)d | %(message)s"
        )
    )
    file_handler.name = "MaaOnmyoji-file"
    target.addHandler(file_handler)

    return target


def change_console_level(level: str | int = "DEBUG") -> None:
    """Change only the PI console threshold while keeping file logging at DEBUG."""

    resolved = _resolve_level(level)
    for handler in logger.handlers:
        if handler.name == "MaaOnmyoji-console":
            handler.setLevel(resolved)
            break
    logger.info("控制台日志等级已更改为: %s", logging.getLevelName(resolved))


logger = setup_logger()
