# tests/test_core.py
# ---------------------------------------------------------------------------
# Pytest unit tests for sqs_fargate_listener.core:
# - _env_int / _env_float helpers
# - VisibilityExtender behavior (stop early; heartbeat loop)
# - SqsListenerEngine: start/join, _recv mapping, _delete_batch chunking
# - _loop logic for batch and per_message modes with a fake SQS client
# - Input validation on SqsListenerEngine.__init__
# ---------------------------------------------------------------------------

import threading
import time

import pytest

import src.sqs_fargate_listener.core as core
from src.sqs_fargate_listener.types import SqsMessage, BatchResult

Q = "http://localhost:4566/000000000000/test-queue"
Q1 = "http://localhost:4566/000000000000/q1"
Q2 = "http://localhost:4566/000000000000/q2"


# =========================
# Helpers & Test Doubles
# =========================

def make_raw_message(
    mid="m1",
    rh="rh1",
    body='{"ok":true}',
    attributes=None,
    msg_attributes=None,
):
    return {
        "MessageId": mid,
        "ReceiptHandle": rh,
        "Body": body,
        "Attributes": attributes or {"ApproximateReceiveCount": "1"},
        "MessageAttributes": msg_attributes or {},
    }


class FakeSQS:
    """
    Minimal fake SQS client to drive the engine without AWS.
    batches: list of lists; each inner list is the raw 'Messages' for that poll.
    """
    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.delete_calls = []
        self.change_vis_calls = []
        self.receive_calls = 0

    def receive_message(self, **kwargs):
        self.receive_calls += 1
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
    def __init__(self, sqs, queue_url, receipt_handle, vis_secs, max_extend, stop_event, msg_stop_event):
        super().__init__(daemon=True)
        self.sqs = sqs
        self.queue_url = queue_url
        self.rh = receipt_handle
        self.vis_secs = vis_secs
        self.max_extend = max_extend
        self.stop_event = stop_event
        self.msg_stop_event = msg_stop_event
        self.started = False
        self.joined = False

    def start(self):
        self.started = True
        return None

    def join(self, timeout=None):
        self.joined = True
        return None


# =========================
# Tests: env helpers
# =========================

def test_env_helpers(monkeypatch):
    monkeypatch.setenv("WAIT_TIME", "15")
    monkeypatch.setenv("IDLE_SLEEP_MAX", "1.25")

    assert core._env_int("WAIT_TIME", 20) == 15
    assert core._env_int("MISSING_INT", 7) == 7
    assert core._env_float("IDLE_SLEEP_MAX", 2.0) == 1.25
    assert core._env_float("MISSING_FLOAT", 3.5) == 3.5


# =========================
# Tests: VisibilityExtender
# =========================

def test_visibility_extender_exits_immediately_if_msg_already_stopped():
    fake_sqs = FakeSQS()
    stop = threading.Event()

    class FastEvent(threading.Event):
        def wait(self, timeout=None):
            return True

    msg_stop = FastEvent()
    msg_stop.set()

    ve = core.VisibilityExtender(
        fake_sqs, Q, "rh", vis_secs=2, max_extend=10, stop_event=stop, msg_stop_event=msg_stop
    )
    ve.start()
    ve.join(timeout=0.2)

    assert fake_sqs.change_vis_calls == []


def test_visibility_extender_heartbeats_until_max_extend():
    fake_sqs = FakeSQS()
    stop = threading.Event()

    class FastEvent(threading.Event):
        def wait(self, timeout=None):
            return False

    msg_stop = FastEvent()

    ve = core.VisibilityExtender(
        fake_sqs, Q, "rh", vis_secs=2, max_extend=3, stop_event=stop, msg_stop_event=msg_stop
    )
    ve.start()
    ve.join(timeout=0.5)

    assert len(fake_sqs.change_vis_calls) == 3
    for call in fake_sqs.change_vis_calls:
        assert call["QueueUrl"] == Q
        assert call["ReceiptHandle"] == "rh"
        assert call["VisibilityTimeout"] == 2


def test_visibility_extender_stops_on_global_stop():
    fake_sqs = FakeSQS()
    stop = threading.Event()
    stop.set()

    class FastEvent(threading.Event):
        def wait(self, timeout=None):
            return False

    msg_stop = FastEvent()

    ve = core.VisibilityExtender(
        fake_sqs, Q, "rh", vis_secs=2, max_extend=100, stop_event=stop, msg_stop_event=msg_stop
    )
    ve.start()
    ve.join(timeout=0.2)

    assert fake_sqs.change_vis_calls == []


def test_visibility_extender_handles_sqs_exception_gracefully():
    class ErrorSQS:
        def change_message_visibility(self, **kwargs):
            raise RuntimeError("SQS unavailable")

    stop = threading.Event()

    class FastEvent(threading.Event):
        def wait(self, timeout=None):
            return False

    msg_stop = FastEvent()

    ve = core.VisibilityExtender(
        ErrorSQS(), Q, "rh", vis_secs=2, max_extend=10, stop_event=stop, msg_stop_event=msg_stop
    )
    ve.start()
    ve.join(timeout=0.5)
    # Should exit cleanly without crashing


# =========================
# Tests: Engine basics
# =========================

def test_engine_start_spawns_threads_and_sets_handlers(monkeypatch):
    monkeypatch.setattr(core.signal, "signal", lambda *a, **k: None)
    fake_sqs = FakeSQS()
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    eng = core.SqsListenerEngine(
        queue_url=Q,
        handler=lambda _: None,
        mode="batch",
        worker_threads=2,
    )
    eng.start()
    try:
        assert len(eng._threads) == 2
        for t in eng._threads:
            assert t.is_alive()
    finally:
        eng.stop_event.set()
        eng.join()


def test__recv_maps_messages(monkeypatch):
    fake_batch = [make_raw_message(mid="mX", rh="rhX", body='{"k": "v"}')]
    fake_sqs = FakeSQS(batches=[fake_batch])
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    msgs = eng._recv()
    assert len(msgs) == 1
    m = msgs[0]
    assert isinstance(m, SqsMessage)
    assert m.message_id == "mX"
    assert m.receipt_handle == "rhX"
    assert m.json == {"k": "v"}


def test__recv_returns_empty_on_no_messages(monkeypatch):
    fake_sqs = FakeSQS(batches=[])
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    msgs = eng._recv()
    assert msgs == []


def test__delete_batch_chunks_in_tens(monkeypatch):
    fake_sqs = FakeSQS()
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    handles = [f"rh{i}" for i in range(23)]
    eng._delete_batch(handles)

    sizes = [len(entries) for (_q, entries) in fake_sqs.delete_calls]
    assert sizes == [10, 10, 3]
    assert all(q == Q for (q, _entries) in fake_sqs.delete_calls)


def test__delete_batch_noop_on_empty(monkeypatch):
    fake_sqs = FakeSQS()
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    eng._delete_batch([])
    assert fake_sqs.delete_calls == []


def test__delete_batch_handles_sqs_error_gracefully(monkeypatch):
    class ErrorSQS(FakeSQS):
        def delete_message_batch(self, **kwargs):
            raise RuntimeError("delete failed")

    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: ErrorSQS())

    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    eng._delete_batch(["rh1"])  # should not raise


# =========================
# Tests: Engine _loop logic
# =========================

def test_loop_batch_mode_success_deletes_ok_and_stops_extenders(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1"),
        make_raw_message(mid="m2", rh="rh2"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs, []])
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    created_exts = []
    def _fake_ext_factory(*args, **kwargs):
        ext = FakeVisibilityExt(*args, **kwargs)
        created_exts.append(ext)
        return ext
    monkeypatch.setattr(core, "VisibilityExtender", _fake_ext_factory)

    def handler(batch):
        nonlocal_eng.stop_event.set()
        return BatchResult(failed_receipt_handles=["rh2"])

    nonlocal_eng = core.SqsListenerEngine(queue_url=Q, handler=handler, mode="batch")
    nonlocal_eng.idle_sleep_max = 0.0

    t = threading.Thread(target=nonlocal_eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert len(fake_sqs.delete_calls) == 1
    (_q, entries) = fake_sqs.delete_calls[0]
    rhs = sorted(e["ReceiptHandle"] for e in entries)
    assert rhs == ["rh1"]

    assert len(created_exts) == 2
    assert all(ext.started for ext in created_exts)
    assert all(ext.joined for ext in created_exts)


def test_loop_batch_mode_all_success_deletes_all(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1"),
        make_raw_message(mid="m2", rh="rh2"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)
    monkeypatch.setattr(core, "VisibilityExtender",
                        lambda *a, **k: FakeVisibilityExt(*a, **k))

    def handler(batch):
        nonlocal_eng.stop_event.set()
        return BatchResult(failed_receipt_handles=[])

    nonlocal_eng = core.SqsListenerEngine(queue_url=Q, handler=handler, mode="batch")
    nonlocal_eng.idle_sleep_max = 0.0

    t = threading.Thread(target=nonlocal_eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    all_deleted = [e["ReceiptHandle"] for (_, entries) in fake_sqs.delete_calls for e in entries]
    assert sorted(all_deleted) == ["rh1", "rh2"]


def test_loop_batch_mode_handler_exception_deletes_nothing(monkeypatch):
    fake_msgs = [make_raw_message(mid="m1", rh="rh1")]
    fake_sqs = FakeSQS(batches=[fake_msgs])
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)
    monkeypatch.setattr(core, "VisibilityExtender",
                        lambda *a, **k: FakeVisibilityExt(*a, **k))

    call_count = [0]
    def handler(batch):
        call_count[0] += 1
        nonlocal_eng.stop_event.set()
        raise RuntimeError("handler exploded")

    nonlocal_eng = core.SqsListenerEngine(queue_url=Q, handler=handler, mode="batch")
    nonlocal_eng.idle_sleep_max = 0.0

    t = threading.Thread(target=nonlocal_eng._loop, daemon=True)
    t.start()
    t.join(timeout=1.0)

    assert fake_sqs.delete_calls == []
    assert call_count[0] == 1


def test_loop_per_message_mode_deletes_only_truthy_and_handles_exceptions(monkeypatch):
    fake_msgs = [
        make_raw_message(mid="m1", rh="rh1"),
        make_raw_message(mid="m2", rh="rh2"),
        make_raw_message(mid="m3", rh="rh3"),
    ]
    fake_sqs = FakeSQS(batches=[fake_msgs, []])
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: fake_sqs)

    created_exts = []
    monkeypatch.setattr(core, "VisibilityExtender",
                        lambda *a, **k: created_exts.append(FakeVisibilityExt(*a, **k)) or created_exts[-1])

    def handler(msg):
        if msg.receipt_handle == "rh1":
            return True
        if msg.receipt_handle == "rh2":
            return False
        raise RuntimeError("boom")

    eng = core.SqsListenerEngine(queue_url=Q, handler=handler, mode="per_message")
    eng.idle_sleep_max = 0.0

    def stop_after():
        time.sleep(0.05)
        eng.stop_event.set()

    threading.Thread(target=stop_after, daemon=True).start()

    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    t.join(timeout=2.0)

    assert len(fake_sqs.delete_calls) == 1
    (_q, entries) = fake_sqs.delete_calls[0]
    assert sorted(e["ReceiptHandle"] for e in entries) == ["rh1"]

    assert len(created_exts) == 3
    assert all(ext.started and ext.joined for ext in created_exts)


def test_handle_stop_sets_flag(monkeypatch):
    monkeypatch.setattr(core.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: FakeSQS())

    eng = core.SqsListenerEngine(queue_url=Q, handler=lambda _: None)
    eng._handle_stop()
    assert eng.stop_event.is_set()
