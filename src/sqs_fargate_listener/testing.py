from __future__ import annotations
import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .types import BatchResult, SqsMessage


@dataclass
class RunResult:
    """Result of running a handler against a FakeSQSQueue."""
    processed: int = 0
    failed: int = 0
    filtered: int = 0


class FakeSQSQueue:
    """
    In-memory SQS queue for unit testing handlers without AWS or mocking boto3.

    Usage (batch mode):
        queue = FakeSQSQueue()
        queue.send({"order_id": "123"})
        queue.send({"order_id": "456"})

        result = queue.run_handler(my_batch_handler, mode="batch")
        assert result.processed == 2
        assert result.failed == 0

    Usage (per_message mode):
        queue = FakeSQSQueue()
        queue.send({"event": "signup"})

        result = queue.run_handler(my_handler, mode="per_message")
        assert result.processed == 1

    Supports async handlers transparently:
        async def my_handler(msg): ...
        result = queue.run_handler(my_handler, mode="per_message")
    """

    def __init__(self, queue_url: str = "http://localhost:4566/000000000000/test-queue"):
        self.queue_url = queue_url
        self._messages: List[SqsMessage] = []
        self._deleted: List[str] = []
        self._counter = 0

    def send(self, body: Any, attributes: Optional[dict] = None) -> str:
        """Enqueue a message. body can be a dict (auto-serialized) or a raw string."""
        self._counter += 1
        mid = f"fake-msg-{self._counter}"
        rh = f"fake-rh-{self._counter}"
        if not isinstance(body, str):
            body = json.dumps(body)
        self._messages.append(SqsMessage(
            message_id=mid,
            receipt_handle=rh,
            body=body,
            attributes={"ApproximateReceiveCount": "1"},
            md={"MessageAttributes": attributes or {}},
        ))
        return mid

    @property
    def deleted_count(self) -> int:
        return len(self._deleted)

    @property
    def pending_count(self) -> int:
        return len(self._messages) - len(self._deleted)

    @property
    def messages(self) -> List[SqsMessage]:
        return list(self._messages)

    def reset(self) -> None:
        """Clear all messages and deleted receipts. Useful between sub-tests."""
        self._messages.clear()
        self._deleted.clear()

    def run_handler(
        self,
        handler: Callable,
        mode: str = "batch",
        filter_fn: Optional[Callable[[SqsMessage], bool]] = None,
    ) -> RunResult:
        """
        Run handler against all queued messages and return a RunResult.
        Does not touch any real SQS queue — purely in-memory.
        """
        is_async = inspect.iscoroutinefunction(handler)

        def invoke(*args: Any) -> Any:
            if is_async:
                return asyncio.run(handler(*args))
            return handler(*args)

        result = RunResult()
        messages = list(self._messages)

        if filter_fn is not None:
            passing = [m for m in messages if filter_fn(m)]
            result.filtered = len(messages) - len(passing)
            messages = passing

        if not messages:
            return result

        if mode == "batch":
            try:
                batch_result = invoke(messages)
                if not isinstance(batch_result, BatchResult):
                    raise TypeError("Batch handler must return BatchResult")
                failed = set(batch_result.failed_receipt_handles)
                ok = [m for m in messages if m.receipt_handle not in failed]
                self._deleted.extend(m.receipt_handle for m in ok)
                result.processed = len(ok)
                result.failed = len(failed)
            except Exception:
                result.failed = len(messages)
        else:
            for msg in messages:
                try:
                    if invoke(msg):
                        self._deleted.append(msg.receipt_handle)
                        result.processed += 1
                    else:
                        result.failed += 1
                except Exception:
                    result.failed += 1

        return result


try:
    import pytest

    @pytest.fixture
    def fake_sqs_queue() -> FakeSQSQueue:
        """pytest fixture that provides a fresh FakeSQSQueue for each test."""
        return FakeSQSQueue()

except ImportError:
    pass
