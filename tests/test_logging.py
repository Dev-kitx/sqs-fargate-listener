# tests/test_logging.py
# -------------------------------------------------------------------
# Tests for logging_setup.py:
# - JsonFormatter produces valid JSON with required fields
# - JsonFormatter includes extra fields and exception info
# - get_logger is idempotent
# - LOG_JSON env var selects JsonFormatter
# -------------------------------------------------------------------

import json
import logging

from src.sqs_fargate_listener.logging_setup import JsonFormatter, get_logger


def _reset_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.__dict__.pop("_sqs_fargate_listener_configured", None)
    logger.handlers.clear()
    return logger


# ---------------------------
# JsonFormatter
# ---------------------------


def _make_record(msg="hello", level=logging.INFO, name="test", extra=None):
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="f.py",
        lineno=10,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_json_formatter_produces_valid_json():
    formatter = JsonFormatter()
    output = formatter.format(_make_record("test message"))
    parsed = json.loads(output)
    assert parsed["message"] == "test message"


def test_json_formatter_required_fields():
    formatter = JsonFormatter()
    parsed = json.loads(formatter.format(_make_record()))
    assert "timestamp" in parsed
    assert "level" in parsed
    assert "logger" in parsed
    assert "message" in parsed
    assert "line" in parsed


def test_json_formatter_timestamp_format():
    formatter = JsonFormatter()
    parsed = json.loads(formatter.format(_make_record()))
    ts = parsed["timestamp"]
    assert ts.endswith("Z")
    assert "T" in ts


def test_json_formatter_level_name():
    formatter = JsonFormatter()
    parsed = json.loads(formatter.format(_make_record(level=logging.ERROR)))
    assert parsed["level"] == "ERROR"


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = _make_record(extra={"message_id": "abc-123", "trace_id": "t-xyz"})
    parsed = json.loads(formatter.format(record))
    assert parsed["message_id"] == "abc-123"
    assert parsed["trace_id"] == "t-xyz"


def test_json_formatter_includes_exception():
    formatter = JsonFormatter()
    try:
        raise ValueError("something broke")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="f.py",
        lineno=1,
        msg="err",
        args=(),
        exc_info=exc_info,
    )
    parsed = json.loads(formatter.format(record))
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]


def test_json_formatter_excludes_builtin_log_record_keys():
    formatter = JsonFormatter()
    record = _make_record(extra={"message_id": "m1"})
    parsed = json.loads(formatter.format(record))
    # Standard LogRecord internals should not bleed through
    assert "exc_info" not in parsed
    assert "args" not in parsed
    assert "msg" not in parsed


def test_json_formatter_handles_non_string_extra():
    formatter = JsonFormatter()
    record = _make_record(extra={"count": 42, "flag": True, "data": {"k": "v"}})
    parsed = json.loads(formatter.format(record))
    assert parsed["count"] == 42
    assert parsed["flag"] is True
    assert parsed["data"] == {"k": "v"}


# ---------------------------
# get_logger
# ---------------------------


def test_get_logger_returns_logger():
    _reset_logger("sqs_test_basic")
    logger = get_logger("sqs_test_basic")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "sqs_test_basic"


def test_get_logger_is_idempotent():
    _reset_logger("sqs_test_idempotent")
    l1 = get_logger("sqs_test_idempotent")
    l2 = get_logger("sqs_test_idempotent")
    assert l1 is l2
    assert len(l1.handlers) == 1  # no duplicate handlers


def test_get_logger_respects_log_level(monkeypatch):
    _reset_logger("sqs_test_level")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    # Force re-read of module-level default
    import importlib

    import src.sqs_fargate_listener.logging_setup as ls

    importlib.reload(ls)

    logger = ls.get_logger("sqs_test_level")
    assert logger.level == logging.DEBUG

    # Restore
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    importlib.reload(ls)


def test_get_logger_json_mode_uses_json_formatter(monkeypatch):
    import importlib

    import src.sqs_fargate_listener.logging_setup as ls

    monkeypatch.setenv("LOG_JSON", "1")
    importlib.reload(ls)

    name = "sqs_test_json_mode"
    _reset_logger(name)
    logger = ls.get_logger(name)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, ls.JsonFormatter)

    # Restore
    monkeypatch.setenv("LOG_JSON", "0")
    importlib.reload(ls)


def test_get_logger_json_output_is_parseable(monkeypatch, capsys):
    import importlib

    import src.sqs_fargate_listener.logging_setup as ls

    monkeypatch.setenv("LOG_JSON", "1")
    importlib.reload(ls)

    name = "sqs_test_json_output"
    _reset_logger(name)
    logger = ls.get_logger(name)
    logger.info("structured message", extra={"order_id": "X99"})

    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed["message"] == "structured message"
    assert parsed["order_id"] == "X99"
    assert parsed["level"] == "INFO"

    monkeypatch.setenv("LOG_JSON", "0")
    importlib.reload(ls)
