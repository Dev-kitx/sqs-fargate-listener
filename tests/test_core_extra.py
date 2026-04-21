"""
Additional coverage for core.py branches not covered by test_core.py:
- _get_stats
- _invoke (async path)
- _call_hook (with and without errors)
- _backoff_secs / _apply_backoff
- _reset_visibility
- filter_fn in _loop (passing, filtered, filter exception, all-filtered)
- hooks in _loop (on_success, on_failure, hook exceptions)
- retry_backoff in batch and per_message modes
- health_check_port branch in start()
"""

from __future__ import annotations

import threading

import src.sqs_fargate_listener.core as core
from src.sqs_fargate_listener.types import BatchResult, SqsMessage

Q = "http://localhost:4566/000000000000/test-queue"


# ---- helpers ----------------------------------------------------------------


def make_raw_message(mid="m1", rh="rh1", body='{"ok":true}', receive_count="1"):
    return {
        "MessageId": mid,
        "ReceiptHandle": rh,
        "Body": body,
        "Attributes": {"ApproximateReceiveCount": receive_count},
        "MessageAttributes": {},
    }


class FakeSQS:
    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.delete_calls = []
        self.change_vis_calls = []

    def receive_message(self, **kwargs):
        if self.batches:
            return {"Messages": self.batches.pop(0)}
        return {}

    def delete_message_batch(self, QueueUrl, Entries):
        self.delete_calls.append((QueueUrl, list(Entries)))
        return {"Successful": [{"Id": e["Id"]} for e in Entries]}

    def change_message_visibility(self, **kwargs):
        self.change_vis_calls.append(kwargs)
        return {}


class FakeVisibilityExt(threading.Thread):
    def __init__(
        self, sqs, queue_url, receipt_handle, vis_secs, max_extend, stop_event, msg_stop_event
    ):
        super().__init__(daemon=True)
        self.rh = receipt_handle
        self.msg_stop_event = msg_stop_event
        self.started = False
        self.joined = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.joined = True


def _make_engine(monkeypatch, fake_sqs, **kwargs):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)
    monkeypatch.setattr(core, "VisibilityExtender", lambda *a, **k: FakeVisibilityExt(*a, **k))
    defaults = {"queue_url": Q, "handler": lambda _: None, "mode": "batch"}
    defaults.update(kwargs)
    eng = core.SqsListenerEngine(**defaults)
    eng.idle_sleep_max = 0.0
    return eng


# ---- _get_stats -------------------------------------------------------------


def test_get_stats_returns_expected_shape(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    stats = eng._get_stats()
    assert stats["queue_url"] == Q
    assert stats["mode"] == "batch"
    assert "uptime_seconds" in stats
    assert "messages_processed" in stats
    assert "messages_failed" in stats
    assert "messages_filtered" in stats


# ---- _invoke (async) --------------------------------------------------------


def test_invoke_async_handler(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())

    async def async_handler(msg):
        return True

    eng = core.SqsListenerEngine(queue_url=Q, handler=async_handler, mode="per_message")
    result = eng._invoke("dummy")
    assert result is True


# ---- _call_hook -------------------------------------------------------------


def test_call_hook_calls_hook(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    called_with = []
    eng._call_hook(lambda x: called_with.append(x), "arg1")
    assert called_with == ["arg1"]


def test_call_hook_none_is_noop(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    eng._call_hook(None, "ignored")  # should not raise


def test_call_hook_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)

    def bad_hook(*_):
        raise RuntimeError("hook failed")

    eng._call_hook(bad_hook, "x")  # should not raise


# ---- _backoff_secs ----------------------------------------------------------


def test_backoff_secs_exponential(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())
    eng = core.SqsListenerEngine(
        queue_url=Q,
        handler=lambda _: None,
        retry_backoff_base=30,
        retry_backoff_max=600,
    )
    msg1 = SqsMessage("id", "rh", "body", {"ApproximateReceiveCount": "1"}, {})
    msg2 = SqsMessage("id", "rh", "body", {"ApproximateReceiveCount": "2"}, {})
    msg5 = SqsMessage("id", "rh", "body", {"ApproximateReceiveCount": "5"}, {})
    assert eng._backoff_secs(msg1) == 30
    assert eng._backoff_secs(msg2) == 60
    assert eng._backoff_secs(msg5) == 480  # 30 * 16 = 480 < 600


def test_backoff_secs_capped_at_max(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())
    eng = core.SqsListenerEngine(
        queue_url=Q,
        handler=lambda _: None,
        retry_backoff_base=30,
        retry_backoff_max=100,
    )
    msg = SqsMessage("id", "rh", "body", {"ApproximateReceiveCount": "10"}, {})
    assert eng._backoff_secs(msg) == 100


# ---- _apply_backoff ---------------------------------------------------------


def test_apply_backoff_calls_change_visibility(monkeypatch):
    fake_sqs = FakeSQS()
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)
    eng = core.SqsListenerEngine(
        queue_url=Q,
        handler=lambda _: None,
        retry_backoff=True,
        retry_backoff_base=30,
        retry_backoff_max=600,
    )
    msg = SqsMessage("id", "rh1", "body", {"ApproximateReceiveCount": "1"}, {})
    eng._apply_backoff(["rh1"], {"rh1": msg})
    assert len(fake_sqs.change_vis_calls) == 1
    assert fake_sqs.change_vis_calls[0]["VisibilityTimeout"] == 30


def test_apply_backoff_skips_unknown_rh(monkeypatch):
    fake_sqs = FakeSQS()
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    eng._apply_backoff(["rh-unknown"], {})
    assert fake_sqs.change_vis_calls == []


def test_apply_backoff_handles_sqs_error(monkeypatch):
    class ErrorSQS(FakeSQS):
        def change_message_visibility(self, **kwargs):
            raise RuntimeError("SQS down")

    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: ErrorSQS())
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    msg = SqsMessage("id", "rh1", "body", {"ApproximateReceiveCount": "1"}, {})
    eng._apply_backoff(["rh1"], {"rh1": msg})  # should not raise


# ---- _reset_visibility ------------------------------------------------------


def test_reset_visibility_calls_change_visibility(monkeypatch):
    fake_sqs = FakeSQS()
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    msg = SqsMessage("id", "rh1", "body", {}, {})
    eng._reset_visibility([msg])
    assert len(fake_sqs.change_vis_calls) == 1
    assert fake_sqs.change_vis_calls[0]["VisibilityTimeout"] == 0


def test_reset_visibility_suppresses_exceptions(monkeypatch):
    class ErrorSQS(FakeSQS):
        def change_message_visibility(self, **kwargs):
            raise RuntimeError("SQS error")

    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: ErrorSQS())
    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    msg = SqsMessage("id", "rh1", "body", {}, {})
    eng._reset_visibility([msg])  # should not raise


# ---- filter_fn in _loop -----------------------------------------------------


def test_loop_filter_fn_passes_and_filters(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1", body="keep"),
        make_raw_message(mid="m2", rh="rh2", body="drop"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="batch",
        filter_fn=lambda m: m.body == "keep",
        handler=lambda batch: eng.stop_event.set() or BatchResult(failed_receipt_handles=[]),
    )

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    # Only the passing message was deleted
    all_deleted = [e["ReceiptHandle"] for (_, entries) in fake_sqs.delete_calls for e in entries]
    assert all_deleted == ["rh1"]
    # Filtered message had visibility reset to 0
    reset_calls = [c for c in fake_sqs.change_vis_calls if c["VisibilityTimeout"] == 0]
    assert any(c["ReceiptHandle"] == "rh2" for c in reset_calls)
    assert eng._stats["messages_filtered"] == 1


def test_loop_filter_fn_all_filtered_continues(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    called = [False]

    def handler(batch):
        called[0] = True
        return BatchResult(failed_receipt_handles=[])

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="batch",
        filter_fn=lambda m: False,
        handler=handler,
    )
    eng.stop_event.set()  # stop after first poll

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert not called[0]  # handler never invoked
    assert fake_sqs.delete_calls == []


def test_loop_filter_fn_exception_treats_as_filtered(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    eng_holder = [None]

    def bad_filter(m):
        eng_holder[0].stop_event.set()
        raise RuntimeError("filter exploded")

    eng = _make_engine(monkeypatch, fake_sqs, filter_fn=bad_filter)
    eng_holder[0] = eng

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert fake_sqs.delete_calls == []
    assert eng._stats["messages_filtered"] == 1


# ---- hooks in _loop ---------------------------------------------------------


def test_loop_batch_on_success_hook_called(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    success_calls = []

    def handler(batch):
        eng.stop_event.set()
        return BatchResult(failed_receipt_handles=[])

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="batch",
        handler=handler,
        on_success=lambda m: success_calls.append(m.receipt_handle),
    )

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert success_calls == ["rh1"]


def test_loop_batch_on_failure_hook_called_for_partial(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1"),
        make_raw_message(mid="m2", rh="rh2"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    failure_calls = []

    def handler(batch):
        eng.stop_event.set()
        return BatchResult(failed_receipt_handles=["rh2"])

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="batch",
        handler=handler,
        on_failure=lambda m, exc: failure_calls.append(m.receipt_handle),
    )

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert failure_calls == ["rh2"]


def test_loop_batch_on_failure_hook_called_on_exception(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    failure_calls = []

    def handler(batch):
        eng.stop_event.set()
        raise RuntimeError("boom")

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="batch",
        handler=handler,
        on_failure=lambda m, exc: failure_calls.append(m.receipt_handle),
    )

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert failure_calls == ["rh1"]


def test_loop_per_message_on_success_and_on_failure_hooks(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1"),
        make_raw_message(mid="m2", rh="rh2"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    success_calls = []
    failure_calls = []
    eng_holder = [None]

    def handler(msg):
        if msg.receipt_handle == "rh1":
            return True
        eng_holder[0].stop_event.set()
        return False

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="per_message",
        handler=handler,
        on_success=lambda m: success_calls.append(m.receipt_handle),
        on_failure=lambda m, exc: failure_calls.append(m.receipt_handle),
    )
    eng_holder[0] = eng

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert success_calls == ["rh1"]
    assert failure_calls == ["rh2"]


# ---- retry_backoff in _loop -------------------------------------------------


def test_loop_batch_retry_backoff_called_on_exception(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])

    def handler(batch):
        eng.stop_event.set()
        raise RuntimeError("fail")

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="batch",
        handler=handler,
        retry_backoff=True,
        retry_backoff_base=30,
    )

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    backoff_calls = [c for c in fake_sqs.change_vis_calls if c.get("VisibilityTimeout", 0) > 0]
    assert len(backoff_calls) >= 1


def test_loop_per_message_retry_backoff_called_on_failure(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    eng_holder = [None]

    def handler(msg):
        eng_holder[0].stop_event.set()
        return False

    eng = _make_engine(
        monkeypatch,
        fake_sqs,
        mode="per_message",
        handler=handler,
        retry_backoff=True,
        retry_backoff_base=30,
    )
    eng_holder[0] = eng

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    backoff_calls = [c for c in fake_sqs.change_vis_calls if c.get("VisibilityTimeout", 0) > 0]
    assert len(backoff_calls) >= 1


# ---- health_check_port in start() ------------------------------------------


def test_start_with_health_check_port(monkeypatch):
    import socket

    monkeypatch.setattr(core.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())

    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    eng = core.SqsListenerEngine(
        queue_url=Q,
        handler=lambda _: None,
        health_check_port=port,
        worker_threads=1,
    )
    eng.start()
    try:
        assert eng._health_server is not None
    finally:
        eng.stop_event.set()
        eng.join()


# ---- stats tracking ---------------------------------------------------------


def test_stats_messages_processed_and_failed_tracked(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1"),
        make_raw_message(mid="m2", rh="rh2"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs])

    def handler(batch):
        eng.stop_event.set()
        return BatchResult(failed_receipt_handles=["rh2"])

    eng = _make_engine(monkeypatch, fake_sqs, mode="batch", handler=handler)

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert eng._stats["messages_processed"] == 1
    assert eng._stats["messages_failed"] == 1
