from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any, List
import os

from .core import SqsListenerEngine
from .logging_setup import get_logger

_logger = get_logger("sqs_fargate_listener")


@dataclass
class _ListenerSpec:
    queue_url: str
    handler: Callable
    mode: str
    opts: Dict[str, Any]


_REGISTRY: list[_ListenerSpec] = []
_VALID_MODES = {"batch", "per_message"}


def _validate_positive_int(name: str, value: Optional[int]) -> None:
    if value is not None and value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")


def _validate_batch_size(value: Optional[int]) -> None:
    if value is not None and not (1 <= value <= 10):
        raise ValueError(f"batch_size must be between 1 and 10, got {value}")


def _validate_wait_time(value: Optional[int]) -> None:
    if value is not None and not (0 <= value <= 20):
        raise ValueError(f"wait_time must be between 0 and 20, got {value}")


def _validate_queue_url(url: str) -> None:
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"queue_url must start with http:// or https://, got: {url!r}")


def sqs_listener(
    queue_url: Optional[str] = None,
    *,
    mode: str = "batch",
    wait_time: Optional[int] = None,
    batch_size: Optional[int] = None,
    visibility_secs: Optional[int] = None,
    max_extend: Optional[int] = None,
    worker_threads: Optional[int] = None,
    # Filtering
    filter_fn: Optional[Callable[[Any], bool]] = None,
    # Retry backoff
    retry_backoff: bool = False,
    retry_backoff_base: int = 30,
    retry_backoff_max: int = 600,
    # Hooks
    on_success: Optional[Callable] = None,
    on_failure: Optional[Callable] = None,
    # Health check
    health_check_port: Optional[int] = None,
    **extra_opts: Any,
):
    """
    Decorator to register an SQS handler.

    Args:
        queue_url:          SQS queue URL. Falls back to QUEUE_URL env var.
        mode:               "batch" or "per_message".
        wait_time:          Long-poll wait seconds (0–20).
        batch_size:         Messages per poll (1–10).
        visibility_secs:    Initial visibility timeout in seconds.
        max_extend:         Max total visibility extension in seconds.
        worker_threads:     Number of concurrent poller threads.
        filter_fn:          Optional callable(SqsMessage) -> bool. Non-matching
                            messages are released back to the queue immediately.
        retry_backoff:      Enable exponential backoff on failure via
                            ChangeMessageVisibility.
        retry_backoff_base: Base delay in seconds for backoff (default 30).
        retry_backoff_max:  Max backoff delay in seconds (default 600).
        on_success:         Hook called with (SqsMessage,) after each success.
        on_failure:         Hook called with (SqsMessage, exc_or_None) after each failure.
        health_check_port:  If set, starts an HTTP health server on this port.
                            GET /health → {"status":"ok"}
                            GET /metrics → stats JSON
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
    _validate_batch_size(batch_size)
    _validate_wait_time(wait_time)
    _validate_positive_int("visibility_secs", visibility_secs)
    _validate_positive_int("max_extend", max_extend)
    _validate_positive_int("worker_threads", worker_threads)

    def _decorator(func: Callable):
        q = queue_url or os.environ.get("QUEUE_URL")
        if not q:
            raise ValueError("queue_url is required (or set QUEUE_URL env var).")
        _validate_queue_url(q)
        spec = _ListenerSpec(
            queue_url=q,
            handler=func,
            mode=mode,
            opts={
                "wait_time": wait_time,
                "batch_size": batch_size,
                "visibility_secs": visibility_secs,
                "max_extend": max_extend,
                "worker_threads": worker_threads,
                "filter_fn": filter_fn,
                "retry_backoff": retry_backoff,
                "retry_backoff_base": retry_backoff_base,
                "retry_backoff_max": retry_backoff_max,
                "on_success": on_success,
                "on_failure": on_failure,
                "health_check_port": health_check_port,
                **extra_opts,
            },
        )
        _REGISTRY.append(spec)
        _logger.info("Registered listener for queue=%s mode=%s handler=%s", q, mode, func.__name__)
        return func
    return _decorator


def run_listeners(block: bool = True):
    """
    Instantiate and start all registered listeners. If block=True, join threads.
    """
    if not _REGISTRY:
        raise RuntimeError("No listeners registered. Did you decorate a function with @sqs_listener?")

    _logger.info("Starting %d SQS listener(s)…", len(_REGISTRY))
    engines: List[SqsListenerEngine] = []
    for spec in _REGISTRY:
        # Drop None values except for boolean/int opts that are intentionally falsy
        _passthrough = {"retry_backoff", "retry_backoff_base", "retry_backoff_max"}
        filtered_opts = {
            k: v for k, v in spec.opts.items()
            if v is not None or k in _passthrough
        }
        eng = SqsListenerEngine(
            queue_url=spec.queue_url,
            handler=spec.handler,
            mode=spec.mode,
            **filtered_opts,
        )
        eng.start()
        engines.append(eng)
    _logger.info("All listeners started.")

    if block:
        for eng in engines:
            eng.join()
        _logger.info("All listeners stopped.")
