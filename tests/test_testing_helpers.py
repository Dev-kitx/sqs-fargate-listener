from __future__ import annotations

from src.sqs_fargate_listener.testing import FakeSQSQueue, RunResult
from src.sqs_fargate_listener.types import BatchResult

# ========================
# FakeSQSQueue.send
# ========================


def test_send_dict_body_serializes_to_json():
    q = FakeSQSQueue()
    mid = q.send({"key": "val"})
    assert mid == "fake-msg-1"
    assert q.messages[0].body == '{"key": "val"}'


def test_send_string_body_stays_as_is():
    q = FakeSQSQueue()
    q.send("raw string")
    assert q.messages[0].body == "raw string"


def test_send_increments_counter():
    q = FakeSQSQueue()
    q.send("a")
    q.send("b")
    assert q.messages[0].message_id == "fake-msg-1"
    assert q.messages[1].message_id == "fake-msg-2"


def test_send_returns_unique_ids():
    q = FakeSQSQueue()
    id1 = q.send("x")
    id2 = q.send("y")
    assert id1 != id2


def test_pending_count_and_deleted_count():
    q = FakeSQSQueue()
    q.send("a")
    q.send("b")
    assert q.pending_count == 2
    assert q.deleted_count == 0


def test_messages_returns_copy():
    q = FakeSQSQueue()
    q.send("a")
    msgs = q.messages
    msgs.clear()
    assert len(q.messages) == 1  # original unaffected


def test_reset_clears_everything():
    q = FakeSQSQueue()
    q.send("a")
    q.run_handler(lambda batch: BatchResult(failed_receipt_handles=[]), mode="batch")
    q.reset()
    assert q.pending_count == 0
    assert q.deleted_count == 0
    assert q.messages == []


# ========================
# run_handler — batch mode
# ========================


def test_run_handler_batch_all_success():
    q = FakeSQSQueue()
    q.send({"n": 1})
    q.send({"n": 2})
    result = q.run_handler(lambda batch: BatchResult(failed_receipt_handles=[]), mode="batch")
    assert result.processed == 2
    assert result.failed == 0
    assert q.deleted_count == 2


def test_run_handler_batch_partial_failure():
    q = FakeSQSQueue()
    q.send("a")
    q.send("b")
    rh_b = q.messages[1].receipt_handle

    result = q.run_handler(lambda batch: BatchResult(failed_receipt_handles=[rh_b]), mode="batch")
    assert result.processed == 1
    assert result.failed == 1
    assert q.deleted_count == 1


def test_run_handler_batch_handler_exception():
    q = FakeSQSQueue()
    q.send("x")

    def boom(batch):
        raise RuntimeError("oops")

    result = q.run_handler(boom, mode="batch")
    assert result.failed == 1
    assert result.processed == 0
    assert q.deleted_count == 0


def test_run_handler_batch_wrong_return_type():
    q = FakeSQSQueue()
    q.send("x")
    result = q.run_handler(lambda batch: "not-a-batch-result", mode="batch")
    assert result.failed == 1


def test_run_handler_empty_queue_returns_zero_result():
    q = FakeSQSQueue()
    result = q.run_handler(lambda batch: BatchResult(failed_receipt_handles=[]), mode="batch")
    assert result.processed == 0
    assert result.failed == 0


# ========================
# run_handler — per_message mode
# ========================


def test_run_handler_per_message_all_success():
    q = FakeSQSQueue()
    q.send("a")
    q.send("b")
    result = q.run_handler(lambda msg: True, mode="per_message")
    assert result.processed == 2
    assert result.failed == 0
    assert q.deleted_count == 2


def test_run_handler_per_message_all_failure():
    q = FakeSQSQueue()
    q.send("x")
    result = q.run_handler(lambda msg: False, mode="per_message")
    assert result.processed == 0
    assert result.failed == 1
    assert q.deleted_count == 0


def test_run_handler_per_message_exception_counts_as_failure():
    q = FakeSQSQueue()
    q.send("x")

    def boom(msg):
        raise ValueError("nope")

    result = q.run_handler(boom, mode="per_message")
    assert result.failed == 1
    assert result.processed == 0


def test_run_handler_per_message_mixed():
    q = FakeSQSQueue()
    q.send("ok")
    q.send("fail")

    def handler(msg):
        return msg.body == "ok"

    result = q.run_handler(handler, mode="per_message")
    assert result.processed == 1
    assert result.failed == 1


# ========================
# run_handler — filter_fn
# ========================


def test_run_handler_filter_reduces_messages():
    q = FakeSQSQueue()
    q.send("keep")
    q.send("drop")

    result = q.run_handler(
        lambda batch: BatchResult(failed_receipt_handles=[]),
        mode="batch",
        filter_fn=lambda m: m.body == "keep",
    )
    assert result.processed == 1
    assert result.filtered == 1


def test_run_handler_filter_all_out_returns_zero():
    q = FakeSQSQueue()
    q.send("x")
    result = q.run_handler(
        lambda batch: BatchResult(failed_receipt_handles=[]),
        mode="batch",
        filter_fn=lambda m: False,
    )
    assert result.processed == 0
    assert result.filtered == 1


# ========================
# Async handlers
# ========================


def test_run_handler_async_batch():
    async def async_handler(batch):
        return BatchResult(failed_receipt_handles=[])

    q = FakeSQSQueue()
    q.send("x")
    result = q.run_handler(async_handler, mode="batch")
    assert result.processed == 1
    assert result.failed == 0


def test_run_handler_async_per_message():
    async def async_handler(msg):
        return True

    q = FakeSQSQueue()
    q.send("x")
    result = q.run_handler(async_handler, mode="per_message")
    assert result.processed == 1


# ========================
# RunResult defaults
# ========================


def test_run_result_defaults():
    r = RunResult()
    assert r.processed == 0
    assert r.failed == 0
    assert r.filtered == 0


# ========================
# pytest fixture
# ========================


def test_fake_sqs_queue_fixture(fake_sqs_queue):
    assert isinstance(fake_sqs_queue, FakeSQSQueue)
    assert fake_sqs_queue.pending_count == 0
