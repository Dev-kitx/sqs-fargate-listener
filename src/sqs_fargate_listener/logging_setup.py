from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

try:
    from colorlog import ColoredFormatter
except Exception:
    ColoredFormatter = None  # type: ignore[assignment,misc]

_DEFAULT_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_DEFAULT_USE_COLOR = os.environ.get("LOG_USE_COLOR", "1").lower() in ("1", "true", "yes", "y")
_DEFAULT_USE_JSON = os.environ.get("LOG_JSON", "0").lower() in ("1", "true", "yes", "y")
_DEFAULT_FORMAT = os.environ.get(
    "LOG_FORMAT",
    "%(log_color)s[%(levelname)s]%(reset)s %(message_log_color)s%(message)s%(reset)s "
    "(%(name)s:%(lineno)d)",
)
_DEFAULT_DATEFMT = os.environ.get("LOG_DATEFMT", "%Y-%m-%d %H:%M:%S")

_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}

_COLORS = {
    "DEBUG": "cyan",
    "INFO": "white",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red",
}

# Standard LogRecord attributes to exclude from JSON extra fields
_LOG_RECORD_BUILTINS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """
    Emits one JSON object per log line. Extra fields passed via logger.info(..., extra={...})
    are included automatically — useful for message_id, trace_id, etc.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        log: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "line": record.lineno,
        }

        # Attach any caller-supplied extra fields (message_id, trace_id, etc.)
        for key, value in record.__dict__.items():
            if key not in _LOG_RECORD_BUILTINS and not key.startswith("_"):
                log[key] = value

        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)

        return json.dumps(log, default=str)


def _is_tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def get_logger(name: str = "sqs_fargate_listener") -> logging.Logger:
    """
    Create or fetch a configured logger.

    Format is controlled by env vars (in priority order):
      LOG_JSON=1            → structured JSON (one object per line, CloudWatch/Datadog/Splunk ready)
      LOG_USE_COLOR=1 + TTY → colorized output for local development
      fallback              → plain text

    Other controls: LOG_LEVEL, LOG_FORMAT, LOG_PLAIN_FORMAT, LOG_DATEFMT
    """
    logger = logging.getLogger(name)
    if getattr(logger, "_sqs_fargate_listener_configured", False):
        return logger

    level = _LEVELS.get(_DEFAULT_LEVEL, logging.INFO)
    logger.setLevel(level)

    handler = logging.StreamHandler(stream=sys.stdout)
    use_color = _DEFAULT_USE_COLOR and ColoredFormatter is not None and _is_tty(sys.stdout)

    if _DEFAULT_USE_JSON:
        formatter: logging.Formatter = JsonFormatter()
    elif use_color:
        formatter = ColoredFormatter(
            _DEFAULT_FORMAT,
            datefmt=_DEFAULT_DATEFMT,
            log_colors=_COLORS,
            secondary_log_colors={"message": _COLORS},
            reset=True,
            style="%",
        )
    else:
        fmt = os.environ.get(
            "LOG_PLAIN_FORMAT", "[%(levelname)s] %(message)s (%(name)s:%(lineno)d)"
        )
        formatter = logging.Formatter(fmt=fmt, datefmt=_DEFAULT_DATEFMT)

    handler.setFormatter(formatter)
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger._sqs_fargate_listener_configured = True  # type: ignore[attr-defined]
    return logger
