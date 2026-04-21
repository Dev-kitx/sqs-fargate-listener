from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import random
import signal
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

from .health import HealthServer
from .logging_setup import get_logger
from .types import BatchResult, SqsMessage

logger = get_logger("sqs_fargate_listener.engine")


def _env_int(name, default):
    return int(os.environ.get(name, str(default)))


def _env_float(name, default):
    return float(os.environ.get(name, str(default)))


class VisibilityExtender(threading.Thread):
    """
    Heartbeats a message's visibility while it's actively being processed.
    Stops immediately when either:
      - the global stop_event (SIGTERM drain) is set, or
      - the per-message msg_stop_event is set (handler finished).
    """

    def __init__(
        self,
        sqs,
        queue_url: str,
        receipt_handle: str,
        vis_secs: int,
        max_extend: int,
        stop_event: threading.Event,
        msg_stop_event: threading.Event,
    ):
        super().__init__(daemon=True)
        self.sqs = sqs
        self.queue_url = queue_url
        self.rh = receipt_handle
        self.vis_secs = vis_secs
        self.max_extend = max_extend
        self.stop_event = stop_event
        self.msg_stop_event = msg_stop_event
        self.elapsed = 0

    def run(self):
        interval = max(1, self.vis_secs // 2)
        while (
            not self.stop_event.is_set()
            and not self.msg_stop_event.is_set()
            and self.elapsed < self.max_extend
        ):
            self.msg_stop_event.wait(timeout=interval)
            if self.stop_event.is_set() or self.msg_stop_event.is_set():
                break
            try:
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url, ReceiptHandle=self.rh, VisibilityTimeout=self.vis_secs
                )
                logger.debug("Extended visibility for RH=%s by %ds", self.rh[:12], self.vis_secs)
            except Exception as e:
                logger.warning("[visibility] extend failed for RH=%s: %s", self.rh[:12], e)
                break
            self.elapsed += interval


class SqsListenerEngine:
    """
    Main engine: poll -> filter -> handle -> hooks -> delete successes -> re-queue failures.

    Handler shapes:

    Batch:
        def handler(messages: List[SqsMessage]) -> BatchResult

    Per-message:
        def handler(message: SqsMessage) -> bool
        (return True to delete; False/exception = retry)

    Both sync and async handlers are supported.
    """

    def __init__(
        self,
        queue_url: str,
        handler: Callable,
        mode: str = "batch",
        wait_time: int | None = None,
        batch_size: int | None = None,
        visibility_secs: int | None = None,
        max_extend: int | None = None,
        worker_threads: int | None = None,
        client_kwargs: dict[str, Any] | None = None,
        # Filtering
        filter_fn: Callable[[SqsMessage], bool] | None = None,
        # Retry backoff
        retry_backoff: bool = False,
        retry_backoff_base: int = 30,
        retry_backoff_max: int = 600,
        # Hooks
        on_success: Callable[[SqsMessage], None] | None = None,
        on_failure: Callable[[SqsMessage, Exception | None], None] | None = None,
        # Health check
        health_check_port: int | None = None,
    ):
        if not queue_url or not queue_url.startswith(("https://", "http://")):
            raise ValueError(f"queue_url must start with http:// or https://, got: {queue_url!r}")
        if mode not in ("batch", "per_message"):
            raise ValueError(f"mode must be 'batch' or 'per_message', got {mode!r}")

        self.queue_url = queue_url
        self.handler = handler
        self.mode = mode
        self.filter_fn = filter_fn
        self.retry_backoff = retry_backoff
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_max = retry_backoff_max
        self.on_success = on_success
        self.on_failure = on_failure
        self.health_check_port = health_check_port
        self._is_async = inspect.iscoroutinefunction(handler)
        self._started_at = datetime.now(timezone.utc)

        self.sqs = boto3.client(
            "sqs",
            config=Config(
                retries={"max_attempts": 10, "mode": "adaptive"},
                user_agent_extra="sqs-fargate-listener/1.1",
            ),
            **(client_kwargs or {}),
        )

        self.wait_time = wait_time if wait_time is not None else _env_int("WAIT_TIME", 20)
        self.batch_size = batch_size if batch_size is not None else _env_int("BATCH_SIZE", 10)
        self.visibility_secs = (
            visibility_secs if visibility_secs is not None else _env_int("VISIBILITY_SECS", 60)
        )
        self.max_extend = max_extend if max_extend is not None else _env_int("MAX_EXTEND", 900)
        self.worker_threads = (
            worker_threads if worker_threads is not None else _env_int("WORKER_THREADS", 4)
        )
        self.idle_sleep_max = _env_float("IDLE_SLEEP_MAX", 2.0)

        if not (0 <= self.wait_time <= 20):
            raise ValueError(f"wait_time must be 0–20, got {self.wait_time}")
        if not (1 <= self.batch_size <= 10):
            raise ValueError(f"batch_size must be 1–10, got {self.batch_size}")
        if self.visibility_secs < 1:
            raise ValueError(f"visibility_secs must be >= 1, got {self.visibility_secs}")
        if self.max_extend < 1:
            raise ValueError(f"max_extend must be >= 1, got {self.max_extend}")
        if self.worker_threads < 1:
            raise ValueError(f"worker_threads must be >= 1, got {self.worker_threads}")

        self._stats: dict[str, Any] = {
            "messages_processed": 0,
            "messages_failed": 0,
            "messages_filtered": 0,
        }
        self._stats_lock = threading.Lock()
        self.stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._health_server: HealthServer | None = None

        logger.info(
            "Configured listener: mode=%s wait_time=%s batch_size=%s visibility=%ss threads=%s queue=%s",
            self.mode,
            self.wait_time,
            self.batch_size,
            self.visibility_secs,
            self.worker_threads,
            self.queue_url,
        )

    def start(self):
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

        if self.health_check_port:
            self._health_server = HealthServer(self.health_check_port, self._get_stats)
            self._health_server.start()
            logger.info("Health check listening on port %d", self.health_check_port)

        for i in range(self.worker_threads):
            t = threading.Thread(target=self._loop, daemon=True, name=f"sqs-poller-{i}")
            t.start()
            self._threads.append(t)
        logger.info("Spawned %d poller thread(s).", self.worker_threads)

    def join(self):
        for t in self._threads:
            while t.is_alive() and not self.stop_event.is_set():
                t.join(timeout=0.2)
        if self._health_server:
            self._health_server.stop()

    def _handle_stop(self, *_):
        logger.info("Stop signal received; draining…")
        self.stop_event.set()

    def _get_stats(self) -> dict[str, Any]:
        uptime = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        with self._stats_lock:
            return {
                "queue_url": self.queue_url,
                "mode": self.mode,
                "worker_threads": self.worker_threads,
                "uptime_seconds": round(uptime, 1),
                **self._stats,
            }

    def _invoke(self, *args: Any) -> Any:
        if self._is_async:
            return asyncio.run(self.handler(*args))
        return self.handler(*args)

    def _call_hook(self, hook: Callable | None, *args: Any) -> None:
        if hook is None:
            return
        try:
            hook(*args)
        except Exception as e:
            logger.warning("[hook] error: %s", e)

    def _backoff_secs(self, msg: SqsMessage) -> int:
        receive_count = int(msg.attributes.get("ApproximateReceiveCount", 1))
        return int(
            min(self.retry_backoff_base * (2 ** (receive_count - 1)), self.retry_backoff_max)
        )

    def _apply_backoff(self, failed_rhs: Iterable[str], msg_map: dict[str, SqsMessage]) -> None:
        for rh in failed_rhs:
            msg = msg_map.get(rh)
            if not msg:
                continue
            delay = self._backoff_secs(msg)
            try:
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=rh,
                    VisibilityTimeout=delay,
                )
                logger.debug("[backoff] RH=%s delayed %ds", rh[:12], delay)
            except Exception as e:
                logger.warning("[backoff] failed for RH=%s: %s", rh[:12], e)

    def _reset_visibility(self, msgs: list[SqsMessage]) -> None:
        """Make filtered messages immediately visible for other consumers."""
        for m in msgs:
            with contextlib.suppress(Exception):
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=m.receipt_handle,
                    VisibilityTimeout=0,
                )

    def _recv(self) -> list[SqsMessage]:
        resp = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=self.batch_size,
            WaitTimeSeconds=self.wait_time,
            VisibilityTimeout=self.visibility_secs,
            AttributeNames=["ApproximateReceiveCount", "SentTimestamp"],
            MessageAttributeNames=["All"],
        )
        msgs = resp.get("Messages", [])
        if msgs:
            logger.debug("Received %d message(s).", len(msgs))
        return [
            SqsMessage(
                message_id=m["MessageId"],
                receipt_handle=m["ReceiptHandle"],
                body=m["Body"],
                attributes=dict(m.get("Attributes", {})),  # type: ignore[arg-type]
                md=dict(m),  # type: ignore[arg-type]
            )
            for m in msgs
        ]

    def _delete_batch(self, receipt_handles: Iterable[str]) -> None:
        handles = list(receipt_handles)
        if not handles:
            return
        total = 0
        for i in range(0, len(handles), 10):
            chunk = handles[i : i + 10]
            try:
                self.sqs.delete_message_batch(
                    QueueUrl=self.queue_url,
                    Entries=[{"Id": str(j), "ReceiptHandle": rh} for j, rh in enumerate(chunk)],
                )
                total += len(chunk)
            except Exception as e:
                logger.error("[delete] batch delete failed: %s", e)
        if total:
            logger.info("Deleted %d message(s).", total)

    def _stop_extenders(self, extenders: dict[str, tuple[threading.Event, Any]]) -> None:
        for msg_stop, _ in extenders.values():
            msg_stop.set()
        for _, t in extenders.values():
            t.join(timeout=0.2)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                batch = self._recv()
                if not batch:
                    time.sleep(random.random() * self.idle_sleep_max)
                    continue

                # --- Filter ---
                if self.filter_fn is not None:
                    passing = []
                    filtered = []
                    for m in batch:
                        try:
                            (passing if self.filter_fn(m) else filtered).append(m)
                        except Exception as e:
                            logger.warning("[filter] error for %s: %s", m.message_id, e)
                            filtered.append(m)
                    if filtered:
                        self._reset_visibility(filtered)
                        with self._stats_lock:
                            self._stats["messages_filtered"] += len(filtered)
                    batch = passing

                if not batch:
                    continue

                # --- Visibility extenders ---
                extenders: dict[str, tuple[threading.Event, VisibilityExtender]] = {}
                for m in batch:
                    msg_stop = threading.Event()
                    ext = VisibilityExtender(
                        self.sqs,
                        self.queue_url,
                        m.receipt_handle,
                        self.visibility_secs,
                        self.max_extend,
                        self.stop_event,
                        msg_stop,
                    )
                    ext.start()
                    extenders[m.receipt_handle] = (msg_stop, ext)

                msg_map = {m.receipt_handle: m for m in batch}

                try:
                    if self.mode == "batch":
                        try:
                            result = self._invoke(batch)
                            if not isinstance(result, BatchResult):
                                raise TypeError("Batch handler must return BatchResult")
                        except Exception as e:
                            logger.error(
                                "[handler] batch error: %s",
                                e,
                                exc_info=True,
                                extra={"message_ids": [m.message_id for m in batch]},
                            )
                            self._stop_extenders(extenders)
                            if self.retry_backoff:
                                self._apply_backoff([m.receipt_handle for m in batch], msg_map)
                            for m in batch:
                                self._call_hook(self.on_failure, m, e)
                            with self._stats_lock:
                                self._stats["messages_failed"] += len(batch)
                            continue

                        failed = set(result.failed_receipt_handles)
                        ok_rhs = [m.receipt_handle for m in batch if m.receipt_handle not in failed]

                        self._stop_extenders(extenders)

                        if failed:
                            logger.warning(
                                "Batch processed with failures: ok=%d failed=%d",
                                len(ok_rhs),
                                len(failed),
                            )
                            if self.retry_backoff:
                                self._apply_backoff(failed, msg_map)
                            for rh in failed:
                                self._call_hook(self.on_failure, msg_map[rh], None)
                        else:
                            logger.info("Batch processed successfully: ok=%d", len(ok_rhs))

                        for rh in ok_rhs:
                            self._call_hook(self.on_success, msg_map[rh])

                        with self._stats_lock:
                            self._stats["messages_processed"] += len(ok_rhs)
                            self._stats["messages_failed"] += len(failed)

                        self._delete_batch(ok_rhs)

                    else:
                        ok_rhs: list[str] = []
                        failed_count = 0
                        for m in batch:
                            exc: Exception | None = None
                            try:
                                success = bool(self._invoke(m))
                            except Exception as e:
                                exc = e
                                success = False
                                logger.error(
                                    "[handler] error: %s",
                                    e,
                                    exc_info=True,
                                    extra={"message_id": m.message_id},
                                )

                            if success:
                                ok_rhs.append(m.receipt_handle)
                                self._call_hook(self.on_success, m)
                            else:
                                failed_count += 1
                                if self.retry_backoff:
                                    self._apply_backoff([m.receipt_handle], msg_map)
                                self._call_hook(self.on_failure, m, exc)

                        self._stop_extenders(extenders)

                        with self._stats_lock:
                            self._stats["messages_processed"] += len(ok_rhs)
                            self._stats["messages_failed"] += failed_count

                        logger.info(
                            "Per-message processed: ok=%d failed=%d", len(ok_rhs), failed_count
                        )
                        self._delete_batch(ok_rhs)

                finally:
                    pass

            except Exception as e:
                logger.error("[loop] error: %s", e, exc_info=True)
                time.sleep(1)
